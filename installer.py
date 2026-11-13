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
approval_timeout_s = 110      # auto-reject after this many seconds without response

[defaults]
auto_metadata = true          # list_tables & describe_table without y/n
max_rows = 1000
timeout_ms = 10000

# ── Postgres Example 1: Full DSN with raw secret (ideal for local dev)
# [db.postgres.ecommerce-dev]
# description = "Development E-Commerce Database"
# dsn = "postgres://ro_user:rahasia@localhost:5432/ecommerce_dev"
# auto_tables = ["mst_*", "ref_*", "app_configs"]

# ── Postgres Example 2: DSN + credentials from environment (shorthand)
# [db.postgres.inventory-core]
# description = "Core Inventory Management"
# dsn = "postgres://localhost:5432/inventory?sslmode=require"
# user = "ro_user"
# password = "env:INVENTORY_PW"

# ── Postgres Example 3: Inline password in DSN from env/file (special chars are auto URL-encoded)
# [db.postgres.analytics-app]
# description = "Analytics Database"
# dsn = "postgres://ro_user:${env:ANALYTICS_PW}@localhost:5432/analytics"

# ── Oracle Example: Connect string + secret from file (or inline: ro_user/${env:PAYROLL_PW}@localhost:1521/PROD)
# [db.oracle.payroll-prod]
# description = "Corporate Payroll Database"
# dsn = "localhost:1521/PROD"  # or remote: "db-host:1521/PROD"
# user = "ro_user"
# password = "file:~/.config/db-grep/secrets/payroll"
# schema = "HR"
# auto_metadata = false       # structure inspection also requires y/n

# ── MySQL Example: URI DSN + credentials from environment
# [db.mysql.orders-app]
# description = "Orders MySQL Database"
# dsn = "mysql://ro_user:${env:ORDERS_PW}@localhost:3306/orders_db"
# auto_tables = ["mst_*", "ref_*"]
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


STD_MCP_ENTRY = {"command": "db-grep", "args": ["--server"]}


def _detect_path_or_bin(cfg: Path, binary: str | None = None, check_parent: bool = True) -> bool:
    return cfg.exists() or (check_parent and cfg.parent.exists()) or bool(binary and shutil.which(binary))


def _install_json_mcp(cfg_file: Path, label: str, section_key: str = "mcpServers", entry: dict | None = None):
    data = read_json_file(cfg_file)
    servers = data.get(section_key, {})
    servers["db"] = dict(entry or STD_MCP_ENTRY)
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
        entry={"command": {"path": "db-grep", "args": ["--server"]}},
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

    print(f"  [a] {'all':<16} - Force configure all harnesses")
    print(f"  [n] {'none':<16} - Skip for now (no harnesses configured)")

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
    print(f"1. Run 'db-grep --config add' (or 'db-grep --config') to add database connections (or edit {config_file})")
    print("2. Run 'db-grep --monitor' in a terminal before starting your AI chat.")
