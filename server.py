import asyncio, fnmatch, json, logging, os, re, sys, time, tomllib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from pydantic import Field
from mcp.server.fastmcp import FastMCP
import sqlglot
from sqlglot import exp

logging.basicConfig(stream=sys.stderr)  # stdout is reserved for stdio MCP protocol
log = logging.getLogger("db-grep")

CONFIG = Path(os.environ.get("DB_GREP_CONFIG", os.environ.get("DB_MCP_CONFIG", "~/.config/db-grep/config.toml"))).expanduser()
AUDIT = Path(os.environ.get("AUDIT_LOG", CONFIG.parent / "audit.jsonl")).expanduser()

Konteks = Annotated[str, Field(
    description="Required. Format '<project-name> · <action>', example 'ecommerce · fetch product inventory'")]
DbName = Annotated[str, Field(description="Database connection name. See list_databases.")]

KNOWN_DEFAULTS_KEYS = {"auto_metadata", "max_rows", "timeout_ms"}
KNOWN_DB_KEYS = {
    "type", "dsn", "user", "password", "password_env", "description",
    "schema", "auto_metadata", "auto_tables", "max_rows", "timeout_ms"
}
KNOWN_MONITOR_KEYS = {"port", "approval_timeout_s"}

REF = re.compile(r"\$\{(env|file):([^}|]+)(?:\|(urlenc))?\}")
SHORT = re.compile(r"^(env|file):(.+)$")

DISABLED_DATABASES: dict[str, str] = {}


# ---------- interpolation and secret resolution ----------
def _read(kind: str, arg: str) -> str:
    if kind == "env":
        if arg not in os.environ:
            raise ValueError(f"Environment variable '{arg}' is not set")
        return os.environ[arg]
    p = Path(arg).expanduser()
    if not p.exists():
        raise ValueError(f"Secret file not found: {p}")
    if os.name == "posix" and (p.stat().st_mode & 0o077):
        raise ValueError(f"Permissions on secret file {p} are too loose. Run: chmod 600 {p}")
    return p.read_text(encoding="utf-8").rstrip("\r\n")


def resolve(value: str, shorthand: bool = False, secrets_pool: set[str] | None = None) -> str:
    if not isinstance(value, str):
        return value
    if shorthand:
        if value.startswith("raw:"):
            val = value[4:]
            if secrets_pool is not None and val:
                secrets_pool.add(val)
            return val
        if m := SHORT.match(value):
            val = _read(m[1], m[2].strip())
            if secrets_pool is not None and val:
                secrets_pool.add(val)
            return val

    def sub(m):
        v = _read(m[1], m[2].strip())
        if secrets_pool is not None and v:
            secrets_pool.add(v)
        return quote(v, safe="") if m[3] else v

    out = REF.sub(sub, value.replace("$$", "\0"))
    res = out.replace("\0", "$")
    return res


def has_raw_secret(cfg: dict) -> bool:
    pw = cfg.get("password")
    if pw is not None:
        pw_str = str(pw)
        is_ref = (
            pw_str.startswith("env:")
            or pw_str.startswith("file:")
            or bool(REF.search(pw_str))
        ) and not pw_str.startswith("raw:")
        if not is_ref:
            return True

    dsn = cfg.get("dsn", "")
    if dsn and isinstance(dsn, str):
        m = re.search(r"://[^/@:]+:([^/@]+)@", dsn)
        if m and "${" not in m.group(1):
            return True
        m_ora = re.search(r"^[^/@:]+/([^/@]+)@", dsn)
        if m_ora and "${" not in m_ora.group(1):
            return True
    return False


def extract_tables(sql: str, dialect: str = "postgres") -> set[str]:
    try:
        d_name = "oracle" if dialect == "oracle" else "postgres"
        parsed = sqlglot.parse_one(sql, read=d_name)
        if not parsed:
            return set()
        ctes = {cte.alias_or_name.lower() for cte in parsed.find_all(exp.CTE) if cte.alias_or_name}
        tables = {t.name.lower() for t in parsed.find_all(exp.Table) if t.name} - ctes
        return tables
    except Exception:
        return set()


def matches_auto_tables(table_name: str, patterns: list[str]) -> bool:
    if not table_name or not patterns:
        return False
    name_lower = table_name.lower()
    return any(fnmatch.fnmatch(name_lower, p.lower()) for p in patterns)


# ---------- config ----------
def read_config():
    if not CONFIG.exists():
        raise ValueError(f"Config not found: {CONFIG}")
    with open(CONFIG, "rb") as f:
        try:
            raw_cfg = tomllib.load(f)
        except Exception as e:
            raise ValueError(f"TOML parse error in {CONFIG}: {e}")

    # Enforce chmod 600 if config contains raw secrets
    has_raw = False
    raw_dbs = raw_cfg.get("db", {})
    if isinstance(raw_dbs, dict):
        for driver_type, table in raw_dbs.items():
            if isinstance(table, dict):
                for name, cfg in table.items():
                    if isinstance(cfg, dict) and has_raw_secret(cfg):
                        has_raw = True
                        break
            if has_raw:
                break

    if has_raw and os.name == "posix":
        mode = CONFIG.stat().st_mode & 0o777
        if mode & 0o077:
            raise ValueError(
                f"Config contains raw secrets and permissions on {CONFIG} are too loose ({oct(mode)}). "
                f"Run: chmod 600 {CONFIG}"
            )
    return raw_cfg


def databases():
    global DISABLED_DATABASES
    DISABLED_DATABASES = {}

    raw_cfg = read_config()
    raw_db = raw_cfg.get("db", {})
    if not raw_db:
        raise ValueError(f"No [db.<driver>.<name>] found in {CONFIG}")

    defaults = raw_cfg.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError(f"[defaults] must be a key-value table in {CONFIG}")
    unknown_defs = set(defaults.keys()) - KNOWN_DEFAULTS_KEYS
    if unknown_defs:
        raise ValueError(f"Unknown key(s) in [defaults]: {', '.join(sorted(unknown_defs))}")

    default_auto_meta = defaults.get("auto_metadata", True)
    default_max_rows = defaults.get("max_rows", 100)
    default_timeout_ms = defaults.get("timeout_ms", 10_000)

    normalized = {}

    for driver_type, table in raw_db.items():
        if not isinstance(table, dict):
            continue

        if driver_type not in DRIVERS:
            raise ValueError(
                f"Unknown driver '[db.{driver_type}]'. Driver must be one of: {', '.join(DRIVERS)}. "
                f"Format required: [db.<driver>.<name>]"
            )

        for name, sub_cfg in table.items():
            composite_key = f"{driver_type}:{name}"
            if not isinstance(sub_cfg, dict):
                DISABLED_DATABASES[composite_key] = "Configuration must be a key-value table"
                continue

            cfg = dict(sub_cfg)
            cfg["type"] = driver_type

            # Check unknown keys
            unknown_keys = set(cfg.keys()) - KNOWN_DB_KEYS
            if unknown_keys:
                DISABLED_DATABASES[composite_key] = f"Unknown configuration key(s): {', '.join(sorted(unknown_keys))}"
                continue

            # Check required fields
            if not cfg.get("dsn"):
                DISABLED_DATABASES[composite_key] = "Missing required field 'dsn'"
                continue
            if driver_type == "oracle" and not cfg.get("user") and ("/" not in cfg.get("dsn", "")):
                DISABLED_DATABASES[composite_key] = "Missing required field 'user' for Oracle"
                continue

            # Check auto_tables format
            if "auto_tables" in cfg:
                if not isinstance(cfg["auto_tables"], list) or not all(isinstance(x, str) for x in cfg["auto_tables"]):
                    DISABLED_DATABASES[composite_key] = "'auto_tables' must be a list of strings"
                    continue

            # Inheritance (auto_tables is NEVER inherited)
            cfg["auto_metadata"] = cfg.get("auto_metadata", default_auto_meta)
            cfg["max_rows"] = cfg.get("max_rows", default_max_rows)
            cfg["timeout_ms"] = cfg.get("timeout_ms", default_timeout_ms)
            cfg.setdefault("auto_tables", [])

            # Dry-run interpolation validation per DB
            try:
                test_pool = set()
                resolve(cfg.get("dsn", ""), shorthand=False, secrets_pool=test_pool)
                if "user" in cfg:
                    resolve(str(cfg["user"]), shorthand=True, secrets_pool=test_pool)
                if "password" in cfg:
                    resolve(str(cfg["password"]), shorthand=True, secrets_pool=test_pool)
                elif cfg.get("password_env"):
                    p_env = cfg["password_env"]
                    if p_env not in os.environ:
                        raise ValueError(f"Environment variable '{p_env}' is not set")
            except Exception as e:
                DISABLED_DATABASES[composite_key] = str(e)
                continue

            normalized[composite_key] = cfg

    if not normalized and not DISABLED_DATABASES:
        raise ValueError(f"No valid [db.<driver>.<name>] configurations found in {CONFIG}")

    return normalized


# ---------- database ----------
class DB:
    conn = None

    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.max_rows = cfg.get("max_rows", 100)
        self.timeout_ms = cfg.get("timeout_ms", 10_000)
        self.secrets = set()

    def get_password(self) -> str:
        if "password" in self.cfg:
            return resolve(str(self.cfg["password"]), shorthand=True, secrets_pool=self.secrets)
        env = self.cfg.get("password_env")
        if env:
            if env not in os.environ:
                raise ValueError(f"Environment variable '{env}' is not set")
            val = os.environ[env]
            self.secrets.add(val)
            return val
        return ""

    def get_user(self) -> str:
        if "user" in self.cfg:
            return resolve(str(self.cfg["user"]), shorthand=True, secrets_pool=self.secrets)
        return ""

    def get_dsn(self) -> str:
        dsn = resolve(self.cfg.get("dsn", ""), shorthand=False, secrets_pool=self.secrets)
        m = re.search(r"://[^/@:]+:([^/@]+)@", dsn)
        if m:
            self.secrets.add(m.group(1))
        m_ora = re.search(r"^[^/@:]+/([^/@]+)@", dsn)
        if m_ora:
            self.secrets.add(m_ora.group(1))
        return dsn

    def redact(self, text: str) -> str:
        if not text or not self.secrets:
            return str(text)
        out = str(text)
        for s in sorted(self.secrets, key=len, reverse=True):
            if s and len(s) > 2:
                out = out.replace(s, "***")
        return out

    def run(self, sql, params=()):
        if self.conn is None:
            self.connect()
        try:
            return self._exec(sql, params)
        except Exception:
            if self.healthy():
                raise
            self.close()
            self.connect()
            return self._exec(sql, params)

    def _exec(self, sql, params):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return cols, cur.fetchmany(self.max_rows)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = None


class Postgres(DB):
    LIST_SQL = "SELECT table_name FROM information_schema.tables WHERE table_schema = %s"
    DESC_SQL = ("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position")

    @property
    def default_schema(self):
        return self.cfg.get("schema", "public")

    def connect(self):
        import psycopg
        dsn = self.get_dsn()
        pw = self.get_password()
        user = self.get_user()
        kwargs = {}
        if user:
            kwargs["user"] = user
        if pw:
            kwargs["password"] = pw

        self.conn = psycopg.connect(
            dsn,
            autocommit=True,
            **kwargs,
            options=f"-c statement_timeout={self.timeout_ms} -c default_transaction_read_only=on",
        )

    def healthy(self):
        return not (self.conn.closed or self.conn.broken)


class Oracle(DB):
    LIST_SQL = "SELECT table_name FROM all_tables WHERE owner = UPPER(:1)"
    DESC_SQL = ("SELECT column_name, data_type, nullable FROM all_tab_columns "
                "WHERE owner = UPPER(:1) AND table_name = UPPER(:2) ORDER BY column_id")

    @property
    def default_schema(self):
        return self.cfg.get("schema", self.get_user() or self.cfg.get("user", ""))

    def connect(self):
        import oracledb
        dsn = self.get_dsn()
        pw = self.get_password()
        user = self.get_user()
        if user or pw:
            self.conn = oracledb.connect(user=user or "", password=pw or "", dsn=dsn)
        else:
            self.conn = oracledb.connect(dsn=dsn)
        self.conn.call_timeout = self.timeout_ms

    def healthy(self):
        return self.conn.is_healthy()

    def _exec(self, sql, params):
        try:
            with self.conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return cols, cur.fetchmany(self.max_rows)
        finally:
            try:
                self.conn.rollback()
            except Exception:
                pass


DRIVERS = {"postgres": Postgres, "oracle": Oracle}
conns: dict[str, DB] = {}


def resolve_db_key(requested_name: str, cfgs: dict) -> str:
    # 1. Exact match (e.g. "postgres:ecommerce-dev" or "inventory-core")
    if requested_name in cfgs:
        return requested_name

    # 2. Check if requested name matches an unqualified short name
    matches = [k for k in cfgs if k.endswith(f":{requested_name}")]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        raise ValueError(
            f"Database name '{requested_name}' is ambiguous across multiple types: {', '.join(matches)}. "
            f"Specify the type prefix explicitly (e.g. '{matches[0]}')."
        )

    # 3. Handle pgsql: alias to postgres:
    if requested_name.startswith("pgsql:"):
        aliased = f"postgres:{requested_name[6:]}"
        if aliased in cfgs:
            return aliased

    available = []
    for k in cfgs:
        available.append(k)
        if ":" in k:
            short = k.split(":", 1)[1]
            if short not in available:
                available.append(short)

    raise ValueError(f"db '{requested_name}' does not exist. Available choices: {', '.join(available)}")


def get_db(name: str):
    cfgs = databases()  # reloaded on each call: config updates apply immediately
    try:
        resolved_key = resolve_db_key(name, cfgs)
    except ValueError as e:
        # Check if the requested name matches a disabled database
        for k, err in DISABLED_DATABASES.items():
            if k == name or k.endswith(f":{name}"):
                raise ValueError(f"Database [{k}] is disabled: {err}") from None
        raise e

    cfg = cfgs[resolved_key]
    cur = conns.get(resolved_key)
    if cur is None or cur.cfg != cfg:
        if cur:
            cur.close()
        cur = conns[resolved_key] = DRIVERS[cfg["type"]](resolved_key, cfg)
    return cur


DEFAULT_PORT = 48260
PORT_RANGE_SIZE = 10
STATE_PORT_FILE = CONFIG.parent / "monitor.port"


def resolve_candidate_ports():
    ports = []
    # 1. State file written by monitor
    if STATE_PORT_FILE.exists():
        try:
            val = int(STATE_PORT_FILE.read_text(encoding="utf-8").strip())
            if 1 <= val <= 65535:
                ports.append(val)
        except Exception:
            pass

    # 2. Environment variable
    env_port = os.environ.get("MONITOR_PORT")
    if env_port:
        try:
            val = int(env_port)
            if val not in ports:
                ports.append(val)
        except ValueError:
            pass

    # 3. Config file
    try:
        cfg_port = int(read_config().get("monitor", {}).get("port", DEFAULT_PORT))
        if cfg_port not in ports:
            ports.append(cfg_port)
    except Exception:
        pass

    # 4. Fallback range scan
    for p in range(DEFAULT_PORT, DEFAULT_PORT + PORT_RANGE_SIZE):
        if p not in ports:
            ports.append(p)

    return ports


# ---------- connection to monitor (one link per client session) ----------
class Link:
    reader = writer = None
    session = "?"

    async def ensure(self, timeout_sec=0):
        if self.writer and not self.writer.is_closing() and not self.reader.at_eof():
            return
        
        deadline = time.monotonic() + max(0, timeout_sec)
        while True:
            candidate_ports = resolve_candidate_ports()
            for port in candidate_ports:
                try:
                    self.reader, self.writer = await asyncio.open_connection("127.0.0.1", port)
                    self.session = json.loads(await asyncio.wait_for(self.reader.readline(), 5))["session"]
                    return
                except (OSError, asyncio.TimeoutError):
                    continue

            if time.monotonic() >= deadline:
                raise OSError("No active monitor connection found")
            await asyncio.sleep(1)

    async def send(self, msg):
        self.writer.write((json.dumps(msg, default=str) + "\n").encode())
        await self.writer.drain()

    def drop(self):
        if self.writer:
            self.writer.close()
        self.writer = None


link = Link()
lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(server):
    try:
        await link.ensure(timeout_sec=0)  # register to monitor when client opens
    except (OSError, asyncio.TimeoutError, ValueError):
        pass  # monitor offline: retry on first tool call
    yield


mcp = FastMCP("db-grep", lifespan=lifespan)


def audit(event, **kw):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event,
           "session": link.session, **kw}
    with open(AUDIT, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def check_konteks(k):
    k = re.sub(r"[\x00-\x1f\x7f]", "", k or "").strip()[:80]
    if "·" not in k:
        raise ValueError("konteks must be in format '<project-name> · <action>', "
                         "example 'ecommerce · fetch product inventory'")
    return k


# ---------- approval gate ----------
async def execute(tool, d, konteks, sql, params=(), reject=None):
    konteks = check_konteks(konteks)

    # Evaluate auto-allow rules
    auto_metadata = d.cfg.get("auto_metadata", True)
    auto_tables = d.cfg.get("auto_tables", [])
    auto_allow = False
    auto_reason = None

    if tool == "list_tables":
        if auto_metadata:
            auto_allow = True
            auto_reason = "auto_metadata = true"
    elif tool == "describe_table":
        table_name = params[1] if len(params) > 1 else ""
        if auto_metadata:
            auto_allow = True
            auto_reason = "auto_metadata = true"
        elif auto_tables and matches_auto_tables(table_name, auto_tables):
            auto_allow = True
            auto_reason = f"table '{table_name}' matches auto_tables"
    elif tool == "query":
        if auto_tables:
            queried_tables = extract_tables(sql, dialect=d.cfg["type"])
            if queried_tables and all(matches_auto_tables(tbl, auto_tables) for tbl in queried_tables):
                auto_allow = True
                auto_reason = f"all tables ({', '.join(sorted(queried_tables))}) match auto_tables"

    async with lock:
        try:
            # Wait up to 25s if monitor is not running yet
            await link.ensure(timeout_sec=25)
        except (OSError, asyncio.TimeoutError):
            audit("denied", reason="monitor offline", db=d.name, konteks=konteks, sql=sql)
            raise PermissionError(
                f"MONITOR_OFFLINE: Approval monitor is not running.\n"
                f"Target Database: {d.name}\n"
                f"Requested Action: {konteks}\n"
                f"SQL: {sql}\n\n"
                f"The database monitor (`db-grep --monitor`) must be opened in another terminal to review and approve this action."
            )

        msg = {"tool": tool, "db": d.name, "db_type": d.cfg.get("type", ""), "konteks": konteks, "sql": sql}
        if reject:
            await link.send({"type": "blocked", "reason": reject, **msg})
            audit("blocked", reason=reject)
            raise ValueError(f"Rejected: {reject}")

        if auto_allow:
            audit("auto_allow", db=d.name, tool=tool, reason=auto_reason, sql=sql)
            await link.send({"type": "auto_allow", "reason": auto_reason, "params": params, **msg})
        else:
            audit("request", db=d.name, db_type=d.cfg.get("type", ""), konteks=konteks, tool=tool, sql=sql, params=params)
            await link.send({"type": "request", "params": params, **msg})
            line = await link.reader.readline()  # timeout is managed by monitor
            if not line:
                link.drop()
                audit("denied", reason="monitor disconnected")
                raise PermissionError("Monitor disconnected while waiting for approval")
            if not json.loads(line)["approved"]:
                audit("denied", reason="user")
                raise PermissionError(
                    f"ACTION_DENIED_BY_USER: Query was rejected by user in terminal monitor.\n"
                    f"SQL: {sql}\n"
                    f"Context: {konteks}"
                )

        t0 = time.monotonic()
        try:
            cols, rows = await asyncio.to_thread(d.run, sql, params)
        except Exception as e:
            redacted_err = d.redact(str(e))
            audit("error", error=redacted_err)
            await link.send({"type": "error", "message": redacted_err})
            raise RuntimeError(redacted_err) from None

        ms = int((time.monotonic() - t0) * 1000)
        audit("executed", rows=len(rows), ms=ms)  # query data itself is never logged
        await link.send({"type": "result", "cols": cols, "rows": rows,
                         "ms": ms, "limit": d.max_rows})
        return json.dumps([dict(zip(cols, r)) for r in rows], default=str)


# ---------- diagnostic and health inspection ----------
PING_QUERIES = {
    "postgres": "SELECT 1;",
    "oracle": "SELECT 1 FROM DUAL",
}


def inspect_config() -> dict:
    report = {
        "valid": True,
        "config_file": str(CONFIG),
        "file_exists": CONFIG.exists(),
        "permissions": None,
        "has_raw_secrets": False,
        "defaults": {},
        "monitor_port": None,
        "databases": [],
        "errors": [],
        "warnings": [],
    }

    if not CONFIG.exists():
        report["valid"] = False
        report["errors"].append(f"Config file not found: {CONFIG}")
        return report

    if os.name == "posix":
        mode = CONFIG.stat().st_mode & 0o777
        report["permissions"] = oct(mode)

    try:
        with open(CONFIG, "rb") as f:
            raw_cfg = tomllib.load(f)
    except Exception as e:
        report["valid"] = False
        report["errors"].append(f"TOML parse error: {e}")
        return report

    report["monitor_port"] = raw_cfg.get("monitor", {}).get("port", DEFAULT_PORT)
    defaults = raw_cfg.get("defaults", {})
    if isinstance(defaults, dict):
        report["defaults"] = defaults
        unknown_defs = set(defaults.keys()) - KNOWN_DEFAULTS_KEYS
        if unknown_defs:
            report["valid"] = False
            report["errors"].append(f"Unknown key(s) in [defaults]: {', '.join(sorted(unknown_defs))}")

    raw_dbs = raw_cfg.get("db", {})
    if not raw_dbs:
        report["valid"] = False
        report["errors"].append("No database tables found. Format required: [db.<driver>.<name>]")
        return report

    # Check for raw secrets across all configs
    has_raw = False
    for driver_type, table in raw_dbs.items():
        if isinstance(table, dict):
            for db_name, cfg in table.items():
                if isinstance(cfg, dict) and has_raw_secret(cfg):
                    has_raw = True
                    break
        if has_raw:
            break
    report["has_raw_secrets"] = has_raw

    if os.name == "posix":
        mode = CONFIG.stat().st_mode & 0o777
        if has_raw and (mode & 0o077):
            report["valid"] = False
            report["errors"].append(
                f"Config contains raw secrets and file permissions ({oct(mode)}) are too loose. "
                f"Run: chmod 600 {CONFIG}"
            )
        elif mode & 0o077:
            report["warnings"].append(
                f"File permissions ({oct(mode)}) allow other local users to read config. "
                f"Recommended: chmod 600 {CONFIG}"
            )

    for driver_type, table in raw_dbs.items():
        if not isinstance(table, dict):
            report["errors"].append(f"[db.{driver_type}] must be a table containing database definitions.")
            report["valid"] = False
            continue

        if driver_type not in DRIVERS:
            report["errors"].append(f"Unknown driver '[db.{driver_type}]'. Supported drivers: {', '.join(DRIVERS)}")
            report["valid"] = False
            continue

        for db_name, cfg in table.items():
            db_report = {
                "name": db_name,
                "key": f"{driver_type}:{db_name}",
                "driver": driver_type,
                "description": cfg.get("description", "") if isinstance(cfg, dict) else "",
                "auto_metadata": cfg.get("auto_metadata", defaults.get("auto_metadata", True)) if isinstance(cfg, dict) else True,
                "auto_tables": cfg.get("auto_tables", []) if isinstance(cfg, dict) else [],
                "valid": True,
                "errors": [],
            }

            if not isinstance(cfg, dict):
                db_report["valid"] = False
                db_report["errors"].append("Configuration must be a key-value table.")
                report["databases"].append(db_report)
                report["valid"] = False
                continue

            # Check unknown keys
            unknown_keys = set(cfg.keys()) - KNOWN_DB_KEYS
            if unknown_keys:
                db_report["valid"] = False
                db_report["errors"].append(f"Unknown key(s): {', '.join(sorted(unknown_keys))}")

            # Check required fields
            if not cfg.get("dsn"):
                db_report["valid"] = False
                db_report["errors"].append("Missing required field 'dsn'")
            if driver_type == "oracle" and not cfg.get("user") and ("/" not in cfg.get("dsn", "")):
                db_report["valid"] = False
                db_report["errors"].append("Missing required field 'user' for Oracle")

            # Check auto_tables
            if "auto_tables" in cfg:
                if not isinstance(cfg["auto_tables"], list) or not all(isinstance(x, str) for x in cfg["auto_tables"]):
                    db_report["valid"] = False
                    db_report["errors"].append("'auto_tables' must be a list of strings")

            # Check secret resolution & auth source
            try:
                test_pool = set()
                resolve(cfg.get("dsn", ""), shorthand=False, secrets_pool=test_pool)
                if "user" in cfg:
                    resolve(str(cfg["user"]), shorthand=True, secrets_pool=test_pool)
                if "password" in cfg:
                    resolve(str(cfg["password"]), shorthand=True, secrets_pool=test_pool)
                    p_str = str(cfg["password"])
                    if p_str.startswith("env:") or "${env:" in p_str:
                        db_report["auth_source"] = "env"
                    elif p_str.startswith("file:") or "${file:" in p_str:
                        db_report["auth_source"] = "file"
                    else:
                        db_report["auth_source"] = "raw"
                elif cfg.get("password_env"):
                    p_env = cfg["password_env"]
                    if p_env not in os.environ:
                        raise ValueError(f"Environment variable '{p_env}' is not set")
                    db_report["auth_source"] = f"env:{p_env}"
                else:
                    db_report["auth_source"] = "dsn / none"
            except Exception as e:
                db_report["valid"] = False
                db_report["errors"].append(str(e))

            if not db_report["valid"]:
                report["valid"] = False

            report["databases"].append(db_report)

    return report


def ping_database(db_key: str, cfg: dict) -> dict:
    driver_type = cfg["type"]
    ping_sql = PING_QUERIES.get(driver_type, "SELECT 1;")
    result = {
        "db": db_key,
        "type": driver_type,
        "query": ping_sql,
        "status": "failed",
        "latency_ms": None,
        "error": None,
    }

    t0 = time.monotonic()
    db_inst = None
    try:
        db_inst = DRIVERS[driver_type](db_key, cfg)
        db_inst.connect()
        cols, rows = db_inst.run(ping_sql)
        ms = round((time.monotonic() - t0) * 1000, 2)
        result["status"] = "connected"
        result["latency_ms"] = ms
    except Exception as e:
        if db_inst:
            result["error"] = db_inst.redact(str(e))
        else:
            result["error"] = str(e)
    finally:
        if db_inst:
            db_inst.close()

    return result
    try:
        db_inst = DRIVERS[driver_type](db_key, cfg)
        db_inst.connect()
        cols, rows = db_inst.run(ping_sql)
        ms = round((time.monotonic() - t0) * 1000, 2)
        result["status"] = "connected"
        result["latency_ms"] = ms
    except Exception as e:
        result["error"] = str(e)
    finally:
        if db_inst:
            db_inst.close()

    return result


def execute_connection_check(target: str = "all") -> list[dict]:
    all_dbs = databases()
    targets_to_check = []

    target_str = (target or "all").strip()
    if target_str.lower() in ("all", "*"):
        targets_to_check = list(all_dbs.keys())
    else:
        # Support comma or space separated names
        tokens = [t.strip() for t in re.split(r"[,;\s]+", target_str) if t.strip()]
        for token in tokens:
            resolved = resolve_db_key(token, all_dbs)
            if resolved not in targets_to_check:
                targets_to_check.append(resolved)

    results = []
    for key in targets_to_check:
        res = ping_database(key, all_dbs[key])
        results.append(res)

    audit("check_connection", targets=targets_to_check, count=len(results))
    return results


# ---------- tools ----------
@mcp.tool()
async def check_config() -> str:
    """Validate config.toml format, permissions, required fields, and environment variables. Does not query databases."""
    report = inspect_config()
    audit("check_config", valid=report["valid"])
    return json.dumps(report, indent=2)


@mcp.tool()
async def check_connection(db: str = "all") -> str:
    """Check database physical connectivity and latency using explicit health-check queries ('SELECT 1;' for Postgres, 'SELECT 1 FROM DUAL' for Oracle). Parameter 'db' can be 'all', a single database name, or comma-separated names. Does not require interactive monitor approval."""
    results = await asyncio.to_thread(execute_connection_check, db)
    return json.dumps(results, indent=2)


@mcp.tool()
async def list_databases() -> str:
    """List available database connections (name, type, description). Does not access databases."""
    all_dbs = databases()
    
    # Check for short name collisions
    short_names = {}
    for key in all_dbs:
        short = key.split(":", 1)[1] if ":" in key else key
        short_names.setdefault(short, []).append(key)

    out = []
    for key, c in all_dbs.items():
        short = key.split(":", 1)[1] if ":" in key else key
        # If collision exists for this short name, present the full qualified name as primary
        effective_name = key if len(short_names.get(short, [])) > 1 else short
        aliases = [key] if effective_name != key else ([short] if short != key else [])
        
        item = {
            "db": effective_name,
            "type": c["type"],
            "description": c.get("description", ""),
        }
        if aliases:
            item["aliases"] = aliases
        out.append(item)

    audit("list_databases")
    return json.dumps(out)


@mcp.tool()
async def list_tables(db: DbName, konteks: Konteks, schema: str = "") -> str:
    """List table names in a schema (default: connection default schema). Requires user approval."""
    d = get_db(db)
    return await execute("list_tables", d, konteks, d.LIST_SQL, (schema or d.default_schema,))


@mcp.tool()
async def describe_table(db: DbName, table: str, konteks: Konteks, schema: str = "") -> str:
    """Columns, data types, and nullability of a table. Requires user approval."""
    d = get_db(db)
    return await execute("describe_table", d, konteks, d.DESC_SQL, (schema or d.default_schema, table))


@mcp.tool()
async def query(db: DbName, sql: str, konteks: Konteks) -> str:
    """Execute read-only SELECT query, rows capped per connection. Requires user approval."""
    d = get_db(db)
    reject = None if sql.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
    return await execute("query", d, konteks, sql, (), reject)


def start():
    try:
        databases()  # invalid config fails fast on startup
    except ValueError as e:
        sys.exit(str(e))
    mcp.run()


if __name__ == "__main__":
    start()
