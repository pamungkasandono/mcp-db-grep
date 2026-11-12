import os
import re
import subprocess
import sys
from pathlib import Path

CONFIG = Path(
    os.environ.get("DB_GREP_CONFIG", os.environ.get("DB_MCP_CONFIG", "~/.config/db-grep/config.toml"))
).expanduser()

HEADER_ACTIVE_RE = re.compile(r"^\[db\.([a-zA-Z0-9_-]+)\.([a-zA-Z0-9_-]+)\]")
HEADER_DISABLED_RE = re.compile(
    r"^#\s*\[DISABLED(?::\s*([^\]]+))?\]\s*\[db\.([a-zA-Z0-9_-]+)\.([a-zA-Z0-9_-]+)\]"
)
SECTION_RE = re.compile(r"^(\[db\.|#\s*\[DISABLED.*\]\s*\[db\.|\[monitor\]|\[defaults\])")


class ConfigEntry:
    def __init__(self, key: str, driver: str, name: str, enabled: bool, reason: str = "", start_line: int = 0, end_line: int = 0, lines: list[str] | None = None):
        self.key = key
        self.driver = driver
        self.name = name
        self.enabled = enabled
        self.reason = reason
        self.start_line = start_line
        self.end_line = end_line
        self.lines = lines or []

    def get_field(self, field_name: str) -> str:
        pattern = re.compile(rf"^#?\s*{field_name}\s*=\s*(.+)$")
        for line in self.lines:
            m = pattern.match(line.strip())
            if m:
                val = m.group(1).strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    return val[1:-1]
                return val
        return ""


def ensure_config_exists() -> Path:
    if not CONFIG.exists():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            CONFIG.parent.chmod(0o700)
        CONFIG.write_text("[monitor]\nport = 48260\n\n[defaults]\nauto_metadata = true\n\n", encoding="utf-8")
        if os.name == "posix":
            CONFIG.chmod(0o600)
    return CONFIG


def read_config_entries() -> tuple[list[str], list[ConfigEntry]]:
    ensure_config_exists()
    content = CONFIG.read_text(encoding="utf-8")
    raw_lines = content.splitlines(keepends=True)

    entries: list[ConfigEntry] = []
    i = 0
    total = len(raw_lines)

    while i < total:
        line = raw_lines[i]
        stripped = line.strip()

        m_active = HEADER_ACTIVE_RE.match(stripped)
        m_disabled = HEADER_DISABLED_RE.match(stripped)

        if m_active:
            driver, name = m_active.group(1), m_active.group(2)
            key = f"{driver}:{name}"
            start = i
            i += 1
            entry_lines = [line]
            while i < total:
                next_stripped = raw_lines[i].strip()
                if SECTION_RE.match(next_stripped):
                    break
                entry_lines.append(raw_lines[i])
                i += 1
            entries.append(ConfigEntry(key, driver, name, enabled=True, start_line=start, end_line=i, lines=entry_lines))
            continue
        elif m_disabled:
            reason = (m_disabled.group(1) or "").strip()
            driver, name = m_disabled.group(2), m_disabled.group(3)
            key = f"{driver}:{name}"
            start = i
            i += 1
            entry_lines = [line]
            while i < total:
                next_stripped = raw_lines[i].strip()
                if SECTION_RE.match(next_stripped):
                    break
                entry_lines.append(raw_lines[i])
                i += 1
            entries.append(ConfigEntry(key, driver, name, enabled=False, reason=reason, start_line=start, end_line=i, lines=entry_lines))
            continue

        i += 1

    return raw_lines, entries


def save_config_lines(lines: list[str]):
    ensure_config_exists()
    CONFIG.write_text("".join(lines), encoding="utf-8")
    if os.name == "posix":
        CONFIG.chmod(0o600)


def resolve_entry(target: str, entries: list[ConfigEntry]) -> ConfigEntry | None:
    """
    Resolve target to a single ConfigEntry.
    - Exact full key match (e.g. 'postgres:inventory-core') -> direct.
    - Short name match -> OK if exactly one found, error if ambiguous.
    - Prints error message and returns None on failure.
    """
    t = target.strip()

    # 1. Exact full key match
    for e in entries:
        if e.key == t:
            return e

    # 2. Short name: collect all matches
    matches = [e for e in entries if e.name == t or e.key.endswith(f":{t}")]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(e.key for e in matches)
        print(
            f"Found {len(matches)} databases named '{t}': {names}\n"
            f"Please be specific, e.g.: db-grep --config disable {matches[0].key}"
        )
        return None

    print(f"No database named '{target}' found in {CONFIG}")
    return None


def find_entry(target: str, entries: list[ConfigEntry]) -> ConfigEntry | None:
    return resolve_entry(target, entries)


def disable_entry(target: str, reason: str = "") -> bool:
    raw_lines, entries = read_config_entries()
    entry = find_entry(target, entries)
    if not entry:
        print(f"Error: Database '{target}' not found in {CONFIG}")
        return False
    if not entry.enabled:
        print(f"Database '{entry.key}' is already disabled.")
        return True

    new_lines = []
    reason_tag = f": {reason.strip()}" if reason.strip() else ""
    header_comment = f"# [DISABLED{reason_tag}] [db.{entry.driver}.{entry.name}]\n"
    new_lines.append(header_comment)

    for line in entry.lines[1:]:
        if line.strip():
            new_lines.append(f"# {line.lstrip('# ')}")
        else:
            new_lines.append(line)

    updated_full = raw_lines[: entry.start_line] + new_lines + raw_lines[entry.end_line :]
    save_config_lines(updated_full)
    print(f"✓ Disabled '{entry.key}'" + (f" (Reason: {reason})" if reason else ""))
    return True


def enable_entry(target: str) -> bool:
    raw_lines, entries = read_config_entries()
    entry = find_entry(target, entries)
    if not entry:
        print(f"Error: Database '{target}' not found in {CONFIG}")
        return False
    if entry.enabled:
        print(f"Database '{entry.key}' is already active.")
        return True

    new_lines = []
    new_lines.append(f"[db.{entry.driver}.{entry.name}]\n")

    for line in entry.lines[1:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            uncommented = re.sub(r"^#\s?", "", line)
            new_lines.append(uncommented)
        else:
            new_lines.append(line)

    updated_full = raw_lines[: entry.start_line] + new_lines + raw_lines[entry.end_line :]
    save_config_lines(updated_full)
    print(f"✓ Enabled '{entry.key}'")
    return True


def toggle_entry(target: str, reason_prompt: bool = False) -> bool:
    _, entries = read_config_entries()
    entry = find_entry(target, entries)
    if not entry:
        print(f"Error: Database '{target}' not found in {CONFIG}")
        return False

    if entry.enabled:
        reason = ""
        if reason_prompt:
            try:
                reason = input(f"Reason for disabling '{entry.key}' (optional, Enter to skip): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                return False
        return disable_entry(entry.key, reason)
    else:
        return enable_entry(entry.key)


def delete_entry(target: str, confirm: bool = True) -> bool:
    raw_lines, entries = read_config_entries()
    entry = find_entry(target, entries)
    if not entry:
        print(f"Error: Database '{target}' not found in {CONFIG}")
        return False

    if confirm:
        try:
            ans = input(f"Are you sure you want to delete [{entry.key}] permanently? [y/N]: ").strip().lower()
            if ans != "y":
                print("Deletion cancelled.")
                return False
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return False

    updated_full = raw_lines[: entry.start_line] + raw_lines[entry.end_line :]
    save_config_lines(updated_full)
    print(f"✓ Deleted [{entry.key}] from {CONFIG}")
    return True


def add_entry(
    driver: str,
    name: str,
    dsn: str,
    description: str = "",
    user: str = "",
    password: str = "",
    auto_metadata: str = "",
    auto_tables: list[str] | None = None,
) -> bool:
    driver = driver.lower().strip()
    if driver not in ("postgres", "oracle"):
        print(f"Error: Unsupported driver '{driver}'. Supported drivers: postgres, oracle")
        return False

    name = re.sub(r"[^a-zA-Z0-9_-]", "-", name.strip().lower())
    if not name:
        print("Error: Database name cannot be empty.")
        return False

    raw_lines, entries = read_config_entries()
    key = f"{driver}:{name}"
    existing = next((e for e in entries if e.key == key), None)
    if existing:
        print(f"Error: Database '{key}' already exists in {CONFIG}")
        return False

    block = [f"\n[db.{driver}.{name}]\n"]
    if description.strip():
        block.append(f'description = "{description.strip()}"\n')
    block.append(f'dsn = "{dsn.strip()}"\n')
    if user.strip():
        block.append(f'user = "{user.strip()}"\n')
    if password.strip():
        block.append(f'password = "{password.strip()}"\n')
    if auto_metadata.lower() in ("true", "false"):
        block.append(f"auto_metadata = {auto_metadata.lower()}\n")
    if auto_tables:
        formatted = ", ".join(f'"{t}"' for t in auto_tables)
        block.append(f"auto_tables = [{formatted}]\n")

    updated_full = raw_lines + block
    save_config_lines(updated_full)
    print(f"✓ Added [{key}] to {CONFIG}")
    return True


def prompt_add_interactive() -> bool:
    print("\n--- Add New Database ---")
    try:
        drv_choice = input("Driver [1: postgres (default), 2: oracle]: ").strip()
        driver = "oracle" if drv_choice == "2" or drv_choice.lower() == "oracle" else "postgres"

        name = input(f"Database name (e.g. {'inventory-core' if driver == 'postgres' else 'payroll-prod'}): ").strip()
        if not name:
            print("Cancelled: Name cannot be empty.")
            return False

        default_dsn = "postgres://ro_user:password@localhost:5432/dbname" if driver == "postgres" else "localhost:1521/ORCL"
        dsn = input(f"DSN / Connection string [e.g. {default_dsn}]: ").strip()
        if not dsn:
            print("Cancelled: DSN cannot be empty.")
            return False

        desc = input("Description (optional): ").strip()
        user = ""
        if driver == "oracle":
            user = input("User (required for Oracle if not in DSN): ").strip()

        pw = input("Password [plain, or 'env:NAME', or 'file:PATH', Enter to skip]: ").strip()
        auto_tables_in = input("Auto-allow tables [optional, comma-separated e.g. mst_*,ref_*]: ").strip()
        auto_tables = [t.strip() for t in auto_tables_in.split(",") if t.strip()] if auto_tables_in else None

        return add_entry(driver, name, dsn, description=desc, user=user, password=pw, auto_tables=auto_tables)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return False


def view_raw_config():
    ensure_config_exists()
    editor = os.environ.get("EDITOR") or ("nano" if os.name == "posix" else "notepad")
    print(f"\nOpening {CONFIG} in {editor}...")
    try:
        subprocess.run([editor, str(CONFIG)])
    except Exception as e:
        print(f"Could not launch editor {editor}: {e}")
        print("\n--- Raw config.toml content ---")
        print(CONFIG.read_text(encoding="utf-8"))


def run_interactive_dashboard():
    while True:
        raw_lines, entries = read_config_entries()
        active = [e for e in entries if e.enabled]
        disabled = [e for e in entries if not e.enabled]

        all_display = entries

        print(f"\n━━ db-grep Configuration Manager ━━")
        print(f"File: {CONFIG}")

        if not all_display:
            print("\n(No databases configured yet)")
        else:
            if active:
                print("\nActive Databases:")
                for idx, e in enumerate(all_display, start=1):
                    if e.enabled:
                        desc = e.get_field("description")
                        dsn = e.get_field("dsn")
                        dsn_disp = f"({dsn[:40]}...)" if len(dsn) > 40 else f"({dsn})" if dsn else ""
                        desc_disp = f" - {desc}" if desc else ""
                        print(f"  [{idx}] {e.key:<28} {dsn_disp}{desc_disp}")

            if disabled:
                print("\nDisabled Databases:")
                for idx, e in enumerate(all_display, start=1):
                    if not e.enabled:
                        reason = f" # [DISABLED: {e.reason}]" if e.reason else " # [DISABLED]"
                        print(f"  [{idx}] {e.key:<28}{reason}")

        print("\nActions:")
        print("  [1-N] Toggle active/disabled")
        print("  [a]   Add new database")
        print("  [d]   Delete database")
        print("  [v]   Open in editor / view raw")
        print("  [q]   Quit")

        try:
            choice = input("\nSelect option [1-N, a, d, v, q]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not choice or choice in ("q", "quit", "exit"):
            break
        elif choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(all_display):
                target = all_display[idx - 1]
                toggle_entry(target.key, reason_prompt=target.enabled)
            else:
                print("Invalid number selection.")
        elif choice == "a":
            prompt_add_interactive()
        elif choice == "d":
            target_in = input("Number or key of database to delete: ").strip()
            if target_in.isdigit():
                idx = int(target_in)
                if 1 <= idx <= len(all_display):
                    delete_entry(all_display[idx - 1].key, confirm=True)
                else:
                    print("Invalid index.")
            elif target_in:
                delete_entry(target_in, confirm=True)
        elif choice in ("v", "e"):
            view_raw_config()
        else:
            print("Unknown action.")
