import asyncio, json, logging, os, re, sys, time, tomllib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from pydantic import Field
from mcp.server.fastmcp import FastMCP

logging.basicConfig(stream=sys.stderr)  # stdout is reserved for stdio MCP protocol
log = logging.getLogger("db-grep")

CONFIG = Path(os.environ.get("DB_GREP_CONFIG", os.environ.get("DB_MCP_CONFIG", "~/.config/db-grep/config.toml"))).expanduser()
AUDIT = Path(os.environ.get("AUDIT_LOG", CONFIG.parent / "audit.jsonl")).expanduser()

Konteks = Annotated[str, Field(
    description="Required. Format '<project-name> · <action>', example 'bekal-akun · fetch student sample data'")]
DbName = Annotated[str, Field(description="Database connection name. See list_databases.")]


# ---------- config ----------
def read_config():
    if not CONFIG.exists():
        raise ValueError(f"Config not found: {CONFIG}")
    if os.name == "posix" and CONFIG.stat().st_mode & 0o077:
        log.warning("Run: chmod 600 %s (file contains credentials)", CONFIG)
    with open(CONFIG, "rb") as f:
        return tomllib.load(f)


def databases():
    dbs = read_config().get("db", {})
    if not dbs:
        raise ValueError(f"No [db.<name>] found in {CONFIG}")
    for name, c in dbs.items():
        if c.get("type") not in DRIVERS:
            raise ValueError(f"[db.{name}] type must be one of: {', '.join(DRIVERS)}")
        for key in ("dsn", "user") if c["type"] == "oracle" else ("dsn",):
            if not c.get(key):
                raise ValueError(f"[db.{name}] missing required field '{key}'")
    return dbs


def password(c):
    if c.get("password"):
        return c["password"]
    env = c.get("password_env")
    if env and env not in os.environ:
        raise ValueError(f"Env var {env} is not set (required by this connection)")
    return os.environ.get(env, "") if env else ""


# ---------- database ----------
class DB:
    conn = None

    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.max_rows = cfg.get("max_rows", 100)
        self.timeout_ms = cfg.get("timeout_ms", 10_000)

    def run(self, sql, params=()):
        if self.conn is None:
            self.connect()
        try:
            return self._exec(sql, params)
        except Exception:
            if self.healthy():
                raise  # standard SQL error
            self.close()  # connection dropped: reconnect and retry once
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
        pw = password(self.cfg)
        self.conn = psycopg.connect(
            self.cfg["dsn"], autocommit=True, **({"password": pw} if pw else {}),
            options=f"-c statement_timeout={self.timeout_ms} -c default_transaction_read_only=on")

    def healthy(self):
        return not (self.conn.closed or self.conn.broken)


class Oracle(DB):
    LIST_SQL = "SELECT table_name FROM all_tables WHERE owner = UPPER(:1)"
    DESC_SQL = ("SELECT column_name, data_type, nullable FROM all_tab_columns "
                "WHERE owner = UPPER(:1) AND table_name = UPPER(:2) ORDER BY column_id")

    @property
    def default_schema(self):
        return self.cfg.get("schema", self.cfg["user"])

    def connect(self):
        import oracledb
        self.conn = oracledb.connect(user=self.cfg["user"], password=password(self.cfg),
                                     dsn=self.cfg["dsn"])
        self.conn.call_timeout = self.timeout_ms

    def healthy(self):
        return self.conn.is_healthy()

    def _exec(self, sql, params):
        try:
            with self.conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")  # extra layer; read-only grant is primary
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


def get_db(name):
    cfgs = databases()  # reloaded on each call: config updates apply immediately
    if name not in cfgs:
        raise ValueError(f"db '{name}' does not exist. Available choices: {', '.join(cfgs)}")
    cur = conns.get(name)
    if cur is None or cur.cfg != cfgs[name]:
        if cur:
            cur.close()
        cur = conns[name] = DRIVERS[cfgs[name]["type"]](name, cfgs[name])
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
                         "example 'bekal-akun · fetch student sample data'")
    return k


# ---------- approval gate ----------
async def execute(tool, d, konteks, sql, params=(), reject=None):
    konteks = check_konteks(konteks)
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

        audit("request", db=d.name, db_type=d.cfg.get("type", ""), konteks=konteks, tool=tool, sql=sql, params=params)
        try:
            msg = {"tool": tool, "db": d.name, "db_type": d.cfg.get("type", ""), "konteks": konteks, "sql": sql}
            if reject:
                await link.send({"type": "blocked", "reason": reject, **msg})
                audit("blocked", reason=reject)
                raise ValueError(f"Rejected: {reject}")

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
                audit("error", error=str(e))
                await link.send({"type": "error", "message": str(e)})
                raise
            ms = int((time.monotonic() - t0) * 1000)
            audit("executed", rows=len(rows), ms=ms)  # query data itself is never logged
            await link.send({"type": "result", "cols": cols, "rows": rows,
                             "ms": ms, "limit": d.max_rows})
            return json.dumps([dict(zip(cols, r)) for r in rows], default=str)
        except (asyncio.CancelledError, ConnectionError):
            link.drop()  # stale responses must not be read by subsequent requests
            raise


# ---------- tools ----------
@mcp.tool()
async def list_databases() -> str:
    """List available database connections (name, type, description). Does not access databases."""
    out = [{"db": n, "type": c["type"], "description": c.get("description", "")}
           for n, c in databases().items()]
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
