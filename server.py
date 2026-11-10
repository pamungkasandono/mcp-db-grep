import asyncio, json, logging, os, re, sys, time, tomllib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from pydantic import Field
from mcp.server.fastmcp import FastMCP

logging.basicConfig(stream=sys.stderr)  # stdout dipakai protokol stdio
log = logging.getLogger("db-grep")

CONFIG = Path(os.environ.get("DB_GREP_CONFIG", os.environ.get("DB_MCP_CONFIG", "~/.config/db-grep/config.toml"))).expanduser()
AUDIT = Path(os.environ.get("AUDIT_LOG", CONFIG.parent / "audit.jsonl")).expanduser()

Konteks = Annotated[str, Field(
    description="Wajib. Format '<nama-project> · <aksi>', contoh 'bekal-akun · ambil sample data siswa'")]
DbName = Annotated[str, Field(description="Nama koneksi database. Lihat list_databases.")]


# ---------- config ----------
def read_config():
    if not CONFIG.exists():
        raise ValueError(f"Config tidak ditemukan: {CONFIG}")
    if os.name == "posix" and CONFIG.stat().st_mode & 0o077:
        log.warning("Jalankan: chmod 600 %s (file berisi kredensial)", CONFIG)
    with open(CONFIG, "rb") as f:
        return tomllib.load(f)


def databases():
    dbs = read_config().get("db", {})
    if not dbs:
        raise ValueError(f"Tidak ada [db.<nama>] di {CONFIG}")
    for name, c in dbs.items():
        if c.get("type") not in DRIVERS:
            raise ValueError(f"[db.{name}] type harus salah satu: {', '.join(DRIVERS)}")
        for key in ("dsn", "user") if c["type"] == "oracle" else ("dsn",):
            if not c.get(key):
                raise ValueError(f"[db.{name}] wajib punya '{key}'")
    return dbs


def password(c):
    if c.get("password"):
        return c["password"]
    env = c.get("password_env")
    if env and env not in os.environ:
        raise ValueError(f"Env var {env} belum diset (dibutuhkan koneksi ini)")
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
                raise  # error SQL biasa
            self.close()  # koneksi mati: sambung ulang, coba sekali lagi
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
                cur.execute("SET TRANSACTION READ ONLY")  # lapisan tambahan; grant SELECT tetap utama
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
    cfgs = databases()  # dibaca ulang: perubahan config langsung berlaku
    if name not in cfgs:
        raise ValueError(f"db '{name}' tidak ada. Pilihan: {', '.join(cfgs)}")
    cur = conns.get(name)
    if cur is None or cur.cfg != cfgs[name]:
        if cur:
            cur.close()
        cur = conns[name] = DRIVERS[cfgs[name]["type"]](name, cfgs[name])
    return cur


# ---------- koneksi ke monitor (satu per sesi Claude) ----------
class Link:
    reader = writer = None
    session = "?"

    async def ensure(self):
        if self.writer and not self.writer.is_closing() and not self.reader.at_eof():
            return
        port = int(os.environ.get("MONITOR_PORT") or read_config().get("monitor", {}).get("port", 8765))
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", port)
        self.session = json.loads(await asyncio.wait_for(self.reader.readline(), 5))["session"]

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
        await link.ensure()  # daftar ke monitor begitu Claude dibuka
    except (OSError, asyncio.TimeoutError, ValueError):
        pass  # monitor belum nyala: coba lagi di request pertama
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
        raise ValueError("konteks wajib berformat '<nama-project> · <aksi>', "
                         "contoh 'bekal-akun · ambil sample data siswa'")
    return k


# ---------- gerbang persetujuan ----------
async def execute(tool, d, konteks, sql, params=(), reject=None):
    konteks = check_konteks(konteks)
    async with lock:
        try:
            await link.ensure()  # menyambung ulang otomatis kalau monitor sempat restart
        except (OSError, asyncio.TimeoutError):
            audit("denied", reason="monitor offline", db=d.name, konteks=konteks, sql=sql)
            raise RuntimeError("Monitor offline. Jalankan `python monitor.py` di terminal lain.")

        audit("request", db=d.name, db_type=d.cfg.get("type", ""), konteks=konteks, tool=tool, sql=sql, params=params)
        try:
            msg = {"tool": tool, "db": d.name, "db_type": d.cfg.get("type", ""), "konteks": konteks, "sql": sql}
            if reject:
                await link.send({"type": "blocked", "reason": reject, **msg})
                audit("blocked", reason=reject)
                raise ValueError(f"Ditolak: {reject}")

            await link.send({"type": "request", "params": params, **msg})
            line = await link.reader.readline()  # kedaluwarsa ditentukan monitor
            if not line:
                link.drop()
                audit("denied", reason="monitor terputus")
                raise PermissionError("Monitor terputus saat menunggu persetujuan")
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
            audit("executed", rows=len(rows), ms=ms)  # isi data sengaja tidak di-log
            await link.send({"type": "result", "cols": cols, "rows": rows,
                             "ms": ms, "limit": d.max_rows})
            return json.dumps([dict(zip(cols, r)) for r in rows], default=str)
        except (asyncio.CancelledError, ConnectionError):
            link.drop()  # jawaban basi tidak boleh terbaca request berikutnya
            raise


# ---------- tools ----------
@mcp.tool()
async def list_databases() -> str:
    """Daftar koneksi database yang tersedia (nama, tipe, deskripsi). Tidak mengakses DB."""
    out = [{"db": n, "type": c["type"], "description": c.get("description", "")}
           for n, c in databases().items()]
    audit("list_databases")
    return json.dumps(out)


@mcp.tool()
async def list_tables(db: DbName, konteks: Konteks, schema: str = "") -> str:
    """Daftar tabel di sebuah schema (default: schema bawaan koneksi). Perlu persetujuan user."""
    d = get_db(db)
    return await execute("list_tables", d, konteks, d.LIST_SQL, (schema or d.default_schema,))


@mcp.tool()
async def describe_table(db: DbName, table: str, konteks: Konteks, schema: str = "") -> str:
    """Kolom, tipe data, dan nullable sebuah tabel. Perlu persetujuan user."""
    d = get_db(db)
    return await execute("describe_table", d, konteks, d.DESC_SQL, (schema or d.default_schema, table))


@mcp.tool()
async def query(db: DbName, sql: str, konteks: Konteks) -> str:
    """Jalankan SELECT read-only, baris dibatasi per koneksi. Perlu persetujuan user."""
    d = get_db(db)
    reject = None if sql.lstrip().lower().startswith(("select", "with")) else "bukan SELECT/WITH"
    return await execute("query", d, konteks, sql, (), reject)


def start():
    try:
        databases()  # config rusak = gagal jelas saat start
    except ValueError as e:
        sys.exit(str(e))
    mcp.run()


if __name__ == "__main__":
    start()
