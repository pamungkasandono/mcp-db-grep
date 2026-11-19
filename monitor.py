import asyncio, json, os, re, sys, time, tomllib
from datetime import datetime
from pathlib import Path

DEFAULT_PORT = 48260
PORT_RANGE_SIZE = 10

CONFIG = Path(os.environ.get("DB_GREP_CONFIG", os.environ.get("DB_MCP_CONFIG", "~/.config/db-grep/config.toml"))).expanduser()
STATE_PORT_FILE = CONFIG.parent / "monitor.port"
AUDIT = Path(os.environ.get("AUDIT_LOG", CONFIG.parent / "audit.jsonl")).expanduser()

DEFAULT_HISTORY_LIMIT = 20

try:
    with open(CONFIG, "rb") as f:
        _cfg = tomllib.load(f)
except Exception:
    _cfg = {}
PREFERRED_PORT = int(os.environ.get("MONITOR_PORT") or _cfg.get("monitor", {}).get("port", DEFAULT_PORT))
PROMPT_TIMEOUT = int(os.environ.get("APPROVAL_TIMEOUT_S") or _cfg.get("monitor", {}).get("approval_timeout_s", 110))
HISTORY_LIMIT = int(os.environ.get("MONITOR_HISTORY_LIMIT") or _cfg.get("monitor", {}).get("history_limit", DEFAULT_HISTORY_LIMIT))

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

                ctx = clean(m.get("context") or m.get("konteks", ""))
                tables = m.get("tables", [])
                columns = m.get("columns", [])
                trigger_reason = m.get("trigger_reason")
                can_auto_allow = m.get("can_auto_allow", True)
                candidate_tables = m.get("candidate_tables", [])

                prompt_active = True
                try:
                    async with stdin_lock:
                        print(f"{head} ━━{N}\n{D}{ctx}{N}\n{clean(m['sql'])}")
                        if m.get("params"):
                            print(f"{D}params: {clean(m['params'])}{N}")
                        if tables:
                            print(f"{D}Tables  : {', '.join(tables)}{N}")
                        if columns:
                            col_summary = ", ".join(columns[:10]) + ("..." if len(columns) > 10 else "")
                            print(f"{D}Columns : {col_summary}{N}")
                        if trigger_reason:
                            reason_color = R if not can_auto_allow else Y
                            print(f"{reason_color}Reason  : {trigger_reason}{N}")

                        if can_auto_allow and candidate_tables:
                            cand_label = f"'{candidate_tables[0]}'" if len(candidate_tables) == 1 else f"{len(candidate_tables)} table(s)"
                            prompt_str = f"Allow? [y]es once / [a]lways allow {cand_label} / [n]o: "
                        elif not can_auto_allow:
                            prompt_str = "Allow? [y]es once / [n]o (auto-allow disabled: sensitive data): "
                        else:
                            prompt_str = "Allow? [y]es once / [n]o: "

                        print(prompt_str, end="", flush=True)
                        key = await asyncio.to_thread(getch, PROMPT_TIMEOUT)
                finally:
                    prompt_active = False

                k_low = key.lower() if key is not None else ""
                add_tables = []
                if k_low == "y":
                    ok = True
                    print(f"{G}allowed (once){N}")
                elif k_low == "a" and can_auto_allow and candidate_tables:
                    ok = True
                    add_tables = candidate_tables
                    cand_label = f"'{candidate_tables[0]}'" if len(candidate_tables) == 1 else f"{len(candidate_tables)} table(s)"
                    print(f"{G}allowed and added {cand_label} to auto_tables in config.toml{N}")
                elif k_low == "a" and not can_auto_allow:
                    ok = False
                    print(f"{R}denied (cannot auto-allow sensitive tables or columns){N}")
                else:
                    ok = False
                    print(f"{R}{'timeout' if key is None else 'denied'}{N}")

                resp_payload = {"approved": ok, "auto_session": False}
                if add_tables and ok:
                    resp_payload["add_auto_tables"] = add_tables
                await reply(writer, resp_payload)
                if not ok:
                    lock.release()
                    holding = False

            elif m["type"] == "auto_allow":
                async with lock:
                    ctx = clean(m.get("context") or m.get("konteks", ""))
                    reason_str = f" {G}({clean(m.get('reason', 'auto_allow'))}){N}" if m.get("reason") else ""
                    print(f"{head} {G}[AUTO-ALLOWED]{N} ━━{N}\n{D}{ctx}{reason_str}{N}\n{clean(m['sql'])}")
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
                    ctx = clean(m.get("context") or m.get("konteks", ""))
                    print(f"{head} BLOCKED ({clean(m['reason'])}) ━━{N}\n{D}{ctx}{N}\n{clean(m['sql'])}")
                    print_auto_approve_banner()

            elif m["type"] == "event":
                async with lock:
                    tool = m.get("tool", "")
                    if tool == "check_connection":
                        targets = ", ".join(m.get("targets", []))
                        print(f"{head} {D}(targets: {targets}){N} ━━")
                        for r in m.get("results", []):
                            icon = f"{G}✓{N}" if r.get("status") == "connected" else f"{R}✗{N}"
                            lat = f"{r.get('latency_ms', 0)} ms"
                            print(f"  {icon} {r.get('db', ''):<25} [{r.get('type', '')}] {r.get('status')} ({lat})")
                    elif tool == "list_databases":
                        dbs = ", ".join(m.get("databases", []))
                        print(f"{head} {D}(found {m.get('count', 0)}: {dbs}){N} ━━")
                    elif tool == "check_config":
                        v_str = f"{G}VALID{N}" if m.get("valid") else f"{R}INVALID{N}"
                        print(f"{head} [{v_str}] ━━")
                    else:
                        print(f"{head} ━━")
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


def read_recent_audit_history(limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    if limit <= 0 or not AUDIT.exists():
        return []
    try:
        with open(AUDIT, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return []
            bytes_to_read = min(size, 512 * 1024)
            f.seek(size - bytes_to_read)
            chunk = f.read()
            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            if bytes_to_read < size and lines:
                lines.pop(0)  # drop potentially incomplete first line

            raw_records = []
            for ln in lines:
                try:
                    raw_records.append(json.loads(ln))
                except Exception:
                    pass

            consolidated = []
            pending_requests = {}
            for rec in raw_records:
                evt = rec.get("event")
                if evt == "request":
                    key = (rec.get("session"), rec.get("db"), rec.get("sql"))
                    pending_requests[key] = rec
                elif evt in ("executed", "error", "denied"):
                    key = (rec.get("session"), rec.get("db"), rec.get("sql"))
                    req = pending_requests.pop(key, None)
                    if req:
                        for fld in ("context", "konteks", "tool", "sql", "db"):
                            if not rec.get(fld) and req.get(fld):
                                rec[fld] = req[fld]
                    consolidated.append(rec)
                elif evt in ("auto_allow", "blocked", "check_connection", "list_databases", "check_config"):
                    consolidated.append(rec)

            return consolidated[-limit:]
    except Exception:
        return []


def format_history_entry(rec: dict) -> list[str]:
    ts_raw = rec.get("ts", "")
    try:
        dt = datetime.fromisoformat(ts_raw).astimezone()
        now = datetime.now().astimezone()
        if dt.date() == now.date():
            time_str = dt.strftime("%H:%M:%S")
        else:
            time_str = dt.strftime("%m-%d %H:%M")
    except Exception:
        time_str = ts_raw[11:19] if len(ts_raw) >= 19 else "??:??:??"

    evt = rec.get("event")
    tool = rec.get("tool", "")
    db = rec.get("db", "")
    db_disp = f" @ {db}" if db else ""
    ctx = rec.get("context") or rec.get("konteks", "")
    ctx_disp = f" · {ctx}" if ctx else ""

    sql = clean(rec.get("sql", "")).strip()
    sql_preview = " ".join(sql.split()) if sql else ""
    if len(sql_preview) > 95:
        sql_preview = sql_preview[:92] + "..."

    lines = []
    if evt == "denied":
        reason = rec.get("reason", "")
        if reason == "monitor offline":
            badge = f"{R}[PAUSED-OFFLINE]{N}"
            lines.append(f"  {time_str}  {badge} {tool}{db_disp}{ctx_disp}")
            if sql_preview:
                lines.append(f"             {D}SQL: {sql_preview}{N}")
            lines.append(f"             ↳ {Y}AI query paused because monitor was offline.{N}")
        elif reason == "user":
            badge = f"{R}[REJECTED-BY-USER]{N}"
            lines.append(f"  {time_str}  {badge} {tool}{db_disp}{ctx_disp}")
            if sql_preview:
                lines.append(f"             {D}SQL: {sql_preview}{N}")
        else:
            badge = f"{R}[DENIED: {reason}]{N}"
            lines.append(f"  {time_str}  {badge} {tool}{db_disp}{ctx_disp}")
            if sql_preview:
                lines.append(f"             {D}SQL: {sql_preview}{N}")

    elif evt == "executed":
        badge = f"{G}[EXECUTED]{N}"
        stats = f"{rec.get('rows', 0)} rows, {rec.get('ms', 0)} ms"
        lines.append(f"  {time_str}  {badge} {tool}{db_disp} {D}({stats}){N}{ctx_disp}")
        if sql_preview:
            lines.append(f"             {D}SQL: {sql_preview}{N}")

    elif evt == "auto_allow":
        badge = f"{G}[AUTO-ALLOW]{N}"
        reason = rec.get("reason", "")
        reason_disp = f" {D}({reason}){N}" if reason else ""
        lines.append(f"  {time_str}  {badge} {tool}{db_disp}{reason_disp}{ctx_disp}")
        if sql_preview:
            lines.append(f"             {D}SQL: {sql_preview}{N}")

    elif evt == "error":
        badge = f"{R}[DB-ERROR]{N}"
        err = clean(rec.get("error", ""))[:80]
        lines.append(f"  {time_str}  {badge} {tool}{db_disp}{ctx_disp}")
        lines.append(f"             {R}Error: {err}{N}")

    elif evt == "blocked":
        badge = f"{R}[BLOCKED]{N}"
        reason = rec.get("reason", "")
        reason_disp = f" {D}({reason}){N}" if reason else ""
        lines.append(f"  {time_str}  {badge} {tool}{db_disp}{reason_disp}{ctx_disp}")
        if sql_preview:
            lines.append(f"             {D}SQL: {sql_preview}{N}")

    elif evt == "check_connection":
        badge = f"{Y}[HEALTH-CHECK]{N}"
        targets = ", ".join(rec.get("targets", []))
        cnt = rec.get("count", len(rec.get("targets", [])))
        lines.append(f"  {time_str}  {badge} check_connection {D}({cnt} targets: {targets}){N}")

    elif evt == "list_databases":
        badge = f"{Y}[METADATA]{N}"
        cnt = rec.get("count", len(rec.get("databases", [])))
        dbs_str = f": {', '.join(rec.get('databases', []))}" if rec.get("databases") else ""
        lines.append(f"  {time_str}  {badge} list_databases {D}(found {cnt}{dbs_str}){N}")

    elif evt == "check_config":
        badge = f"{Y}[CONFIG-CHECK]{N}"
        valid = rec.get("valid", True)
        v_str = f"{G}VALID{N}" if valid else f"{R}INVALID{N}"
        lines.append(f"  {time_str}  {badge} check_config [{v_str}]")

    elif evt == "request":
        badge = f"{Y}[PENDING]{N}"
        lines.append(f"  {time_str}  {badge} {tool}{db_disp}{ctx_disp}")
        if sql_preview:
            lines.append(f"             {D}SQL: {sql_preview}{N}")

    else:
        badge = f"{D}[{evt.upper()}]{N}"
        lines.append(f"  {time_str}  {badge} {tool}{db_disp}{ctx_disp}")

    return lines


def render_history_banner(history: list[dict]):
    print(f"\n{Y}╔════════════════════════════════════════════════════════════════════════════════════╗{N}")
    print(f"{Y}║                   📜 RECENT ACTIVITY (BEFORE MONITOR OPENED)                  ║{N}")
    print(f"{Y}╚════════════════════════════════════════════════════════════════════════════════════╝{N}")
    if not history:
        print(f"  {D}No previous database activity recorded in audit log.{N}")
    else:
        for rec in history:
            for line in format_history_entry(rec):
                print(line)
    print(f"{D}──────────────────────────────────────────────────────────────────────────────────────{N}")


def render_live_banner(bound_port: int, fallback: bool = False):
    print(f" {G}🟢 LIVE STREAM & APPROVAL BRIDGE ACTIVE{N} {D}(127.0.0.1:{bound_port}){N}")
    if fallback:
        print(f" {Y}Note: Preferred port {PREFERRED_PORT} was busy. Fallback to {bound_port}.{N}")
    print(f" {D}Waiting for incoming database actions from AI client...{N}")
    print(f"{D}──────────────────────────────────────────────────────────────────────────────────────{N}\n", flush=True)


async def main(history_limit: int | None = None):
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

    # Determine effective history limit
    limit = HISTORY_LIMIT if history_limit is None else history_limit

    # Phase 1: BEFORE (Recent History)
    if limit > 0:
        history = read_recent_audit_history(limit=limit)
        render_history_banner(history)

    # Phase 2: AFTER (Live Stream)
    render_live_banner(bound_port, fallback=(bound_port != PREFERRED_PORT))

    kb_task = asyncio.create_task(keyboard_listener())
    try:
        async with srv:
            await srv.serve_forever()
    finally:
        kb_task.cancel()
        remove_state_port()


def start(history_limit: int | None = None):
    try:
        asyncio.run(main(history_limit=history_limit))
    except KeyboardInterrupt:
        pass
    finally:
        remove_state_port()


if __name__ == "__main__":
    start()
