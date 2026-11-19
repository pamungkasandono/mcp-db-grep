import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_INSTRUCTIONS = """# Database MCP (`db-grep`) Handling Rules

## Case 1: When any tool from `db-grep` server returns `MONITOR_OFFLINE`
This happens when the terminal monitor (`db-grep monitor`) is not running.

1. **ANTI-BYPASS STRICT RULE (DO NOT VIOLATE)**:
   - **DO NOT** attempt to read database credentials from `.env`, configuration files, or source code.
   - **DO NOT** create your own database connection scripts (Bash, psql, Node.js, Python, etc.) to bypass the monitor.
   - **DO NOT** try other tools to circumvent this requirement.

2. **Notify User Clearly**:
   - Inform the user that the query was paused because the approval monitor is offline:
     "Database monitor is offline. To protect your data, all queries require approval via `db-grep monitor`."
   - State the query that was about to run and the target database.

3. **Prompt User Next Step**:
   - Use the `question` tool (or prompt choice) to ask user:
     - "I have started `db-grep monitor` in another terminal, retry query"
     - "Cancel and continue without database query"

---

## Case 2: When any tool returns `ACTION_DENIED_BY_USER`
This happens when the operator pressed `n` (reject) in the terminal monitor.

1. **Stop Tool Calls Immediately**:
   - Never retry the rejected query.
   - Never call any other `db-grep` tool for the current request.
   - Never attempt alternative direct database access.

2. **Explain Naturally (Do NOT copy or echo raw error instructions)**:
   - State clearly that the query was rejected in the monitor.
   - Show the SQL query that was rejected.
   - Explain what data was being queried and why it is needed to answer the user's request.
   - Explain what cannot be done or verified without this data.

3. **Prompt User Next Step**:
   - Use the `question` tool (or prompt choice) to ask user:
     - "Retry query (I will approve in monitor)"
     - "Continue without database query"
"""

SAMPLE_CONFIG = """[monitor]
port = 48260
approval_timeout_s = 110      # auto-reject after this many seconds without response
history_limit = 20           # replay recent audit activity before monitor opened (0 = off)

[defaults]
allow_metadata = ["read"]     # list_tables, find_objects, search_columns & describe_table without prompt
allow_data = []               # data queries require approval unless covered by allow_tables
max_rows = 1000
timeout_ms = 10000

# Manage databases via CLI (recommended):
#   db-grep --config add
#   db-grep --config list
#
# Or add manual entries below (see README.md for examples):
#   [db.<driver>.<name>]
"""


# ---------------- Helper Path & JSON Utilities ----------------

def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
        # Remove single-line comments // if any for lenient jsonc
        stripped = re.sub(r"^\s*//.*$", "", content, flags=re.MULTILINE)
        return json.loads(stripped) if stripped.strip() else {}
    except Exception:
        return {}


def write_json_file(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _home_config_path(rel_path: str) -> Path:
    if sys.platform == "win32":
        userprofile = os.environ.get("USERPROFILE")
        base = Path(userprofile) if userprofile else Path("~").expanduser()
        return base / rel_path
    return Path(f"~/{rel_path}").expanduser()


def _appdata_config_path(rel_path: str, mac_support: bool = True, linux_rel: str | None = None) -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else _home_config_path("AppData/Roaming")
        return base / rel_path
    if sys.platform == "darwin" and mac_support:
        return _home_config_path(f"Library/Application Support/{rel_path}")
    return _home_config_path(f".config/{linux_rel or rel_path}")


def get_claude_desktop_config_path() -> Path:
    return _appdata_config_path("Claude/claude_desktop_config.json")


def get_opencode_config_path() -> Path:
    opencode_dir = _home_config_path(".config/opencode")
    target_json = opencode_dir / "opencode.jsonc"
    if not target_json.exists() and (opencode_dir / "opencode.json").exists():
        return opencode_dir / "opencode.json"
    return target_json


def get_antigravity_config_path() -> Path:
    return _home_config_path(".gemini/config/mcp_config.json")


def get_cursor_config_path() -> Path:
    return _home_config_path(".cursor/mcp.json")


def get_codex_config_path() -> Path:
    return _home_config_path(".codex/config.toml")


def get_roo_code_config_path() -> Path:
    return _appdata_config_path("Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json")


def get_zed_config_path() -> Path:
    return _appdata_config_path("Zed/settings.json", mac_support=False, linux_rel="zed/settings.json")


def get_pi_agent_dir() -> Path:
    env_dir = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(env_dir).expanduser() if env_dir else _home_config_path(".pi/agent")


def get_pi_config_path() -> Path:
    return get_pi_agent_dir() / "mcp.json"


MCP_SERVER_NAME = "db-grep"
STD_MCP_ENTRY = {"command": "db-grep", "args": ["server"]}

STATUS_CONFIGURED = "configured"
STATUS_NEEDS_UPDATE = "needs_update"
STATUS_READY = "ready"
STATUS_NOT_INSTALLED = "not_installed"

# Terminal color codes (matching updater.py styling)
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[90m"
RESET = "\033[0m"

STATUS_META = {
    STATUS_CONFIGURED: {"label": "✓ configured", "color": GREEN},
    STATUS_NEEDS_UPDATE: {"label": "▲ update needed", "color": YELLOW},
    STATUS_READY: {"label": "○ ready to install", "color": CYAN},
    STATUS_NOT_INSTALLED: {"label": "· not installed", "color": DIM},
}


def _supports_color() -> bool:
    if "NO_COLOR" in os.environ:
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def format_status_badge(status: str, width: int = 19, use_color: bool | None = None) -> str:
    color_enabled = _supports_color() if use_color is None else use_color
    meta = STATUS_META.get(status, {"label": status, "color": ""})
    raw_label = meta["label"]
    color = meta["color"] if color_enabled else ""
    padded = f"{raw_label:<{width}}"
    if color:
        return f"{color}{padded}{RESET}"
    return padded


def shorten_path(path_str: str, max_len: int = 50) -> str:
    """Shorten path by replacing home dir with ~ and middle dirs with ... if too long."""
    if not path_str:
        return ""
    try:
        home_str = str(Path.home())
    except Exception:
        home_str = ""

    p = path_str
    if home_str and p.startswith(home_str):
        p = "~" + p[len(home_str):]
    elif os.name == "nt":
        userprofile = os.environ.get("USERPROFILE")
        if userprofile and p.startswith(userprofile):
            p = "~" + p[len(userprofile):]

    if len(p) <= max_len:
        return p

    if p.startswith("`") or ("/" not in p and "\\" not in p):
        return p

    sep = "\\" if "\\" in p else "/"
    parts = p.replace("\\", "/").split("/")
    if len(parts) > 2:
        # 1. Try: prefix/.../parent/file (e.g. ~/AppData/.../settings/cline_mcp_settings.json)
        if len(parts) > 3:
            cand = f"{sep.join(parts[:2])}{sep}...{sep}{sep.join(parts[-2:])}"
            if len(cand) <= max_len:
                return cand
            cand = f"{parts[0]}{sep}...{sep}{sep.join(parts[-2:])}"
            if len(cand) <= max_len:
                return cand
        # 2. Try: prefix/.../file (e.g. ~/.../claude_desktop_config.json)
        cand = f"{parts[0]}{sep}...{sep}{parts[-1]}"
        if len(cand) <= max_len:
            return cand

    return p[: max_len - 3] + "..."


def _detect_path_or_bin(cfg: Path, binary: str | None = None, check_parent: bool = True) -> bool:
    return cfg.exists() or (check_parent and cfg.parent.exists()) or bool(binary and shutil.which(binary))


def _is_legacy_db_grep_entry(val: object) -> bool:
    if not isinstance(val, dict):
        return False
    cmd = val.get("command")
    if cmd == "db-grep" or cmd in (["db-grep", "--server"], ["db-grep", "server"]):
        return True
    if isinstance(cmd, list) and len(cmd) > 0 and cmd[0] == "db-grep":
        return True
    if isinstance(cmd, dict) and cmd.get("path") == "db-grep":
        return True
    args = val.get("args")
    if isinstance(args, list) and "--server" in args and cmd == "db-grep":
        return True
    return False


def _inspect_json_mcp_status(cfg_path: Path, section_key: str = "mcpServers") -> str:
    if not cfg_path.exists():
        return STATUS_READY
    data = read_json_file(cfg_path)
    servers = data.get(section_key, {})
    if not isinstance(servers, dict):
        return STATUS_READY

    # Check for legacy 'db' entry belonging to db-grep
    if _is_legacy_db_grep_entry(servers.get("db")):
        return STATUS_NEEDS_UPDATE

    # Check for 'db-grep' entry
    if MCP_SERVER_NAME in servers:
        entry = servers[MCP_SERVER_NAME]
        entry_str = json.dumps(entry) if isinstance(entry, (dict, list)) else str(entry)
        if "--server" in entry_str:
            return STATUS_NEEDS_UPDATE
        return STATUS_CONFIGURED

    return STATUS_READY


def _install_json_mcp(cfg_file: Path, label: str, section_key: str = "mcpServers", entry: dict | None = None):
    data = read_json_file(cfg_file)
    servers = data.get(section_key, {})
    if not isinstance(servers, dict):
        servers = {}
    if _is_legacy_db_grep_entry(servers.get("db")):
        servers.pop("db", None)
    servers[MCP_SERVER_NAME] = dict(entry or STD_MCP_ENTRY)
    data[section_key] = servers
    write_json_file(cfg_file, data)
    print(f"  ✓ Configured {label}: {cfg_file}")


# ---------------- 9 Harness Implementations in Ordered Sequence ----------------

# 1. Claude Code CLI
def detect_claude_cli() -> bool:
    return bool(shutil.which("claude"))


def install_claude_cli(_instructions_file: Path):
    if not shutil.which("claude"):
        print("  ✗ claude command not found in PATH")
        return
    try:
        # Clean up legacy 'db' entry in ~/.claude.json projects if present
        cfg_path = _home_config_path(".claude.json")
        if cfg_path.exists():
            data = read_json_file(cfg_path)
            modified = False
            for proj in data.get("projects", {}).values():
                if isinstance(proj, dict) and "mcpServers" in proj:
                    if _is_legacy_db_grep_entry(proj["mcpServers"].get("db")):
                        proj["mcpServers"].pop("db", None)
                        modified = True
            if _is_legacy_db_grep_entry(data.get("mcpServers", {}).get("db")):
                data["mcpServers"].pop("db", None)
                modified = True
            if modified:
                write_json_file(cfg_path, data)

        subprocess.run(["claude", "mcp", "remove", "db"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["claude", "mcp", "remove", MCP_SERVER_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        res = subprocess.run(
            ["claude", "mcp", "add", MCP_SERVER_NAME, "--", "db-grep", "server"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            print(f"  ✓ Registered MCP '{MCP_SERVER_NAME}' to Claude Code CLI")
        else:
            print(f"  ✗ Claude CLI registration returned error: {res.stderr.strip()}")
    except Exception as e:
        print(f"  ✗ Failed to register to Claude CLI: {e}")


# 2. Claude Desktop
def detect_claude_desktop() -> bool:
    return _detect_path_or_bin(get_claude_desktop_config_path())


def install_claude_desktop(_instructions_file: Path):
    _install_json_mcp(get_claude_desktop_config_path(), "Claude Desktop")


# 3. OpenCode
def detect_opencode() -> bool:
    return _detect_path_or_bin(get_opencode_config_path(), "opencode")


def install_opencode(instructions_file: Path):
    target_json = get_opencode_config_path()
    target_json.parent.mkdir(parents=True, exist_ok=True)

    data = read_json_file(target_json)

    instr_path = str(instructions_file)
    instructions = data.get("instructions", [])
    if instr_path not in instructions:
        instructions.append(instr_path)
        data["instructions"] = instructions

    mcp = data.get("mcp", {})
    if _is_legacy_db_grep_entry(mcp.get("db")):
        mcp.pop("db", None)
    mcp[MCP_SERVER_NAME] = {
        "type": "local",
        "command": ["db-grep", "server"],
        "enabled": True,
    }
    data["mcp"] = mcp
    write_json_file(target_json, data)
    print(f"  ✓ Configured OpenCode: {target_json}")


# 4. Antigravity
def detect_antigravity() -> bool:
    agy_cfg = get_antigravity_config_path()
    return _detect_path_or_bin(agy_cfg, "antigravity") or agy_cfg.parent.parent.exists()


def install_antigravity(_instructions_file: Path):
    _install_json_mcp(get_antigravity_config_path(), "Antigravity")


# 5. Cursor
def detect_cursor() -> bool:
    return _detect_path_or_bin(get_cursor_config_path(), "cursor")


def install_cursor(_instructions_file: Path):
    _install_json_mcp(get_cursor_config_path(), "Cursor")


# 6. Codex CLI
def detect_codex() -> bool:
    return _detect_path_or_bin(get_codex_config_path(), "codex")


def install_codex(_instructions_file: Path):
    cfg_file = get_codex_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    content = cfg_file.read_text(encoding="utf-8") if cfg_file.exists() else ""

    if "[mcp_servers.db-grep]" in content:
        # Update legacy --server args if present
        if 'args = ["--server"]' in content:
            updated = content.replace('args = ["--server"]', 'args = ["server"]')
            cfg_file.write_text(updated, encoding="utf-8")
        print(f"  ✓ Codex already configured: {cfg_file}")
        return

    # Migrate legacy [mcp_servers.db] belonging to db-grep
    legacy_re = re.compile(r'\[mcp_servers\.db\]\s*\n\s*command\s*=\s*"db-grep"')
    if legacy_re.search(content):
        updated = legacy_re.sub('[mcp_servers.db-grep]\ncommand = "db-grep"\nargs = ["server"]', content, count=1)
        cfg_file.write_text(updated, encoding="utf-8")
        print(f"  ✓ Migrated Codex MCP entry to 'db-grep': {cfg_file}")
        return

    entry = '\n[mcp_servers.db-grep]\ncommand = "db-grep"\nargs = ["server"]\n'
    updated = (content.rstrip() + "\n" + entry).lstrip()
    cfg_file.write_text(updated, encoding="utf-8")
    print(f"  ✓ Configured Codex: {cfg_file}")


# 7. Roo Code
def detect_roo_code() -> bool:
    return _detect_path_or_bin(get_roo_code_config_path())


def install_roo_code(_instructions_file: Path):
    _install_json_mcp(
        get_roo_code_config_path(),
        "Roo Code",
        entry={**STD_MCP_ENTRY, "disabled": False, "autoApprove": []},
    )


# 8. Zed Editor
def detect_zed() -> bool:
    return _detect_path_or_bin(get_zed_config_path(), "zed")


def install_zed(_instructions_file: Path):
    _install_json_mcp(
        get_zed_config_path(),
        "Zed Editor",
        section_key="context_servers",
        entry={"command": {"path": "db-grep", "args": ["server"]}},
    )


# 9. Pi
def detect_pi() -> bool:
    return _detect_path_or_bin(get_pi_config_path(), "pi")


def install_pi(_instructions_file: Path):
    _install_json_mcp(get_pi_config_path(), "Pi")


# ---------------- Registry Definition in Exact Requested Order ----------------

HARNESS_REGISTRY = [
    {
        "id": "claude",
        "name": "Claude Code CLI",
        "desc": "`claude mcp add`",
        "detect": detect_claude_cli,
        "install": install_claude_cli,
    },
    {
        "id": "claude-desktop",
        "name": "Claude Desktop",
        "desc": lambda: str(get_claude_desktop_config_path()),
        "detect": detect_claude_desktop,
        "install": install_claude_desktop,
    },
    {
        "id": "opencode",
        "name": "OpenCode",
        "desc": lambda: str(get_opencode_config_path()),
        "detect": detect_opencode,
        "install": install_opencode,
    },
    {
        "id": "antigravity",
        "name": "Antigravity",
        "desc": lambda: str(get_antigravity_config_path()),
        "detect": detect_antigravity,
        "install": install_antigravity,
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "desc": lambda: str(get_cursor_config_path()),
        "detect": detect_cursor,
        "install": install_cursor,
    },
    {
        "id": "codex",
        "name": "Codex CLI",
        "desc": lambda: str(get_codex_config_path()),
        "detect": detect_codex,
        "install": install_codex,
    },
    {
        "id": "roo-code",
        "name": "Roo Code",
        "desc": lambda: str(get_roo_code_config_path()),
        "detect": detect_roo_code,
        "install": install_roo_code,
    },
    {
        "id": "zed",
        "name": "Zed Editor",
        "desc": lambda: str(get_zed_config_path()),
        "detect": detect_zed,
        "install": install_zed,
    },
    {
        "id": "pi",
        "name": "Pi Coding Agent",
        "desc": lambda: str(get_pi_config_path()),
        "detect": detect_pi,
        "install": install_pi,
    },
]


def get_harness_status(item: dict) -> str:
    """Determine installation status for a harness: configured, needs_update, ready, or not_installed."""
    if not item["detect"]():
        return STATUS_NOT_INSTALLED

    hid = item["id"]

    if hid == "claude":
        cfg = _home_config_path(".claude.json")
        if not cfg.exists():
            return STATUS_READY
        data = read_json_file(cfg)
        servers = dict(data.get("mcpServers", {}))
        for proj in data.get("projects", {}).values():
            if isinstance(proj, dict) and "mcpServers" in proj:
                servers.update(proj["mcpServers"])
        if _is_legacy_db_grep_entry(servers.get("db")):
            return STATUS_NEEDS_UPDATE
        if MCP_SERVER_NAME in servers:
            entry = servers[MCP_SERVER_NAME]
            if "--server" in json.dumps(entry):
                return STATUS_NEEDS_UPDATE
            return STATUS_CONFIGURED
        return STATUS_READY

    if hid == "opencode":
        return _inspect_json_mcp_status(get_opencode_config_path(), section_key="mcp")

    if hid == "zed":
        return _inspect_json_mcp_status(get_zed_config_path(), section_key="context_servers")

    if hid == "codex":
        cfg = get_codex_config_path()
        if not cfg.exists():
            return STATUS_READY
        try:
            content = cfg.read_text(encoding="utf-8")
        except Exception:
            return STATUS_READY
        if "[mcp_servers.db]" in content and "db-grep" in content:
            return STATUS_NEEDS_UPDATE
        if "[mcp_servers.db-grep]" in content:
            if 'args = ["--server"]' in content or "--server" in content:
                return STATUS_NEEDS_UPDATE
            return STATUS_CONFIGURED
        return STATUS_READY

    path_map = {
        "claude-desktop": get_claude_desktop_config_path,
        "antigravity": get_antigravity_config_path,
        "cursor": get_cursor_config_path,
        "roo-code": get_roo_code_config_path,
        "pi": get_pi_config_path,
    }
    if hid in path_map:
        return _inspect_json_mcp_status(path_map[hid](), section_key="mcpServers")

    return STATUS_READY


def ask_user_choices(detected_map: dict | None = None, status_map: dict | None = None) -> list[str]:
    use_color = _supports_color()

    # Determine status map if not provided
    if status_map is None:
        status_map = {}
        for item in HARNESS_REGISTRY:
            hid = item["id"]
            if detected_map is not None:
                is_detected = detected_map.get(hid, False)
                status_map[hid] = STATUS_READY if is_detected else STATUS_NOT_INSTALLED
            else:
                status_map[hid] = get_harness_status(item)

    print("\nSupported AI clients / harnesses:\n")

    term_w = shutil.get_terminal_size((80, 24)).columns
    max_path_len = max(35, term_w - 44)

    detected_ids = []
    menu_lines = []
    for idx, item in enumerate(HARNESS_REGISTRY, start=1):
        hid = item["id"]
        status = status_map.get(hid, STATUS_NOT_INSTALLED)
        if status != STATUS_NOT_INSTALLED:
            detected_ids.append(hid)

        badge = format_status_badge(status, width=19, use_color=use_color)
        desc_val = item["desc"]() if callable(item["desc"]) else item["desc"]
        short_desc = shorten_path(desc_val, max_path_len)
        detail = f"{item['name']} ({short_desc})"
        menu_lines.append(f"  [{idx}] {hid:<16} - {badge} {detail}")

    for line in menu_lines:
        print(line)

    sep_len = max(50, min(term_w - 4, 76))
    print("  " + "─" * sep_len)
    print(f"  [a] {'all':<16} - Force configure all harnesses")
    print(f"  [n] {'none':<16} - Skip for now (no harnesses configured)")
    print("  " + "─" * sep_len)

    if detected_ids:
        default_label = f"detected only ({', '.join(detected_ids)})"
    else:
        default_label = "none"

    try:
        choice = input(f"\nSelect harness to configure [default: {default_label}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipping harness configuration.")
        return []

    if not choice:
        return detected_ids

    choice_lower = choice.lower()
    if choice_lower in ("a", "all"):
        return [item["id"] for item in HARNESS_REGISTRY]
    if choice_lower in ("n", "none"):
        return []

    selected = []
    tokens = [t.strip() for t in choice.replace(",", " ").split()]
    for t in tokens:
        if t.isdigit():
            idx = int(t) - 1
            if 0 <= idx < len(HARNESS_REGISTRY):
                selected.append(HARNESS_REGISTRY[idx]["id"])
        else:
            for item in HARNESS_REGISTRY:
                if t.lower() == item["id"]:
                    selected.append(item["id"])

    return list(dict.fromkeys(selected))


def run_install(target_harness: str = None):
    config_dir = _home_config_path(".config/db-grep")
    config_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        config_dir.chmod(0o700)

    # 1. Write config.toml if missing
    config_file = config_dir / "config.toml"
    if not config_file.exists():
        config_file.write_text(SAMPLE_CONFIG, encoding="utf-8")
        if os.name == "posix":
            config_file.chmod(0o600)
        print(f"✓ Created template config: {config_file}")
    else:
        print(f"✓ Config already exists: {config_file}")

    # 2. Write instructions.md
    instructions_file = config_dir / "instructions.md"
    bundled_instructions = Path(__file__).resolve().parent / "instructions" / "db-grep.md"
    if bundled_instructions.exists():
        shutil.copy2(bundled_instructions, instructions_file)
    else:
        instructions_file.write_text(DEFAULT_INSTRUCTIONS, encoding="utf-8")
    print(f"✓ Installed instructions: {instructions_file}")

    # 3. Detect installed harnesses & compute status
    status_map = {item["id"]: get_harness_status(item) for item in HARNESS_REGISTRY}
    detected_ids = [hid for hid, st in status_map.items() if st != STATUS_NOT_INSTALLED]

    if target_harness:
        target = target_harness.lower()
        if target == "all":
            targets = [item["id"] for item in HARNESS_REGISTRY]
        elif target == "none":
            targets = []
        else:
            targets = [t.strip() for t in target.split(",") if t.strip()]
    else:
        if not sys.stdin.isatty():
            targets = detected_ids
        else:
            targets = ask_user_choices(status_map=status_map)

    print("\nApplying configuration to selected harnesses:")
    handler_map = {item["id"]: item["install"] for item in HARNESS_REGISTRY}
    for t in targets:
        if t in handler_map:
            handler_map[t](instructions_file)
        else:
            print(f"  ? Unknown harness: {t}")

    print("\nSetup complete! Next steps:")
    print(f"1. Run 'db-grep config add' (or 'db-grep config') to add database connections (or edit {config_file})")
    print("2. Run 'db-grep monitor' in a terminal before starting your AI chat.")

    # Check if Oracle is configured and driver is missing
    try:
        import driver_manager
        if not driver_manager.is_driver_ready("oracle") and sys.stdin.isatty():
            import tomllib
            if config_file.exists():
                cfg_data = tomllib.loads(config_file.read_text(encoding="utf-8"))
                has_oracle = any(
                    isinstance(v, dict) and (v.get("type") == "oracle" or k.startswith("oracle:") or k.startswith("ora:"))
                    for k, v in cfg_data.get("db", {}).items()
                )
                if has_oracle:
                    print("\n[!] Oracle database detected in configuration.")
                    try:
                        ans = input("Would you like to install the Oracle Instant Client driver now for legacy 10g support? [Y/n]: ").strip().lower()
                        if ans in ("", "y", "yes"):
                            driver_manager.install_driver("oracle")
                    except (KeyboardInterrupt, EOFError):
                        pass
    except (ImportError, OSError, tomllib.TOMLDecodeError):
        pass


# Backward compatibility alias
run_init = run_install
