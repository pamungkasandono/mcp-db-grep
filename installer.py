import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_INSTRUCTIONS = """# Database MCP (`db-grep`) Handling Rules

## Case 1: When any tool from `db` server returns `MONITOR_OFFLINE`
This happens when the terminal monitor (`db-grep --monitor`) is not running.

1. **ANTI-BYPASS STRICT RULE (DO NOT VIOLATE)**:
   - **DO NOT** attempt to read database credentials from `.env`, configuration files, or source code.
   - **DO NOT** create your own database connection scripts (Bash, psql, Node.js, Python, etc.) to bypass the monitor.
   - **DO NOT** try other tools to circumvent this requirement.

2. **Notify User Clearly**:
   - Inform the user that the query was paused because the approval monitor is offline:
     "Database monitor is offline. To protect your data, all queries require approval via `db-grep --monitor`."
   - State the query that was about to run and the target database.

3. **Prompt User Next Step**:
   - Use the `question` tool (or prompt choice) to ask user:
     - "I have started `db-grep --monitor` in another terminal, retry query"
     - "Cancel and continue without database query"

---

## Case 2: When any tool returns `ACTION_DENIED_BY_USER`
This happens when the operator pressed `n` (reject) in the terminal monitor.

1. **Stop Tool Calls Immediately**:
   - Never retry the rejected query.
   - Never call any other `db` tool for the current request.
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

# Example Postgres Connection:
# [db.my-app]
# type = "postgres"
# description = "Main application database"
# dsn = "postgres://user@localhost:5432/my_app"
# password_env = "MY_APP_PW"  # or: password = "plain_password"

# Example Oracle Connection:
# [db.legacy-db]
# type = "oracle"
# description = "Corporate Oracle database"
# dsn = "host:1521/SERVICE_NAME"
# user = "ro_user"
# password_env = "ORACLE_PW"
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


def get_claude_desktop_config_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path("~").expanduser() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "darwin":
        return Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
    else:
        return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


def get_cursor_config_path() -> Path:
    if sys.platform == "win32":
        userprofile = os.environ.get("USERPROFILE")
        base = Path(userprofile) if userprofile else Path("~").expanduser()
        return base / ".cursor" / "mcp.json"
    else:
        return Path("~/.cursor/mcp.json").expanduser()


def get_codex_config_path() -> Path:
    if sys.platform == "win32":
        userprofile = os.environ.get("USERPROFILE")
        base = Path(userprofile) if userprofile else Path("~").expanduser()
        return base / ".codex" / "config.toml"
    else:
        return Path("~/.codex/config.toml").expanduser()


def get_roo_code_config_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path("~").expanduser() / "AppData" / "Roaming"
        return base / "Code" / "User" / "globalStorage" / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json"
    elif sys.platform == "darwin":
        return Path("~/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json").expanduser()
    else:
        return Path("~/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json").expanduser()


def get_zed_config_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path("~").expanduser() / "AppData" / "Roaming"
        return base / "Zed" / "settings.json"
    else:
        return Path("~/.config/zed/settings.json").expanduser()


def get_pi_agent_dir() -> Path:
    env_dir = os.environ.get("PI_CODING_AGENT_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path("~/.pi/agent").expanduser()


# ---------------- 9 Harness Implementations in Ordered Sequence ----------------

# 1. Claude Code CLI
def detect_claude_cli() -> bool:
    return bool(shutil.which("claude"))


def install_claude_cli(_instructions_file: Path):
    if not shutil.which("claude"):
        print("  ✗ claude command not found in PATH")
        return
    try:
        subprocess.run(["claude", "mcp", "remove", "db"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        res = subprocess.run(["claude", "mcp", "add", "db", "--", "db-grep", "--server"], capture_output=True, text=True)
        if res.returncode == 0:
            print("  ✓ Registered MCP 'db' to Claude Code CLI")
        else:
            print(f"  ✗ Claude CLI registration returned error: {res.stderr.strip()}")
    except Exception as e:
        print(f"  ✗ Failed to register to Claude CLI: {e}")


# 2. Claude Desktop
def detect_claude_desktop() -> bool:
    cfg = get_claude_desktop_config_path()
    return cfg.exists() or cfg.parent.exists()


def install_claude_desktop(_instructions_file: Path):
    cfg_file = get_claude_desktop_config_path()
    data = read_json_file(cfg_file)
    mcp = data.get("mcpServers", {})
    mcp["db"] = {
        "command": "db-grep",
        "args": ["--server"],
    }
    data["mcpServers"] = mcp
    write_json_file(cfg_file, data)
    print(f"  ✓ Configured Claude Desktop: {cfg_file}")


# 3. OpenCode
def detect_opencode() -> bool:
    opencode_dir = Path("~/.config/opencode").expanduser()
    return opencode_dir.exists() or bool(shutil.which("opencode"))


def install_opencode(instructions_file: Path):
    opencode_dir = Path("~/.config/opencode").expanduser()
    opencode_dir.mkdir(parents=True, exist_ok=True)
    target_json = opencode_dir / "opencode.jsonc"
    if not target_json.exists():
        alt = opencode_dir / "opencode.json"
        target_json = alt if alt.exists() else opencode_dir / "opencode.jsonc"

    data = read_json_file(target_json)

    instr_path = str(instructions_file)
    instructions = data.get("instructions", [])
    if instr_path not in instructions:
        instructions.append(instr_path)
        data["instructions"] = instructions

    mcp = data.get("mcp", {})
    mcp["db"] = {
        "type": "local",
        "command": ["db-grep", "--server"],
        "enabled": True,
    }
    data["mcp"] = mcp
    write_json_file(target_json, data)
    print(f"  ✓ Configured OpenCode: {target_json}")


# 4. Antigravity
def detect_antigravity() -> bool:
    agy_cfg = Path("~/.gemini/config/mcp_config.json").expanduser()
    return agy_cfg.parent.exists() or Path("~/.gemini").expanduser().exists() or bool(shutil.which("antigravity"))


def install_antigravity(_instructions_file: Path):
    cfg_file = Path("~/.gemini/config/mcp_config.json").expanduser()
    data = read_json_file(cfg_file)
    mcp = data.get("mcpServers", {})
    mcp["db"] = {
        "command": "db-grep",
        "args": ["--server"],
    }
    data["mcpServers"] = mcp
    write_json_file(cfg_file, data)
    print(f"  ✓ Configured Antigravity: {cfg_file}")


# 5. Cursor
def detect_cursor() -> bool:
    cfg = get_cursor_config_path()
    return cfg.exists() or cfg.parent.exists() or bool(shutil.which("cursor"))


def install_cursor(_instructions_file: Path):
    cfg_file = get_cursor_config_path()
    data = read_json_file(cfg_file)
    mcp = data.get("mcpServers", {})
    mcp["db"] = {
        "command": "db-grep",
        "args": ["--server"],
    }
    data["mcpServers"] = mcp
    write_json_file(cfg_file, data)
    print(f"  ✓ Configured Cursor: {cfg_file}")


# 6. Codex CLI
def detect_codex() -> bool:
    cfg = get_codex_config_path()
    return cfg.exists() or cfg.parent.exists() or bool(shutil.which("codex"))


def install_codex(_instructions_file: Path):
    cfg_file = get_codex_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    content = cfg_file.read_text(encoding="utf-8") if cfg_file.exists() else ""

    entry = '\n[mcp_servers.db]\ncommand = "db-grep"\nargs = ["--server"]\n'
    if "[mcp_servers.db]" in content:
        # Already configured
        print(f"  ✓ Codex already configured: {cfg_file}")
        return

    updated = (content.rstrip() + "\n" + entry).lstrip()
    cfg_file.write_text(updated, encoding="utf-8")
    print(f"  ✓ Configured Codex: {cfg_file}")


# 7. Roo Code
def detect_roo_code() -> bool:
    cfg = get_roo_code_config_path()
    return cfg.exists() or cfg.parent.exists()


def install_roo_code(_instructions_file: Path):
    cfg_file = get_roo_code_config_path()
    data = read_json_file(cfg_file)
    mcp = data.get("mcpServers", {})
    mcp["db"] = {
        "command": "db-grep",
        "args": ["--server"],
        "disabled": False,
        "autoApprove": [],
    }
    data["mcpServers"] = mcp
    write_json_file(cfg_file, data)
    print(f"  ✓ Configured Roo Code: {cfg_file}")


# 8. Zed Editor
def detect_zed() -> bool:
    cfg = get_zed_config_path()
    return cfg.exists() or cfg.parent.exists() or bool(shutil.which("zed"))


def install_zed(_instructions_file: Path):
    cfg_file = get_zed_config_path()
    data = read_json_file(cfg_file)
    context_servers = data.get("context_servers", {})
    context_servers["db"] = {
        "command": {
            "path": "db-grep",
            "args": ["--server"],
        }
    }
    data["context_servers"] = context_servers
    write_json_file(cfg_file, data)
    print(f"  ✓ Configured Zed Editor: {cfg_file}")


# 9. Pi
def detect_pi() -> bool:
    agent_dir = get_pi_agent_dir()
    return agent_dir.exists() or bool(shutil.which("pi"))


def install_pi(_instructions_file: Path):
    cfg_file = get_pi_agent_dir() / "mcp.json"
    data = read_json_file(cfg_file)
    mcp = data.get("mcpServers", {})
    mcp["db"] = {
        "command": "db-grep",
        "args": ["--server"],
    }
    data["mcpServers"] = mcp
    write_json_file(cfg_file, data)
    print(f"  ✓ Configured Pi: {cfg_file}")


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
        "desc": "~/.config/opencode/opencode.jsonc",
        "detect": detect_opencode,
        "install": install_opencode,
    },
    {
        "id": "antigravity",
        "name": "Antigravity",
        "desc": "~/.gemini/config/mcp_config.json",
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
        "desc": lambda: str(get_pi_agent_dir() / "mcp.json"),
        "detect": detect_pi,
        "install": install_pi,
    },
]


def ask_user_choices(detected_map: dict) -> list[str]:
    print("\nSupported AI clients / harnesses:")

    detected_ids = []
    for idx, item in enumerate(HARNESS_REGISTRY, start=1):
        hid = item["id"]
        is_detected = detected_map.get(hid, False)
        status = "(detected)" if is_detected else "(not installed)"
        if is_detected:
            detected_ids.append(hid)
        desc_val = item["desc"]() if callable(item["desc"]) else item["desc"]
        print(f"  [{idx}] {hid:<16} - {item['name']} ({desc_val}) {status}")

    print("  [a]  all              - Force configure all harnesses")
    print("  [n]  none             - Skip harness configuration")

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


def run_init(target_harness: str = None):
    config_dir = Path("~/.config/db-grep").expanduser()
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

    # 3. Detect installed harnesses
    detected_map = {item["id"]: item["detect"]() for item in HARNESS_REGISTRY}

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
            targets = [hid for hid, detected in detected_map.items() if detected]
        else:
            targets = ask_user_choices(detected_map)

    print("\nApplying configuration to selected harnesses:")
    handler_map = {item["id"]: item["install"] for item in HARNESS_REGISTRY}
    for t in targets:
        if t in handler_map:
            handler_map[t](instructions_file)
        else:
            print(f"  ? Unknown harness: {t}")

    print("\nSetup complete! Next steps:")
    print(f"1. Add your database connections to: {config_file}")
    print("2. Run 'db-grep --monitor' in a terminal before starting your AI chat.")
