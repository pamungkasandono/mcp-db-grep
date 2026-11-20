import asyncio, fnmatch, json, logging, os, re, sys, time, tomllib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, unquote, urlparse
from pydantic import Field
from mcp.server.fastmcp import FastMCP
import sqlglot
from sqlglot import exp

logging.basicConfig(stream=sys.stderr)  # stdout is reserved for stdio MCP protocol
log = logging.getLogger("db-grep")

CONFIG = Path(os.environ.get("DB_GREP_CONFIG", os.environ.get("DB_MCP_CONFIG", "~/.config/db-grep/config.toml"))).expanduser()
AUDIT = Path(os.environ.get("AUDIT_LOG", CONFIG.parent / "audit.jsonl")).expanduser()

Context = Annotated[str, Field(
    description="Required. Format '<project-name> · <action>', example 'ecommerce · fetch product inventory'")]
Konteks = Context
DbName = Annotated[str, Field(description="Database connection name. See list_databases.")]

KNOWN_DEFAULTS_KEYS = {"allow_metadata", "allow_data", "max_rows", "timeout_ms"}
KNOWN_DB_KEYS = {
    "type", "dsn", "user", "password", "password_env", "description",
    "schema", "allow_metadata", "allow_data",
    "allow_tables", "restricted_tables", "restricted_columns", "max_rows", "timeout_ms"
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


URI_SCHEMES = ("postgres://", "postgresql://", "mysql://")


def resolve(value: str, shorthand: bool = False, secrets_pool: set[str] | None = None) -> str:
    if not isinstance(value, str):
        return value
    if shorthand:
        if value.startswith("raw:"):
            val = value[4:]
            if secrets_pool is not None and val:
                secrets_pool.add(val)
            return val
        if value.strip() in ("env:", "file:"):
            raise ValueError(f"Empty target in shorthand reference: '{value}'")
        if m := SHORT.match(value):
            target = m[2].strip()
            if not target:
                raise ValueError(f"Empty target in shorthand reference: '{value}'")
            val = _read(m[1], target)
            if secrets_pool is not None and val:
                secrets_pool.add(val)
            return val

    escaped = value.replace("$$", "\0")
    is_uri = escaped.strip().lower().startswith(URI_SCHEMES)

    def sub(m):
        v = _read(m[1], m[2].strip())
        # Auto-encode if explicitly '|urlenc' OR if token sits inside URI userinfo (before '@')
        in_uri_userinfo = is_uri and ("@" in escaped[m.end():].split("?", 1)[0])
        should_encode = bool(m[3]) or in_uri_userinfo
        encoded = quote(v, safe="") if should_encode else v
        if secrets_pool is not None and v:
            secrets_pool.add(v)
            if should_encode:
                secrets_pool.add(encoded)
        return encoded

    out = REF.sub(sub, escaped)
    if "${" in out:
        raise ValueError(f"Invalid or unclosed interpolation syntax in: '{value}'")
    return out.replace("\0", "$")


def normalize_dsn_credentials(dsn: str, secrets_pool: set[str] | None = None) -> str:
    if not dsn or not isinstance(dsn, str):
        return dsn
    s = dsn.strip()
    if not s.lower().startswith(URI_SCHEMES):
        return dsn

    scheme, rest = s.split("://", 1)
    if "@" not in rest:
        return dsn

    # Separate optional query string before locating the rightmost '@' of userinfo
    if "?" in rest and "@" in rest.split("?", 1)[1]:
        before_q, query = rest.split("?", 1)
        if "@" not in before_q:
            return dsn
        userinfo, host_path = before_q.rsplit("@", 1)
        host_and_rest = f"{host_path}?{query}"
    else:
        userinfo, host_and_rest = rest.rsplit("@", 1)

    if not host_and_rest or host_and_rest.startswith("/"):
        return dsn

    if ":" in userinfo:
        raw_user, raw_pw = userinfo.split(":", 1)
        dec_user, dec_pw = unquote(raw_user), unquote(raw_pw)
        enc_user, enc_pw = quote(dec_user, safe=""), quote(dec_pw, safe="")
        if secrets_pool is not None and dec_pw:
            secrets_pool.add(dec_pw)
            secrets_pool.add(enc_pw)
        return f"{scheme}://{enc_user}:{enc_pw}@{host_and_rest}"

    dec_user = unquote(userinfo)
    return f"{scheme}://{quote(dec_user, safe='')}@{host_and_rest}"


def _extract_dsn_passwords(dsn: str, ignore_refs: bool = False) -> list[str]:
    if not dsn or not isinstance(dsn, str):
        return []
    target = REF.sub("__REF__", dsn.strip()) if ignore_refs else dsn.strip()
    passwords = []
    if target.lower().startswith(URI_SCHEMES):
        rest = target.split("://", 1)[1].split("?", 1)[0]
        if "@" in rest:
            userinfo = rest.rsplit("@", 1)[0]
            if ":" in userinfo:
                pw = userinfo.split(":", 1)[1]
                passwords.append(pw)
                if not ignore_refs and unquote(pw) != pw:
                    passwords.append(unquote(pw))
    elif "@" in target and "://" not in target:
        userinfo = target.rsplit("@", 1)[0]
        if "/" in userinfo:
            passwords.append(userinfo.split("/", 1)[1])

    if ignore_refs:
        return [p for p in passwords if p and "__REF__" not in p]
    return [p for p in passwords if p]


def _detect_source_kind(raw_val: str) -> str | None:
    if raw_val.startswith("env:") or "${env:" in raw_val:
        return "env"
    if raw_val.startswith("file:") or "${file:" in raw_val:
        return "file"
    return None


def has_raw_secret(cfg: dict) -> bool:
    pw = cfg.get("password")
    if pw is not None:
        pw_str = str(pw)
        is_ref = (
            pw_str.startswith(("env:", "file:")) or bool(REF.search(pw_str))
        ) and not pw_str.startswith("raw:")
        if not is_ref:
            return True

    dsn = cfg.get("dsn", "")
    if not dsn or not isinstance(dsn, str):
        return False
    if dsn.startswith(("env:", "file:")):
        return False
    dsn_check = dsn[4:] if dsn.startswith("raw:") else dsn
    return bool(_extract_dsn_passwords(dsn_check, ignore_refs=True))


def _validate_uri_dsn(s: str, label: str) -> None:
    rest = s.split("://", 1)[1]
    if not rest or rest.startswith("/") or any(c.isspace() for c in s):
        raise ValueError(f"Invalid {label} URI DSN syntax: '{s}'")
    host_part = rest.split("/", 1)[0].rsplit("@", 1)[-1]
    if not host_part:
        raise ValueError(f"Missing host in {label} DSN: '{s}'")
    if ":" in host_part and not host_part.endswith("]"):
        port_str = host_part.rsplit(":", 1)[1]
        if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
            raise ValueError(f"Invalid port '{port_str}' in {label} DSN: '{s}'")


def validate_dsn_syntax(driver_type: str, dsn: str) -> None:
    if not dsn or not isinstance(dsn, str) or not dsn.strip():
        raise ValueError("DSN cannot be empty")
    s = dsn.strip()

    if "${" in s or s.startswith(("env:", "file:", "raw:")):
        raise ValueError(f"Unresolved reference or invalid prefix in DSN: '{s}'")

    if driver_type == "postgres":
        if s.startswith(("postgres://", "postgresql://")):
            _validate_uri_dsn(s, "PostgreSQL")
        elif "=" in s:
            pairs = [p for p in s.split() if p]
            if not all(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*=.+$", p) for p in pairs):
                raise ValueError(f"Invalid PostgreSQL conninfo DSN syntax: '{s}'")
        else:
            raise ValueError(
                f"Invalid PostgreSQL DSN syntax: '{s}'. Expected 'postgres://...' URI or 'key=value' conninfo"
            )

    elif driver_type == "mysql":
        if s.startswith("mysql://"):
            _validate_uri_dsn(s, "MySQL")
        else:
            raise ValueError(f"Invalid MySQL DSN syntax: '{s}'. Expected 'mysql://...' URI")

    elif driver_type == "oracle":
        if "://" in s:
            raise ValueError(f"Invalid Oracle DSN syntax (unexpected URI scheme): '{s}'")
        if s.upper().startswith("(DESCRIPTION"):
            if s.count("(") != s.count(")"):
                raise ValueError(f"Unbalanced parentheses in Oracle TNS DSN: '{s}'")
            return
        if any(c.isspace() for c in s) or s.count("@") > 1:
            raise ValueError(f"Invalid Oracle DSN syntax: '{s}'")
        target = s.split("@", 1)[1] if "@" in s else s
        target = target.lstrip("/")
        if not target or target.endswith("/") or target.startswith(":"):
            raise ValueError(f"Invalid Oracle target host/service in DSN: '{s}'")
        host_port = target.split("/", 1)[0]
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            if not host or not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
                raise ValueError(f"Invalid host or port in Oracle DSN: '{s}'")


def _config_has_raw_secrets(raw_dbs: dict) -> bool:
    if not isinstance(raw_dbs, dict):
        return False
    return any(
        isinstance(cfg, dict) and has_raw_secret(cfg)
        for table in raw_dbs.values()
        if isinstance(table, dict)
        for cfg in table.values()
    )


def _validate_db_entry(driver_type: str, sub_cfg: object, defaults: dict) -> tuple[dict | None, str | None, list[str]]:
    if not isinstance(sub_cfg, dict):
        return None, None, ["Configuration must be a key-value table"]

    cfg = dict(sub_cfg)
    cfg["type"] = driver_type
    errors = []

    unknown_keys = set(cfg.keys()) - KNOWN_DB_KEYS
    if unknown_keys:
        errors.append(f"Unknown configuration key(s): {', '.join(sorted(unknown_keys))}")

    raw_dsn = cfg.get("dsn")
    if not raw_dsn:
        errors.append("Missing required field 'dsn'")

    for list_field in ("allow_metadata", "allow_data", "allow_tables", "restricted_tables", "restricted_columns"):
        if list_field in cfg:
            if not isinstance(cfg[list_field], list) or not all(isinstance(x, str) for x in cfg[list_field]):
                errors.append(f"'{list_field}' must be a list of strings")

    # Inheritance (allow_tables, restricted_tables, and restricted_columns are NEVER inherited)
    cfg["allow_metadata"] = cfg.get("allow_metadata", defaults.get("allow_metadata", ["read"]))
    cfg["allow_data"] = cfg.get("allow_data", defaults.get("allow_data", []))
    cfg["max_rows"] = cfg.get("max_rows", defaults.get("max_rows", 100))
    cfg["timeout_ms"] = cfg.get("timeout_ms", defaults.get("timeout_ms", 10_000))
    cfg.setdefault("allow_tables", [])
    cfg.setdefault("restricted_tables", [])
    cfg.setdefault("restricted_columns", [])

    auth_source = None
    try:
        test_pool = set()
        resolved_dsn = ""
        if raw_dsn:
            resolved_dsn = resolve(str(raw_dsn), shorthand=True, secrets_pool=test_pool)
            resolved_dsn = normalize_dsn_credentials(resolved_dsn, secrets_pool=test_pool)
            validate_dsn_syntax(driver_type, resolved_dsn)

        if driver_type == "oracle" and not cfg.get("user") and ("/" not in resolved_dsn):
            errors.append("Missing required field 'user' for Oracle")

        if "user" in cfg:
            resolve(str(cfg["user"]), shorthand=True, secrets_pool=test_pool)
        if "password" in cfg:
            p_str = str(cfg["password"])
            resolve(p_str, shorthand=True, secrets_pool=test_pool)
            auth_source = _detect_source_kind(p_str) or "raw"
        elif cfg.get("password_env"):
            p_env = cfg["password_env"]
            if p_env not in os.environ:
                raise ValueError(f"Environment variable '{p_env}' is not set")
            auth_source = f"env:{p_env}"
        else:
            dsn_src = _detect_source_kind(str(raw_dsn or ""))
            if dsn_src:
                auth_source = dsn_src
            elif has_raw_secret(cfg):
                auth_source = "raw"
            else:
                auth_source = "dsn / none"
    except Exception as e:
        errors.append(str(e))

    return cfg, auth_source, errors


def inspect_query_access(sql: str, dialect: str = "postgres") -> tuple[set[str], set[str], bool]:
    """
    Extracts referenced physical tables, explicit columns, and star/wildcard usage.
    Excludes CTE definitions and aliases from table names.
    """
    try:
        d_name = dialect if dialect in ("postgres", "oracle", "mysql") else "postgres"
        parsed = sqlglot.parse_one(sql, read=d_name)
        if not parsed:
            return set(), set(), False
        ctes = {cte.alias_or_name.lower() for cte in parsed.find_all(exp.CTE) if cte.alias_or_name}
        tables = {t.name.lower() for t in parsed.find_all(exp.Table) if t.name} - ctes
        columns = {c.name.lower() for c in parsed.find_all(exp.Column) if c.name}
        has_star = bool(list(parsed.find_all(exp.Star)))
        return tables, columns, has_star
    except Exception:
        return set(), set(), False


def extract_tables(sql: str, dialect: str = "postgres") -> set[str]:
    return inspect_query_access(sql, dialect)[0]


def matches_pattern(name: str, patterns: list[str]) -> bool:
    if not name or not patterns:
        return False
    name_lower = name.lower()
    return any(fnmatch.fnmatch(name_lower, p.lower()) for p in patterns)


def matches_allow_tables(table_name: str, patterns: list[str]) -> bool:
    return matches_pattern(table_name, patterns)


matches_auto_tables = matches_allow_tables


def check_restricted_columns(
    columns: set[str],
    tables: set[str],
    has_star: bool,
    restricted_patterns: list[str],
) -> list[str]:
    """
    Returns list of matched restricted column descriptions if any.
    Handles:
    - Bare column patterns (e.g. 'salary*', 'password_hash')
    - Table-qualified patterns (e.g. 'pegawai.salary_amount')
    - Wildcard '*' queries on tables with restricted columns
    """
    if not restricted_patterns:
        return []

    matched = set()

    for col in columns:
        for pat in restricted_patterns:
            pat_lower = pat.lower()
            if "." in pat_lower:
                pat_tbl, pat_col = pat_lower.split(".", 1)
                if any(fnmatch.fnmatch(t.lower(), pat_tbl) for t in tables) and fnmatch.fnmatch(col.lower(), pat_col):
                    matched.add(f"{col} ({pat})")
            else:
                if fnmatch.fnmatch(col.lower(), pat_lower):
                    matched.add(col)

    if has_star:
        for pat in restricted_patterns:
            pat_lower = pat.lower()
            if "." in pat_lower:
                pat_tbl, _ = pat_lower.split(".", 1)
                if any(fnmatch.fnmatch(t.lower(), pat_tbl) for t in tables):
                    matched.add(f"* on {pat_tbl} ({pat})")
            else:
                matched.add(f"* ({pat})")

    return sorted(matched)


check_sensitive_columns = check_restricted_columns


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
    if _config_has_raw_secrets(raw_cfg.get("db", {})) and os.name == "posix":
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
    for list_field in ("allow_metadata", "allow_data"):
        if list_field in defaults:
            if not isinstance(defaults[list_field], list) or not all(isinstance(x, str) for x in defaults[list_field]):
                raise ValueError(f"[defaults].{list_field} must be a list of strings")

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
            cfg, _, errors = _validate_db_entry(driver_type, sub_cfg, defaults)
            if errors:
                DISABLED_DATABASES[composite_key] = errors[0]
                continue
            normalized[composite_key] = cfg

    if not normalized and not DISABLED_DATABASES:
        raise ValueError(f"No valid [db.<driver>.<name>] configurations found in {CONFIG}")

    return normalized


# ---------- database ----------
class DB:
    conn = None
    LIST_SQL = "SELECT table_name FROM information_schema.tables WHERE table_schema = %s"
    DESC_SQL = ("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position")

    @staticmethod
    def _clean_pattern(pattern: str) -> str:
        clean = pattern.strip().replace("*", "%") if pattern else "%"
        if not clean:
            return "%"
        if not clean.startswith("%") and not clean.endswith("%"):
            return f"%{clean}%"
        return clean

    def get_search_query(self, pattern: str, schema: str = "", object_type: str = "ALL", limit: int = 50):
        clean_pat = self._clean_pattern(pattern)
        conds = [
            "table_schema NOT IN ('pg_catalog', 'information_schema')",
            "UPPER(table_name) LIKE UPPER(%s)"
        ]
        params = [clean_pat]

        ot = (object_type or "ALL").strip().upper()
        if ot == "TABLE":
            conds.append("table_type = 'BASE TABLE'")
        elif ot == "VIEW":
            conds.append("table_type = 'VIEW'")
        else:
            conds.append("table_type IN ('BASE TABLE', 'VIEW', 'FOREIGN TABLE')")

        if schema:
            conds.append("LOWER(table_schema) = LOWER(%s)")
            params.append(schema.strip())

        max_limit = max(1, min(limit or 50, self.max_rows))
        params.append(max_limit)

        sql = (
            "SELECT table_schema AS schema_name, table_name AS object_name, "
            "CASE WHEN table_type = 'BASE TABLE' THEN 'TABLE' ELSE table_type END AS object_type "
            "FROM information_schema.tables "
            f"WHERE {' AND '.join(conds)} "
            "ORDER BY table_schema, table_name "
            "LIMIT %s"
        )
        return sql, tuple(params)

    @staticmethod
    def _parse_and_validate_table(table: str, schema: str = "") -> tuple[str, str]:
        table_clean = table.strip().strip('"').strip("'")
        target_schema = schema.strip().strip('"').strip("'")
        if "." in table_clean and not target_schema:
            parts = table_clean.split(".", 1)
            target_schema = parts[0].strip().strip('"').strip("'")
            table_clean = parts[1].strip().strip('"').strip("'")

        if not re.match(r"^[A-Za-z0-9_#$]+$", table_clean):
            raise ValueError(f"Invalid table identifier: '{table}'")
        if target_schema and not re.match(r"^[A-Za-z0-9_#$]+$", target_schema):
            raise ValueError(f"Invalid schema identifier: '{schema}'")
        return target_schema, table_clean

    def get_sample_query(self, table: str, limit: int = 5, schema: str = ""):
        target_schema, table_clean = self._parse_and_validate_table(table, schema)
        ident = f'"{target_schema}"."{table_clean}"' if target_schema else f'"{table_clean}"'
        max_limit = max(1, min(limit or 5, 50, self.max_rows))
        return f"SELECT * FROM {ident} LIMIT %s", (max_limit,)

    def get_search_columns_query(self, pattern: str, schema: str = "", limit: int = 50):
        clean_pat = self._clean_pattern(pattern)
        conds = [
            "table_schema NOT IN ('pg_catalog', 'information_schema')",
            "UPPER(column_name) LIKE UPPER(%s)"
        ]
        params = [clean_pat]
        if schema:
            conds.append("LOWER(table_schema) = LOWER(%s)")
            params.append(schema.strip())
        max_limit = max(1, min(limit or 50, self.max_rows))
        params.append(max_limit)
        sql = (
            "SELECT table_schema AS schema_name, table_name, column_name, data_type "
            "FROM information_schema.columns "
            f"WHERE {' AND '.join(conds)} "
            "ORDER BY table_schema, table_name, ordinal_position "
            "LIMIT %s"
        )
        return sql, tuple(params)

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
        dsn = resolve(self.cfg.get("dsn", ""), shorthand=True, secrets_pool=self.secrets)
        dsn = normalize_dsn_credentials(dsn, secrets_pool=self.secrets)
        validate_dsn_syntax(self.cfg.get("type", "postgres"), dsn)
        self.secrets.update(_extract_dsn_passwords(dsn))
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


class MySQL(DB):
    @property
    def default_schema(self):
        if "schema" in self.cfg:
            return self.cfg["schema"]
        return urlparse(self.get_dsn()).path.lstrip("/")

    def get_search_query(self, pattern: str, schema: str = "", object_type: str = "ALL", limit: int = 50):
        clean_pat = self._clean_pattern(pattern)
        conds = [
            "table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')",
            "UPPER(table_name) LIKE UPPER(%s)"
        ]
        params = [clean_pat]

        ot = (object_type or "ALL").strip().upper()
        if ot == "TABLE":
            conds.append("table_type = 'BASE TABLE'")
        elif ot == "VIEW":
            conds.append("table_type = 'VIEW'")
        else:
            conds.append("table_type IN ('BASE TABLE', 'VIEW')")

        if schema:
            conds.append("LOWER(table_schema) = LOWER(%s)")
            params.append(schema.strip())

        max_limit = max(1, min(limit or 50, self.max_rows))
        params.append(max_limit)

        sql = (
            "SELECT table_schema AS schema_name, table_name AS object_name, "
            "CASE WHEN table_type = 'BASE TABLE' THEN 'TABLE' ELSE table_type END AS object_type "
            "FROM information_schema.tables "
            f"WHERE {' AND '.join(conds)} "
            "ORDER BY table_schema, table_name "
            "LIMIT %s"
        )
        return sql, tuple(params)

    def get_sample_query(self, table: str, limit: int = 5, schema: str = ""):
        target_schema, table_clean = self._parse_and_validate_table(table, schema)
        ident = f"`{target_schema}`.`{table_clean}`" if target_schema else f"`{table_clean}`"
        max_limit = max(1, min(limit or 5, 50, self.max_rows))
        return f"SELECT * FROM {ident} LIMIT %s", (max_limit,)

    def get_search_columns_query(self, pattern: str, schema: str = "", limit: int = 50):
        clean_pat = self._clean_pattern(pattern)
        conds = [
            "table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')",
            "UPPER(column_name) LIKE UPPER(%s)"
        ]
        params = [clean_pat]
        if schema:
            conds.append("LOWER(table_schema) = LOWER(%s)")
            params.append(schema.strip())
        max_limit = max(1, min(limit or 50, self.max_rows))
        params.append(max_limit)
        sql = (
            "SELECT table_schema AS schema_name, table_name, column_name, data_type "
            "FROM information_schema.columns "
            f"WHERE {' AND '.join(conds)} "
            "ORDER BY table_schema, table_name, ordinal_position "
            "LIMIT %s"
        )
        return sql, tuple(params)

    def connect(self):
        import pymysql
        dsn = self.get_dsn()
        parsed = urlparse(dsn)
        user = self.get_user() or (unquote(parsed.username) if parsed.username else "")
        pw = self.get_password() or (unquote(parsed.password) if parsed.password else "")
        db_name = parsed.path.lstrip("/") or self.cfg.get("schema", "")
        timeout_sec = max(1, int(self.timeout_ms / 1000))

        self.conn = pymysql.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=user,
            password=pw,
            database=db_name or None,
            autocommit=True,
            connect_timeout=timeout_sec,
            read_timeout=timeout_sec,
            write_timeout=timeout_sec,
        )
        with self.conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")

    def healthy(self):
        return bool(self.conn and getattr(self.conn, "open", False))


class Oracle(DB):
    LIST_SQL = ("SELECT table_name FROM all_tables WHERE owner = UPPER(:1) "
                "UNION "
                "SELECT view_name AS table_name FROM all_views WHERE owner = UPPER(:1) "
                "ORDER BY table_name")
    DESC_SQL = (
        "SELECT c.column_name, c.data_type, c.nullable, o.status "
        "FROM all_tab_columns c "
        "LEFT JOIN all_objects o ON ("
        "    o.owner = c.owner AND o.object_name = c.table_name "
        "    AND o.object_type IN ('TABLE', 'VIEW', 'SYNONYM', 'MATERIALIZED VIEW')"
        ") "
        "WHERE (c.owner = UPPER(:1) AND c.table_name = UPPER(:2)) "
        "   OR (c.owner, c.table_name) IN ("
        "       SELECT table_owner, table_name FROM all_synonyms "
        "       WHERE owner IN (UPPER(:1), 'PUBLIC') AND synonym_name = UPPER(:2)"
        "   ) "
        "ORDER BY c.column_id"
    )

    @property
    def default_schema(self):
        return self.cfg.get("schema", self.get_user() or self.cfg.get("user", ""))

    def get_search_query(self, pattern: str, schema: str = "", object_type: str = "ALL", limit: int = 50):
        clean_pat = self._clean_pattern(pattern)
        ot = (object_type or "ALL").strip().upper()
        if ot == "TABLE":
            type_filter = "object_type IN ('TABLE')"
        elif ot == "VIEW":
            type_filter = "object_type IN ('VIEW', 'MATERIALIZED VIEW')"
        elif ot == "SYNONYM":
            type_filter = "object_type IN ('SYNONYM')"
        else:
            type_filter = "object_type IN ('TABLE', 'VIEW', 'SYNONYM', 'MATERIALIZED VIEW')"

        conds = [type_filter, "UPPER(object_name) LIKE UPPER(:1)"]
        params = [clean_pat]

        idx = 2
        if schema:
            conds.append(f"owner = UPPER(:{idx})")
            params.append(schema.strip())
            idx += 1
        else:
            conds.append(
                "owner NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'CTXSYS', 'MDSYS', "
                "'XDB', 'OUTLN', 'WMSYS', 'APPQOSSYS', 'AUDSYS', 'GSMADMIN_INTERNAL', "
                "'LBACSYS', 'OJVMSYS')"
            )

        max_limit = max(1, min(limit or 50, self.max_rows))
        params.append(max_limit)

        sql = (
            "SELECT schema_name, object_name, object_type FROM ("
            "SELECT owner AS schema_name, object_name, object_type "
            "FROM all_objects "
            f"WHERE {' AND '.join(conds)} "
            "ORDER BY owner, object_name"
            f") WHERE ROWNUM <= :{idx}"
        )
        return sql, tuple(params)

    def get_sample_query(self, table: str, limit: int = 5, schema: str = ""):
        target_schema, table_clean = self._parse_and_validate_table(table, schema)
        target_owner = target_schema or self.default_schema
        ident = f'"{target_owner.upper()}"."{table_clean.upper()}"' if target_owner else f'"{table_clean.upper()}"'
        max_limit = max(1, min(limit or 5, 50, self.max_rows))
        return f"SELECT * FROM {ident} WHERE ROWNUM <= :1", (max_limit,)

    def get_search_columns_query(self, pattern: str, schema: str = "", limit: int = 50):
        clean_pat = self._clean_pattern(pattern)
        conds = ["UPPER(column_name) LIKE UPPER(:1)"]
        params = [clean_pat]
        idx = 2
        if schema:
            conds.append(f"owner = UPPER(:{idx})")
            params.append(schema.strip())
            idx += 1
        else:
            conds.append(
                "owner NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'CTXSYS', 'MDSYS', "
                "'XDB', 'OUTLN', 'WMSYS', 'APPQOSSYS', 'AUDSYS', 'GSMADMIN_INTERNAL', "
                "'LBACSYS', 'OJVMSYS')"
            )
        max_limit = max(1, min(limit or 50, self.max_rows))
        params.append(max_limit)
        sql = (
            "SELECT schema_name, table_name, column_name, data_type FROM ("
            "SELECT owner AS schema_name, table_name, column_name, data_type "
            "FROM all_tab_columns "
            f"WHERE {' AND '.join(conds)} "
            "ORDER BY owner, table_name, column_id"
            f") WHERE ROWNUM <= :{idx}"
        )
        return sql, tuple(params)

    def connect(self):
        import oracledb
        import oracle_driver

        oracle_driver.ensure_thick_mode()
        dsn = self.get_dsn()
        pw = self.get_password()
        user = self.get_user()
        try:
            if user or pw:
                self.conn = oracledb.connect(user=user or "", password=pw or "", dsn=dsn)
            else:
                self.conn = oracledb.connect(dsn=dsn)
            self.conn.call_timeout = self.timeout_ms
        except oracledb.Error as e:
            if "DPY-3015" in str(e):
                raise oracledb.DatabaseError(
                    f"{e}\n"
                    "Tip: Database uses legacy Oracle 10g authentication. "
                    "Run 'db-grep driver oracle' to automatically download and configure the driver."
                ) from e
            raise

    def healthy(self):
        return self.conn.is_healthy()

    def _exec(self, sql, params):
        try:
            with self.conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return cols, cur.fetchmany(self.max_rows)
        except Exception as e:
            err_msg = str(e)
            if "ORA-00933" in err_msg and re.search(r"\blimit\s+\d+", sql, re.IGNORECASE):
                m = re.search(r"\blimit\s+(\d+)", sql, re.IGNORECASE)
                n = m.group(1) if m else "N"
                hint = (
                    f"\n💡 Hint: Oracle SQL does not support the 'LIMIT' clause. "
                    f"Use 'FETCH FIRST {n} ROWS ONLY' (Oracle 12c+) or 'WHERE ROWNUM <= {n}' instead."
                )
                raise type(e)(f"{err_msg}{hint}") from e
            raise
        finally:
            try:
                self.conn.rollback()
            except Exception:
                pass


from config_manager import DRIVER_ALIASES, DRIVER_SHORTHANDS, normalize_db_token

DRIVERS = {"postgres": Postgres, "oracle": Oracle, "mysql": MySQL}
conns: dict[str, DB] = {}


def resolve_db_keys(requested_name: str, cfgs: dict, allow_multiple: bool = False) -> list[str]:
    norm_name = normalize_db_token(requested_name)
    if norm_name in cfgs:
        return [norm_name]

    matches = [k for k in cfgs if k.endswith(f":{norm_name}")]
    if matches:
        if len(matches) > 1 and not allow_multiple:
            raise ValueError(
                f"Database name '{requested_name}' is ambiguous across multiple types: {', '.join(matches)}. "
                f"Specify the type prefix explicitly (e.g. '{matches[0]}')."
            )
        return matches

    available = list(dict.fromkeys(
        part
        for k in cfgs
        for part in ([k] + ([k.split(":", 1)[1]] if ":" in k else []))
    ))
    raise ValueError(f"db '{requested_name}' does not exist. Available choices: {', '.join(available)}")


def resolve_db_key(requested_name: str, cfgs: dict) -> str:
    return resolve_db_keys(requested_name, cfgs, allow_multiple=False)[0]


def resolve_db_keys_for_check(token: str, cfgs: dict) -> list[str]:
    return resolve_db_keys(token, cfgs, allow_multiple=True)


def get_db(name: str):
    cfgs = databases()  # reloaded on each call: config updates apply immediately
    try:
        resolved_key = resolve_db_key(name, cfgs)
    except ValueError as e:
        norm_name = normalize_db_token(name)
        # Check if the requested name matches a disabled database
        for k, err in DISABLED_DATABASES.items():
            if k == norm_name or k.endswith(f":{norm_name}"):
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

    async def notify(self, msg):
        """Send non-blocking event notification to monitor if connected."""
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write((json.dumps(msg, default=str) + "\n").encode())
                await self.writer.drain()
            except Exception:
                pass

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


def check_context(k):
    k = re.sub(r"[\x00-\x1f\x7f]", "", k or "").strip()[:80]
    if "·" not in k:
        raise ValueError("context must be in format '<project-name> · <action>', "
                         "example 'ecommerce · fetch product inventory'")
    return k

check_konteks = check_context


# ---------- approval gate ----------
async def execute(tool, d, context, sql, params=(), reject=None):
    context = check_context(context)

    # Evaluate access control rules
    allow_metadata = d.cfg.get("allow_metadata", ["read"])
    allow_data = d.cfg.get("allow_data", [])
    allow_tables = d.cfg.get("allow_tables", [])
    restricted_tables = d.cfg.get("restricted_tables", [])
    restricted_columns = d.cfg.get("restricted_columns", [])

    auto_allow = False
    auto_reason = None
    trigger_reason = None
    can_auto_allow = True
    candidate_tables: list[str] = []
    queried_tables: set[str] = set()
    queried_columns: set[str] = set()
    matched_restricted: list[str] = []

    if tool in ("list_tables", "find_objects", "search_columns"):
        if "read" in allow_metadata:
            auto_allow = True
            auto_reason = "allow_metadata permits 'read'"
        else:
            trigger_reason = "allow_metadata does not permit 'read'"
            can_auto_allow = False

    elif tool == "describe_table":
        table_name = params[1] if len(params) > 1 else ""
        if table_name:
            queried_tables = {table_name.lower()}

        if table_name and matches_pattern(table_name, restricted_tables):
            trigger_reason = f"restricted_table '{table_name}'"
            can_auto_allow = False
        elif "read" in allow_metadata:
            auto_allow = True
            auto_reason = "allow_metadata permits 'read'"
        elif allow_tables and matches_pattern(table_name, allow_tables):
            auto_allow = True
            auto_reason = f"table '{table_name}' matches allow_tables"
        else:
            trigger_reason = f"table '{table_name}' not in allow_tables"
            candidate_tables = [table_name] if table_name else []

    elif tool in ("query", "sample_table"):
        if tool == "sample_table" and "read" in allow_metadata:
            auto_allow = True
            auto_reason = "allow_metadata permits 'read'"
        else:
            queried_tables, queried_columns, has_star = inspect_query_access(
                sql, dialect=d.cfg.get("type", "postgres")
            )

            # Priority 1: Restricted columns (Highest priority)
            matched_restricted = check_restricted_columns(
                queried_columns, queried_tables, has_star, restricted_columns
            )
            if matched_restricted:
                trigger_reason = f"restricted column(s): {', '.join(matched_restricted)}"
                can_auto_allow = False

            # Priority 2: Restricted tables (High priority)
            elif any(matches_pattern(tbl, restricted_tables) for tbl in queried_tables):
                matched_restricted_tbls = [tbl for tbl in queried_tables if matches_pattern(tbl, restricted_tables)]
                trigger_reason = f"restricted_table(s): {', '.join(sorted(matched_restricted_tbls))}"
                can_auto_allow = False

            # Priority 3: allow_data or allow_tables
            elif "read" in allow_data:
                auto_allow = True
                auto_reason = "allow_data permits 'read'"
            elif queried_tables and all(matches_pattern(tbl, allow_tables) for tbl in queried_tables):
                auto_allow = True
                auto_reason = f"all tables ({', '.join(sorted(queried_tables))}) match allow_tables"
            else:
                unapproved = [tbl for tbl in queried_tables if not matches_pattern(tbl, allow_tables)]
                trigger_reason = f"unapproved table(s): {', '.join(sorted(unapproved))}" if unapproved else "query requires approval"
                candidate_tables = sorted(unapproved)

    async with lock:
        try:
            # Wait up to 25s if monitor is not running yet
            await link.ensure(timeout_sec=25)
        except (OSError, asyncio.TimeoutError):
            audit("denied", reason="monitor offline", db=d.name, context=context, sql=sql, tool=tool)
            raise PermissionError(
                f"MONITOR_OFFLINE: Approval monitor is not running.\n"
                f"Target Database: {d.name}\n"
                f"Requested Action: {context}\n"
                f"SQL: {sql}\n\n"
                f"The database monitor (`db-grep monitor`) must be opened in another terminal to review and approve this action."
            )

        msg = {
            "tool": tool,
            "db": d.name,
            "db_type": d.cfg.get("type", ""),
            "context": context,
            "sql": sql,
            "tables": sorted(queried_tables),
            "columns": sorted(queried_columns),
            "trigger_reason": trigger_reason,
            "restricted_matched": matched_restricted,
            "sensitive_matched": matched_restricted,
            "can_auto_allow": can_auto_allow,
            "candidate_tables": candidate_tables,
        }
        if reject:
            await link.send({"type": "blocked", "reason": reject, **msg})
            audit("blocked", reason=reject, tool=tool, db=d.name, context=context, sql=sql)
            raise ValueError(f"Rejected: {reject}")

        if auto_allow:
            audit("auto_allow", db=d.name, tool=tool, reason=auto_reason, sql=sql, context=context)
            await link.send({"type": "auto_allow", "reason": auto_reason, "params": params, **msg})
        else:
            audit("request", db=d.name, db_type=d.cfg.get("type", ""), context=context, tool=tool, sql=sql, params=params, trigger_reason=trigger_reason)
            await link.send({"type": "request", "params": params, **msg})
            line = await link.reader.readline()  # timeout is managed by monitor
            if not line:
                link.drop()
                audit("denied", reason="monitor disconnected", tool=tool, db=d.name, context=context, sql=sql)
                raise PermissionError("Monitor disconnected while waiting for approval")
            resp = json.loads(line)
            if not resp["approved"]:
                audit("denied", reason="user", tool=tool, db=d.name, context=context, sql=sql)
                raise PermissionError(
                    f"ACTION_DENIED_BY_USER: Query was rejected by user in terminal monitor.\n"
                    f"SQL: {sql}\n"
                    f"Context: {context}"
                )

            # Persist newly allowed tables to config.toml if user chose [a]lways allow
            add_tables = resp.get("add_allow_tables", resp.get("add_auto_tables", []))
            if add_tables:
                try:
                    import config_manager
                    db_target = f"{d.cfg.get('type')}:{d.name}"
                    config_manager.add_allow_tables(db_target, add_tables)
                    for t in add_tables:
                        if t not in d.cfg["allow_tables"] and t.lower() not in [x.lower() for x in d.cfg["allow_tables"]]:
                            d.cfg["allow_tables"].append(t)
                    audit("add_allow_tables", db=d.name, tables=add_tables)
                except Exception:
                    pass

        t0 = time.monotonic()
        try:
            cols, rows = await asyncio.to_thread(d.run, sql, params)
        except Exception as e:
            redacted_err = d.redact(str(e))
            audit("error", error=redacted_err, tool=tool, db=d.name, context=context, sql=sql)
            await link.send({"type": "error", "message": redacted_err})
            raise RuntimeError(redacted_err) from None

        ms = int((time.monotonic() - t0) * 1000)
        audit("executed", rows=len(rows), ms=ms, tool=tool, db=d.name, context=context, sql=sql)  # query data itself is never logged
        await link.send({"type": "result", "cols": cols, "rows": rows,
                         "ms": ms, "limit": d.max_rows})
        return json.dumps({"columns": cols, "rows": rows}, default=str)


# ---------- diagnostic and health inspection ----------
PING_QUERIES = {
    "postgres": "SELECT 1;",
    "oracle": "SELECT 1 FROM DUAL",
    "mysql": "SELECT 1;",
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
    has_raw = _config_has_raw_secrets(raw_dbs)
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

    eff_defaults = defaults if isinstance(defaults, dict) else {}
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
            cfg_norm, auth_source, errors = _validate_db_entry(driver_type, cfg, eff_defaults)
            db_report = {
                "name": db_name,
                "key": f"{driver_type}:{db_name}",
                "driver": driver_type,
                "description": cfg_norm.get("description", "") if cfg_norm else "",
                "allow_metadata": cfg_norm.get("allow_metadata", ["read"]) if cfg_norm else ["read"],
                "allow_data": cfg_norm.get("allow_data", []) if cfg_norm else [],
                "allow_tables": cfg_norm["allow_tables"] if cfg_norm and isinstance(cfg_norm.get("allow_tables"), list) else [],
                "restricted_tables": cfg_norm["restricted_tables"] if cfg_norm and isinstance(cfg_norm.get("restricted_tables"), list) else [],
                "restricted_columns": cfg_norm["restricted_columns"] if cfg_norm and isinstance(cfg_norm.get("restricted_columns"), list) else [],
                "valid": not errors,
                "errors": errors,
            }
            if auth_source:
                db_report["auth_source"] = auth_source
            if errors:
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


def execute_connection_check(target: str | list[str] = "all", all_dbs: dict | None = None) -> list[dict]:
    if all_dbs is None:
        all_dbs = databases()

    target_str = (" ".join(target) if isinstance(target, list) else (target or "all")).strip()
    if not target_str or target_str.lower() in ("all", "*"):
        targets_to_check = list(all_dbs.keys())
    else:
        targets_to_check = []
        tokens = [t.strip() for t in re.split(r"[,;\s]+", target_str) if t.strip()]
        for token in tokens:
            for resolved in resolve_db_keys_for_check(token, all_dbs):
                if resolved not in targets_to_check:
                    targets_to_check.append(resolved)

    results = [ping_database(key, all_dbs[key]) for key in targets_to_check]
    audit("check_connection", targets=targets_to_check, count=len(results),
          results=[{"db": r["db"], "type": r["type"], "status": r["status"], "latency_ms": r.get("latency_ms", 0)} for r in results])
    return results


# ---------- tools ----------
@mcp.tool()
async def check_config() -> str:
    """Validate config.toml format, permissions, required fields, and environment variables. Does not query databases."""
    report = inspect_config()
    audit("check_config", valid=report["valid"])
    await link.notify({"type": "event", "tool": "check_config", "valid": report["valid"]})
    return json.dumps(report, indent=2)


@mcp.tool()
async def check_connection(db: str = "all") -> str:
    """Check database physical connectivity and latency using explicit health-check queries ('SELECT 1;' for Postgres, 'SELECT 1 FROM DUAL' for Oracle). Parameter 'db' can be 'all', a single database name, or comma-separated names. Does not require interactive monitor approval."""
    results = await asyncio.to_thread(execute_connection_check, db)
    await link.notify({
        "type": "event",
        "tool": "check_connection",
        "targets": [db] if isinstance(db, str) else list(db),
        "count": len(results),
        "results": [{"db": r["db"], "type": r["type"], "status": r["status"], "latency_ms": r.get("latency_ms", 0)} for r in results],
    })
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
        shorthand_prefix = DRIVER_SHORTHANDS.get(c["type"])
        if shorthand_prefix:
            aliases.append(f"{shorthand_prefix}:{short}")
        
        item = {
            "db": effective_name,
            "type": c["type"],
            "description": c.get("description", ""),
        }
        if aliases:
            item["aliases"] = aliases
        out.append(item)

    dbs_summary = [item["db"] for item in out]
    audit("list_databases", count=len(out), databases=dbs_summary)
    await link.notify({"type": "event", "tool": "list_databases", "count": len(out), "databases": dbs_summary})
    return json.dumps(out)


@mcp.tool()
async def list_tables(db: DbName, context: Context, schema: str = "") -> str:
    """List table and view names in a schema (default: connection default schema). Returns columnar format {'columns': [...], 'rows': [...]}. Requires user approval."""
    d = get_db(db)
    return await execute("list_tables", d, context, d.LIST_SQL, (schema or d.default_schema,))


@mcp.tool()
async def find_objects(
    db: DbName,
    context: Context,
    pattern: str,
    schema: str = "",
    object_type: str = "ALL",
    limit: int = 50,
) -> str:
    """Search for tables, views, or synonyms matching a pattern/keyword (case-insensitive).
    Searches across all accessible schemas if schema is empty.
    Returns matching objects in columnar format {'columns': [...], 'rows': [...]}.
    Recommended over list_tables for searching specific tables/views or exploring enterprise databases."""
    d = get_db(db)
    sql, params = d.get_search_query(pattern, schema=schema, object_type=object_type, limit=limit)
    return await execute("find_objects", d, context, sql, params)


@mcp.tool()
async def search_columns(
    db: DbName,
    pattern: str,
    context: Context,
    schema: str = "",
    limit: int = 50,
) -> str:
    """Search for columns matching a pattern/keyword across tables and views (case-insensitive).
    Searches across all accessible schemas if schema is empty.
    Returns matching columns in columnar format {'columns': [...], 'rows': [...]}.
    Ideal for finding which tables store a specific column (e.g. '*CIF*', 'NIK', 'KODE_CABANG')."""
    d = get_db(db)
    sql, params = d.get_search_columns_query(pattern, schema=schema, limit=limit)
    return await execute("search_columns", d, context, sql, params)


@mcp.tool()
async def describe_table(
    db: DbName,
    table: str,
    context: Context,
    schema: str = "",
    compact: bool = False,
) -> str:
    """Columns, data types, and nullability of a table or view. Supports 'schema.table' format.
    Set compact=True to return concise string array ['COL: TYPE', ...] saving ~65% tokens on wide tables.
    Returns object status (VALID/INVALID) and helpful diagnostic hints if invalid. Requires user approval."""
    d = get_db(db)
    table_clean = table.strip().strip('"').strip("'")
    target_schema = schema.strip()
    if "." in table_clean and not target_schema:
        parts = table_clean.split(".", 1)
        target_schema = parts[0].strip().strip('"').strip("'")
        table_clean = parts[1].strip().strip('"').strip("'")

    res_json = await execute("describe_table", d, context, d.DESC_SQL, (target_schema or d.default_schema, table_clean))
    try:
        raw_rows = json.loads(res_json)
    except Exception:
        return res_json

    if isinstance(raw_rows, dict) and "columns" in raw_rows and "rows" in raw_rows:
        cols = [c.lower() for c in raw_rows["columns"]]
        raw_rows = [dict(zip(cols, r)) for r in raw_rows["rows"]]
    elif not isinstance(raw_rows, list):
        return res_json

    target_name = f"{target_schema}.{table_clean}" if target_schema else table_clean
    if not raw_rows:
        return json.dumps({"table": target_name, "status": "NOT_FOUND", "columns": []})

    # Detect object status and any compilation issues
    status = "VALID"
    error_hint = None
    cols_out = []

    for r in raw_rows:
        # Normalize keys regardless of DB casing
        r_low = {k.lower(): v for k, v in r.items()}
        col_name = r_low.get("column_name") or r_low.get("name") or ""
        col_type = r_low.get("data_type") or r_low.get("type") or ""
        nullable = r_low.get("is_nullable") or r_low.get("nullable") or ""
        row_status = r_low.get("status")

        if (row_status and str(row_status).upper() == "INVALID") or col_type.upper() == "UNDEFINED":
            status = "INVALID"

        if compact:
            cols_out.append(f"{col_name}: {col_type}")
        else:
            col_def = {"name": col_name, "type": col_type}
            if nullable:
                col_def["nullable"] = nullable
            cols_out.append(col_def)

    if status == "INVALID":
        error_hint = "View has compilation errors in database (status: INVALID). Querying this view will fail with ORA-04063."

    out = {
        "table": target_name,
        "status": status,
    }
    if error_hint:
        out["error_hint"] = error_hint
    out["columns"] = cols_out

    return json.dumps(out)


@mcp.tool()
async def sample_table(
    db: DbName,
    table: str,
    context: Context,
    limit: int = 5,
    schema: str = "",
) -> str:
    """Quick preview of 1-50 rows from a table or view (default: 5 rows).
    Automatically generates dialect-specific limit queries (LIMIT / ROWNUM).
    Returns rows in columnar format {'columns': [...], 'rows': [...]} for token efficiency.
    Ideal for inspecting real data values and column formats without writing manual queries."""
    d = get_db(db)
    sql, params = d.get_sample_query(table, limit=limit, schema=schema)
    return await execute("sample_table", d, context, sql, params)


@mcp.tool()
async def query(db: DbName, sql: str, context: Context) -> str:
    """Execute read-only SELECT query, rows capped per connection.
    Returns tabular data in columnar format {'columns': [...], 'rows': [...]} for 50-70% token efficiency.
    Requires user approval."""
    d = get_db(db)
    reject = None if sql.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
    return await execute("query", d, context, sql, (), reject)


def start():
    try:
        databases()  # invalid config fails fast on startup
    except ValueError as e:
        sys.exit(str(e))
    mcp.run()


if __name__ == "__main__":
    start()
