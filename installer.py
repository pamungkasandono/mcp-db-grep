import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_INSTRUCTIONS = """# Database MCP (`db-grep`) Handling Rules

When any tool from `db` server returns an error containing `ACTION_DENIED_BY_USER`:

1. **Stop Tool Calls Immediately**:
   - Never retry the rejected query.
   - Never call any other `db` tool for the current request.

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
port = 8765

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


def get_claude_desktop_config_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path("~").expanduser() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "darwin":
        return Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
    else:
        return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


def detect_harnesses():
    detected = {}

    # Opencode
    opencode_dir = Path("~/.config/opencode").expanduser()
    if opencode_dir.exists():
        detected["opencode"] = str(opencode_dir)

    # Claude CLI / Claude Code
    if shutil.which("claude"):
        detected["claude"] = "Claude Code CLI detected"

    # Claude Desktop
    claude_desktop_cfg = get_claude_desktop_config_path()
    # Check if config file exists or parent app directory exists
    if claude_desktop_cfg.exists() or claude_desktop_cfg.parent.exists():
        detected["claude-desktop"] = str(claude_desktop_cfg)

    return detected


def install_opencode(instructions_file: Path):
    opencode_dir = Path("~/.config/opencode").expanduser()
    opencode_dir.mkdir(parents=True, exist_ok=True)
    target_json = opencode_dir / "opencode.jsonc"
    if not target_json.exists():
        alt = opencode_dir / "opencode.json"
        if alt.exists():
            target_json = alt
        else:
            target_json = opencode_dir / "opencode.jsonc"

    data = {}
    if target_json.exists():
        try:
            with open(target_json, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

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

    with open(target_json, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  ✓ Configured Opencode: {target_json}")


def install_claude_cli():
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


def install_claude_desktop():
    cfg_file = get_claude_desktop_config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if cfg_file.exists():
        try:
            with open(cfg_file, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

    mcp = data.get("mcpServers", {})
    mcp["db"] = {
        "command": "db-grep",
        "args": ["--server"],
    }
    data["mcpServers"] = mcp

    with open(cfg_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  ✓ Configured Claude Desktop: {cfg_file}")


def ask_user_choices(detected: dict) -> list[str]:
    print("\nSupported AI clients / harnesses:")
    harnesses = ["opencode", "claude", "claude-desktop"]
    descriptions = {
        "opencode": "Opencode CLI (~/.config/opencode)",
        "claude": "Claude Code CLI (`claude mcp add`)",
        "claude-desktop": f"Claude Desktop App ({get_claude_desktop_config_path()})",
    }

    detected_keys = [k for k in harnesses if k in detected]
    detected_indices = []

    for idx, key in enumerate(harnesses, start=1):
        is_detected = key in detected
        status = "(detected)" if is_detected else "(not installed)"
        if is_detected:
            detected_indices.append(str(idx))
        print(f"  [{idx}] {key:<15} - {descriptions[key]} {status}")

    print("  [a] all             - Force configure all harnesses")
    print("  [n] none            - Skip harness configuration")

    if detected_keys:
        default_label = f"detected only ({', '.join(detected_keys)})"
    else:
        default_label = "none"

    try:
        choice = input(f"\nSelect harness to configure [default: {default_label}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipping harness configuration.")
        return []

    # If user pressed enter, default to detected only
    if not choice:
        return detected_keys

    choice_lower = choice.lower()
    if choice_lower in ("a", "all"):
        return harnesses
    if choice_lower in ("n", "none"):
        return []

    selected = []
    tokens = [t.strip() for t in choice.replace(",", " ").split()]
    for t in tokens:
        if t == "1" or t.lower() == "opencode":
            selected.append("opencode")
        elif t == "2" or t.lower() == "claude":
            selected.append("claude")
        elif t == "3" or t.lower() == "claude-desktop":
            selected.append("claude-desktop")

    return list(dict.fromkeys(selected))


def run_init(target_harness: str = None):
    config_dir = Path("~/.config/db-grep").expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        config_dir.chmod(0o700)

    # 1. Write config.toml if missing
    config_file = config_dir / "config.toml"
    if not config_file.exists():
        config_file.write_text(SAMPLE_CONFIG)
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
        instructions_file.write_text(DEFAULT_INSTRUCTIONS)
    print(f"✓ Installed instructions: {instructions_file}")

    # 3. Determine which harnesses to install
    detected = detect_harnesses()
    if target_harness:
        target = target_harness.lower()
        if target == "all":
            targets = ["opencode", "claude", "claude-desktop"]
        elif target == "none":
            targets = []
        else:
            targets = [t.strip() for t in target.split(",") if t.strip()]
    else:
        # If running in non-interactive environment (pipes/ci)
        if not sys.stdin.isatty():
            targets = [k for k in detected.keys() if k in ("opencode", "claude", "claude-desktop")]
        else:
            targets = ask_user_choices(detected)

    print("\nApplying configuration to selected harnesses:")
    for t in targets:
        if t == "opencode":
            install_opencode(instructions_file)
        elif t == "claude":
            install_claude_cli()
        elif t == "claude-desktop":
            install_claude_desktop()
        else:
            print(f"  ? Unknown harness: {t}")

    print("\nSetup complete! Next steps:")
    print(f"1. Add your database connections to: {config_file}")
    print("2. Run 'db-grep --monitor' in a terminal before starting your AI chat.")
