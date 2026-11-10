import asyncio, json, os, re, time, tomllib
from datetime import datetime
from pathlib import Path

CONFIG = Path(os.environ.get("DB_MCP_CONFIG", "~/.config/db-mcp/config.toml")).expanduser()
try:
    with open(CONFIG, "rb") as f:
        _cfg = tomllib.load(f)
except FileNotFoundError:
    _cfg = {}
PORT = int(os.environ.get("MONITOR_PORT") or _cfg.get("monitor", {}).get("port", 8765))

PROMPT_TIMEOUT = 110  # detik; habis = otomatis ditolak
R, G, Y, D, N = "\033[31m", "\033[32m", "\033[33m", "\033[2m", "\033[0m"
COLORS = ["\033[36m", "\033[35m", "\033[34m", "\033[92m", "\033[95m"]
lock = asyncio.Lock()  # satu prompt pada satu waktu, lintas sesi
sessions = set()

clean = lambda s: re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(s))  # buang escape code dari AI
tag = lambda n: f"{COLORS[ord(n[0]) % len(COLORS)]}[{n}]{N}"


def new_name():
    i = 0
    while (n := chr(65 + i) if i < 26 else f"S{i}") in sessions:
        i += 1
    return n


def getch(timeout):  # satu tombol tanpa Enter; None = timeout
    try:
        import msvcrt
    except ImportError:
        import select, sys, termios, tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            return sys.stdin.read(1) if ready else None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if msvcrt.kbhit():
            return msvcrt.getwch()
        time.sleep(0.05)
    return None


def show_table(cols, rows):
    cell = lambda v: clean(v).replace("\n", " ")[:40]
    w = [max([len(cell(c))] + [len(cell(r[i])) for r in rows]) for i, c in enumerate(cols)]
    line = lambda vals: "  ".join(cell(v).ljust(w[i]) for i, v in enumerate(vals))
    print(line(cols))
    print("  ".join("-" * x for x in w))
    for r in rows:
        print(line(r))


async def reply(writer, msg):
    try:
        writer.write((json.dumps(msg) + "\n").encode())
        await writer.drain()
    except OSError:
        pass  # server sudah putus; jawaban dibuang


async def handle(reader, writer):
    name = new_name()
    sessions.add(name)
    t = tag(name)
    print(f"{G}+ Sesi {name} terhubung{N}")
    await reply(writer, {"type": "hello", "session": name})
    holding = False  # lock ditahan dari 'request' sampai 'result'/'error'
    try:
        while line := await reader.readline():
            m = json.loads(line)
            now = datetime.now().strftime("%H:%M:%S")
            db_label = m.get("db", "")
            if db_label and m.get("db_type"):
                db_type = "pgsql" if m["db_type"] == "postgres" else m["db_type"]
                db = f" @ {clean(db_type)}:{clean(db_label)}"
            elif db_label:
                db = f" @ {clean(db_label)}"
            else:
                db = ""
            head = f"\n{Y}━━ {now}{N} {t} {Y}{m.get('tool', '')}{db}"

            if m["type"] == "request":
                await lock.acquire()
                holding = True
                print(f"{head} ━━{N}\n{D}{clean(m['konteks'])}{N}\n{clean(m['sql'])}")
                if m["params"]:
                    print(f"{D}params: {clean(m['params'])}{N}")
                print("Izinkan? [y/n] ", end="", flush=True)
                key = await asyncio.to_thread(getch, PROMPT_TIMEOUT)
                ok = key is not None and key.lower() == "y"  # selain y = tolak
                print(f"{G}diizinkan{N}" if ok else f"{R}{'kedaluwarsa' if key is None else 'ditolak'}{N}")
                await reply(writer, {"approved": ok})
                if not ok:
                    lock.release()
                    holding = False

            elif m["type"] == "result":
                n = len(m["rows"])
                print(f"{t} {D}{n} baris, {m['ms']} ms, semuanya dikirim ke AI{N}")
                show_table(m["cols"], m["rows"])
                if n >= m["limit"]:
                    print(f"{Y}terpotong di {m['limit']} baris{N}")
                if holding:
                    lock.release()
                    holding = False

            elif m["type"] == "error":
                print(f"{t} {R}error DB: {clean(m['message'])}{N}")
                if holding:
                    lock.release()
                    holding = False

            elif m["type"] == "blocked":
                async with lock:
                    print(f"{head} DIBLOKIR ({clean(m['reason'])}) ━━{N}\n{D}{clean(m['konteks'])}{N}\n{clean(m['sql'])}")
    finally:
        if holding:
            lock.release()
        sessions.discard(name)
        print(f"{R}- Sesi {name} terputus{N}")
        writer.close()


async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", PORT)
    print(f"Monitor siap di 127.0.0.1:{PORT}. Menunggu sesi Claude...")
    async with srv:
        await srv.serve_forever()


def start():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    start()
