import asyncio, json, os, re, sys, time, tomllib
from datetime import datetime
from pathlib import Path

DEFAULT_PORT = 48260
PORT_RANGE_SIZE = 10

CONFIG = Path(os.environ.get("DB_GREP_CONFIG", os.environ.get("DB_MCP_CONFIG", "~/.config/db-grep/config.toml"))).expanduser()
STATE_PORT_FILE = CONFIG.parent / "monitor.port"

try:
    with open(CONFIG, "rb") as f:
        _cfg = tomllib.load(f)
except Exception:
    _cfg = {}
PREFERRED_PORT = int(os.environ.get("MONITOR_PORT") or _cfg.get("monitor", {}).get("port", DEFAULT_PORT))
PROMPT_TIMEOUT = int(os.environ.get("APPROVAL_TIMEOUT_S") or _cfg.get("monitor", {}).get("approval_timeout_s", 110))

R, G, Y, D, N = "\033[31m", "\033[32m", "\033[33m", "\033[2m", "\033[0m"
COLORS = ["\033[36m", "\033[35m", "\033[34m", "\033[92m", "\033[95m"]
lock = asyncio.Lock()  # one prompt or active output block at a time across sessions
stdin_lock = asyncio.Lock()  # prevent concurrent stdin access between prompt and background hotkey
prompt_active = False  # True while waiting for prompt/menu user input
sessions = set()
auto_approved_sessions: dict[str, str] = {}  # {session_name: last_db_display}

clean = lambda s: re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(s))  # strip escape codes
tag = lambda n: f"{COLORS[ord(n[0]) % len(COLORS)]}[{n}]{N}"


def new_name():
    i = 0
    while (n := chr(65 + i) if i < 26 else f"S{i}") in sessions:
        i += 1
    return n


def parse_selection_tokens(user_input: str, total_items: int) -> list[int]:
    selected_indices = []
    tokens = [t.strip() for t in user_input.replace(";", " ").replace(",", " ").split() if t.strip()]

    for token in tokens:
        if "-" in token:
            parts = token.split("-", 1)
            if parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]), int(parts[1])
                if start > end:
                    start, end = end, start
                for idx in range(start, end + 1):
                    if 1 <= idx <= total_items:
                        selected_indices.append(idx - 1)
        elif token.isdigit():
            idx = int(token)
            if 1 <= idx <= total_items:
                selected_indices.append(idx - 1)

    return list(dict.fromkeys(selected_indices))


def getch(timeout):  # single keypress without enter; None = timeout
    try:
        import msvcrt
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if msvcrt.kbhit():
                return msvcrt.getwch()
            time.sleep(0.05)
        return None
    except ImportError:
        import select, termios, tty
        try:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
        except Exception:
            return None
        try:
            tty.setraw(fd)
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            return sys.stdin.read(1) if ready else None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_line(prompt_text: str) -> str | None:
    try:
        return input(prompt_text)
    except (EOFError, KeyboardInterrupt):
        return None


def print_auto_approve_banner():
    if not auto_approved_sessions:
        return
    items = [
        f"{tag(s)}{D}({db}){N}" if db else tag(s)
        for s, db in auto_approved_sessions.items()
    ]
    summary = ", ".join(items[:6])
    if len(items) > 6:
        summary += f" +{len(items) - 6} more"
    print(f"{G}⚡ AUTO-APPROVE ON:{N} {summary} {D}— press [l] anytime to lock{N}", flush=True)


async def lock_sessions_interactive():
    global prompt_active
    active_list = list(auto_approved_sessions.items())
    count = len(active_list)
    if count == 0:
        print(f"\n{D}No sessions currently have auto-approve enabled.{N}", flush=True)
        return

    # Case 1: Exactly 1 active session -> lock immediately without extra prompt
    if count == 1:
        sess_name, _ = active_list[0]
        auto_approved_sessions.pop(sess_name, None)
        print(f"\n{Y}🔒 Locked auto-approve for {tag(sess_name)} — manual [y/n] approval restored.{N}", flush=True)
        return

    prompt_active = True
    try:
        # Case 2: 2 to 4 active sessions (< 5 connections) -> direct single-key selection
        if count < 5:
            all_single_char = all(len(s) == 1 for s, _ in active_list)
            if all_single_char:
                choices_disp = " / ".join(
                    f"{tag(s)}{D}({db}){N}" if db else tag(s) for s, db in active_list
                )
                keys_hint = "/".join(s for s, _ in active_list)
                async with stdin_lock:
                    print(
                        f"\n{Y}Lock auto-approve session:{N} {choices_disp}\n"
                        f"Press [{keys_hint}] to lock one, [*/a/Enter] to lock ALL, or [Esc/q] to cancel: ",
                        end="",
                        flush=True,
                    )
                    key = await asyncio.to_thread(getch, PROMPT_TIMEOUT)
                if key is None or key in ("\x1b", "q", "Q"):
                    print(f"{D}cancelled{N}", flush=True)
                    return
                if key in ("*", "a", "A", "\r", "\n"):
                    locked = [s for s, _ in active_list]
                    auto_approved_sessions.clear()
                    print(f"{Y}🔒 Locked ALL auto-approve sessions ({', '.join(locked)}).{N}", flush=True)
                    return
                matched = next((s for s, _ in active_list if s.lower() == key.lower()), None)
                if matched:
                    auto_approved_sessions.pop(matched, None)
                    print(f"{Y}🔒 Locked auto-approve for {tag(matched)}.{N}", flush=True)
                    print_auto_approve_banner()
                else:
                    print(f"{D}cancelled (unrecognized key){N}", flush=True)
                return
            else:
                # Fallback if any of the <5 sessions has a multi-char name like S27: single-key numeric 1..4
                choices_disp = "  ".join(
                    f"[{idx}] {tag(s)}{D}({db}){N}" if db else f"[{idx}] {tag(s)}"
                    for idx, (s, db) in enumerate(active_list, start=1)
                )
                async with stdin_lock:
                    print(
                        f"\n{Y}Lock auto-approve session:{N} {choices_disp}\n"
                        f"Press [1-{count}] to lock one, [*/a/Enter] to lock ALL, or [Esc/q] to cancel: ",
                        end="",
                        flush=True,
                    )
                    key = await asyncio.to_thread(getch, PROMPT_TIMEOUT)
                if key is None or key in ("\x1b", "q", "Q"):
                    print(f"{D}cancelled{N}", flush=True)
                    return
                if key in ("*", "a", "A", "\r", "\n"):
                    locked = [s for s, _ in active_list]
                    auto_approved_sessions.clear()
                    print(f"{Y}🔒 Locked ALL auto-approve sessions ({', '.join(locked)}).{N}", flush=True)
                    return
                if key.isdigit() and 1 <= int(key) <= count:
                    target_sess = active_list[int(key) - 1][0]
                    auto_approved_sessions.pop(target_sess, None)
                    print(f"{Y}🔒 Locked auto-approve for {tag(target_sess)}.{N}", flush=True)
                    print_auto_approve_banner()
                else:
                    print(f"{D}cancelled (unrecognized key){N}", flush=True)
                return

        # Case 3: >= 5 active sessions -> numbered list + multi-select input
        async with stdin_lock:
            print(f"\n{Y}--- Active Auto-Approve Sessions ({count}) ---{N}")
            for idx, (s, db) in enumerate(active_list, start=1):
                db_info = f" (last db: {db})" if db else ""
                print(f"  [{idx}] Session {s:<4}{D}{db_info}{N}")
            print(f"  [a] Lock ALL auto-approve sessions")
            print(f"  [q] Cancel")

            choice = await asyncio.to_thread(
                read_line,
                f"\nSelect session(s) to lock [default: all, e.g. 1, 1,3, 1-{count}, or 'all']: ",
            )
        if choice is None or choice.strip().lower() in ("q", "quit", "exit"):
            print(f"{D}Cancelled.{N}", flush=True)
            return

        choice_str = choice.strip()
        if not choice_str or choice_str.lower() in ("a", "all", "*"):
            locked = [s for s, _ in active_list]
            auto_approved_sessions.clear()
            print(f"{Y}🔒 Locked ALL auto-approve sessions ({', '.join(locked)}).{N}", flush=True)
            return

        indices = parse_selection_tokens(choice_str, count)
        if not indices:
            print(f"{R}No valid session index selected.{N}", flush=True)
            return

        locked = []
        for idx in indices:
            sess_name = active_list[idx][0]
            if sess_name in auto_approved_sessions:
                auto_approved_sessions.pop(sess_name, None)
                locked.append(sess_name)
        print(f"{Y}🔒 Locked auto-approve for: {', '.join(locked)}.{N}", flush=True)
        print_auto_approve_banner()
    finally:
        prompt_active = False


async def keyboard_listener():
    """Background task to listen for [l] lock hotkey when no prompt is active."""
    while True:
        if prompt_active or not auto_approved_sessions or lock.locked() or stdin_lock.locked():
            await asyncio.sleep(0.08)
            continue

        async with stdin_lock:
            if prompt_active or not auto_approved_sessions or lock.locked():
                continue
            key = await asyncio.to_thread(getch, 0.08)

        if key is not None and key.lower() == "l":
            async with lock:
                await lock_sessions_interactive()
        else:
            await asyncio.sleep(0.02)


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
        pass  # server already disconnected; drop reply


async def handle(reader, writer):
    global prompt_active
    name = new_name()
    sessions.add(name)
    t = tag(name)
    print(f"{G}+ Session {name} connected{N}")
    await reply(writer, {"type": "hello", "session": name})
    holding = False  # hold lock from 'request' until 'result'/'error'
    try:
        while line := await reader.readline():
            m = json.loads(line)
            now = datetime.now().strftime("%H:%M:%S")
            db_label = m.get("db", "")
            db_short = ""
            if db_label:
                # If db_label already contains type prefix (e.g. 'postgres:ecommerce-dev'), normalize display
                if ":" in db_label:
                    d_type, d_name = db_label.split(":", 1)
                    disp_type = "pgsql" if d_type in ("postgres", "pgsql") else d_type
                    db_short = f"{clean(disp_type)}:{clean(d_name)}"
                elif m.get("db_type"):
                    disp_type = "pgsql" if m["db_type"] in ("postgres", "pgsql") else m["db_type"]
                    db_short = f"{clean(disp_type)}:{clean(db_label)}"
                else:
                    db_short = clean(db_label)
                db = f" @ {db_short}"
            else:
                db = ""
            head = f"\n{Y}━━ {now}{N} {t} {Y}{m.get('tool', '')}{db}"

            if m["type"] == "request":
                await lock.acquire()
                holding = True

                if name in auto_approved_sessions:
                    if db_short:
                        auto_approved_sessions[name] = db_short
                    print(f"{head} {G}[AUTO-APPROVED: session {name}]{N} ━━{N}\n{D}{clean(m['konteks'])}{N}\n{clean(m['sql'])}")
                    if m.get("params"):
                        print(f"{D}params: {clean(m['params'])}{N}")
                    await reply(writer, {"approved": True, "auto_session": True})
                else:
                    prompt_active = True
                    try:
                        async with stdin_lock:
                            print(f"{head} ━━{N}\n{D}{clean(m['konteks'])}{N}\n{clean(m['sql'])}")
                            if m.get("params"):
                                print(f"{D}params: {clean(m['params'])}{N}")
                            print("Allow? [y]es once / [a]uto-approve session / [n]o: ", end="", flush=True)
                            key = await asyncio.to_thread(getch, PROMPT_TIMEOUT)
                    finally:
                        prompt_active = False

                    k_low = key.lower() if key is not None else ""
                    if k_low == "y":
                        ok = True
                        auto_sess = False
                        print(f"{G}allowed (once){N}")
                    elif k_low == "a":
                        ok = True
                        auto_sess = True
                        auto_approved_sessions[name] = db_short
                        print(f"{G}auto-approve enabled for {t} (press 'l' anytime to lock){N}")
                    else:
                        ok = False
                        auto_sess = False
                        print(f"{R}{'timeout' if key is None else 'denied'}{N}")

                    await reply(writer, {"approved": ok, "auto_session": auto_sess})
                    if not ok:
                        print_auto_approve_banner()
                        lock.release()
                        holding = False

            elif m["type"] == "auto_allow":
                async with lock:
                    reason_str = f" {G}({clean(m.get('reason', 'auto_allow'))}){N}" if m.get("reason") else ""
                    print(f"{head} {G}[AUTO-ALLOWED]{N} ━━{N}\n{D}{clean(m['konteks'])}{reason_str}{N}\n{clean(m['sql'])}")
                    if m.get("params"):
                        print(f"{D}params: {clean(m['params'])}{N}")

            elif m["type"] == "result":
                n = len(m["rows"])
                print(f"{t} {D}{n} rows, {m['ms']} ms, sent to AI{N}")
                show_table(m["cols"], m["rows"])
                if n >= m["limit"]:
                    print(f"{Y}capped at {m['limit']} rows{N}")
                print_auto_approve_banner()
                if holding:
                    lock.release()
                    holding = False

            elif m["type"] == "error":
                print(f"{t} {R}DB error: {clean(m['message'])}{N}")
                print_auto_approve_banner()
                if holding:
                    lock.release()
                    holding = False

            elif m["type"] == "blocked":
                async with lock:
                    print(f"{head} BLOCKED ({clean(m['reason'])}) ━━{N}\n{D}{clean(m['konteks'])}{N}\n{clean(m['sql'])}")
                    print_auto_approve_banner()
    finally:
        if holding:
            lock.release()
        sessions.discard(name)
        was_auto = auto_approved_sessions.pop(name, None) is not None
        extra = f" {D}(auto-approve cleared){N}" if was_auto else ""
        print(f"{R}- Session {name} disconnected{N}{extra}")
        writer.close()


def write_state_port(port: int):
    STATE_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Atomic write / replace existing file
        tmp = STATE_PORT_FILE.with_suffix(".tmp")
        tmp.write_text(str(port), encoding="utf-8")
        tmp.replace(STATE_PORT_FILE)
    except Exception:
        STATE_PORT_FILE.write_text(str(port), encoding="utf-8")


def remove_state_port():
    try:
        if STATE_PORT_FILE.exists():
            STATE_PORT_FILE.unlink()
    except Exception:
        pass


async def main():
    srv = None
    bound_port = None
    
    # Try preferred port, then fallback across range
    ports_to_try = [PREFERRED_PORT] + [
        p for p in range(DEFAULT_PORT, DEFAULT_PORT + PORT_RANGE_SIZE) if p != PREFERRED_PORT
    ]
    
    for port in ports_to_try:
        try:
            srv = await asyncio.start_server(handle, "127.0.0.1", port)
            bound_port = port
            break
        except OSError:
            continue

    if srv is None:
        print(f"{R}Error: Could not bind to any monitor port in range {DEFAULT_PORT}-{DEFAULT_PORT + PORT_RANGE_SIZE - 1}{N}")
        return

    # Overwrite / replace any previous or stale monitor.port
    write_state_port(bound_port)

    if bound_port != PREFERRED_PORT:
        print(f"{Y}Note: Preferred port {PREFERRED_PORT} was busy. Fallback to {bound_port}.{N}")
    print(f"Monitor running on 127.0.0.1:{bound_port}. Waiting for client session...")

    kb_task = asyncio.create_task(keyboard_listener())
    try:
        async with srv:
            await srv.serve_forever()
    finally:
        kb_task.cancel()
        remove_state_port()


def start():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        remove_state_port()


if __name__ == "__main__":
    start()
