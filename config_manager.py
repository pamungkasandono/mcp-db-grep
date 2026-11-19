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
        CONFIG.write_text("[monitor]\nport = 48260\n\n[defaults]\nallow_metadata = [\"read\"]\nallow_data = []\n\n", encoding="utf-8")
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

        if m_active or m_disabled:
            if m_active:
                driver, name = m_active.group(1), m_active.group(2)
                enabled, reason = True, ""
            else:
                reason = (m_disabled.group(1) or "").strip()
                driver, name = m_disabled.group(2), m_disabled.group(3)
                enabled = False

            start = i
            entry_lines = [line]
            i += 1
            while i < total and not SECTION_RE.match(raw_lines[i].strip()):
                entry_lines.append(raw_lines[i])
                i += 1
            entries.append(
                ConfigEntry(
                    f"{driver}:{name}",
                    driver,
                    name,
                    enabled=enabled,
                    reason=reason,
                    start_line=start,
                    end_line=i,
                    lines=entry_lines,
                )
            )
            continue

        i += 1

    return raw_lines, entries


def save_config_lines(lines: list[str]):
    ensure_config_exists()
    CONFIG.write_text("".join(lines), encoding="utf-8")
    if os.name == "posix":
        CONFIG.chmod(0o600)


from driver_manager import DRIVER_ALIASES, DRIVER_SHORTHANDS, SUPPORTED_DRIVERS


def normalize_db_token(token: str) -> str:
    t = (token or "").strip()
    if ":" in t:
        prefix, rest = t.split(":", 1)
        canonical = DRIVER_ALIASES.get(prefix.lower())
        if canonical:
            return f"{canonical}:{rest}"
    return t


def resolve_entry(target: str, entries: list[ConfigEntry]) -> ConfigEntry | None:
    """
    Resolve target to a single ConfigEntry.
    - Exact full key match or shorthand alias (e.g. 'postgres:inventory-core', 'pgsql:inventory-core') -> direct.
    - Short name match -> OK if exactly one found, error if ambiguous.
    - Prints error message and returns None on failure.
    """
    t = normalize_db_token(target)

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
            f"Please be specific, e.g.: db-grep config disable {matches[0].key}"
        )
        return None

    print(f"No database named '{target}' found in {CONFIG}")
    return None


find_entry = resolve_entry


def _load_target_entry(target: str) -> tuple[list[str], ConfigEntry | None]:
    raw_lines, entries = read_config_entries()
    return raw_lines, resolve_entry(target, entries)


def disable_entry(target: str, reason: str = "") -> bool:
    raw_lines, entry = _load_target_entry(target)
    if not entry:
        return False
    if not entry.enabled:
        print(f"Database '{entry.key}' is already disabled.")
        return True

    reason_tag = f": {reason.strip()}" if reason.strip() else ""
    new_lines = [f"# [DISABLED{reason_tag}] [db.{entry.driver}.{entry.name}]\n"]

    for line in entry.lines[1:]:
        if line.strip():
            new_lines.append(f"# {line.lstrip('# ')}")
        else:
            new_lines.append(line)

    save_config_lines(raw_lines[: entry.start_line] + new_lines + raw_lines[entry.end_line :])
    print(f"✓ Disabled '{entry.key}'" + (f" (Reason: {reason})" if reason else ""))
    return True


def enable_entry(target: str) -> bool:
    raw_lines, entry = _load_target_entry(target)
    if not entry:
        return False
    if entry.enabled:
        print(f"Database '{entry.key}' is already active.")
        return True

    new_lines = [f"[db.{entry.driver}.{entry.name}]\n"]

    for line in entry.lines[1:]:
        if line.strip().startswith("#"):
            new_lines.append(re.sub(r"^#\s?", "", line))
        else:
            new_lines.append(line)

    save_config_lines(raw_lines[: entry.start_line] + new_lines + raw_lines[entry.end_line :])
    print(f"✓ Enabled '{entry.key}'")
    return True


def toggle_entry(target: str, reason_prompt: bool = False) -> bool:
    _, entry = _load_target_entry(target)
    if not entry:
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
    return enable_entry(entry.key)


def delete_entry(target: str, confirm: bool = True) -> bool:
    raw_lines, entry = _load_target_entry(target)
    if not entry:
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

    save_config_lines(raw_lines[: entry.start_line] + raw_lines[entry.end_line :])
    print(f"✓ Deleted [{entry.key}] from {CONFIG}")
    return True


def add_allow_tables(target: str, tables: list[str]) -> bool:
    """Append tables to allow_tables list of a database entry in config.toml."""
    raw_lines, entry = _load_target_entry(target)
    if not entry:
        return False
    if not tables:
        return True

    allow_re = re.compile(r"^(\s*(?:allow_tables|auto_tables)\s*=\s*)\[(.*)\](.*)$")
    found_idx = -1
    existing_tables: list[str] = []

    for idx, line in enumerate(entry.lines):
        m = allow_re.match(line)
        if m:
            found_idx = idx
            raw_list = "[" + m.group(2) + "]"
            try:
                import json
                parsed = json.loads(raw_list.replace("'", '"'))
                if isinstance(parsed, list):
                    existing_tables = [str(x) for x in parsed]
            except Exception:
                existing_tables = [t.strip().strip("\"'") for t in m.group(2).split(",") if t.strip()]
            break

    merged = list(existing_tables)
    for t in tables:
        if t not in merged and t.lower() not in [x.lower() for x in merged]:
            merged.append(t)

    import json
    formatted_list = json.dumps(merged)
    new_line = f"allow_tables = {formatted_list}\n"

    new_lines = list(entry.lines)
    if found_idx >= 0:
        new_lines[found_idx] = new_line
    else:
        if len(new_lines) > 0:
            new_lines.insert(1, new_line)
        else:
            new_lines.append(new_line)

    save_config_lines(raw_lines[: entry.start_line] + new_lines + raw_lines[entry.end_line :])
    return True


add_auto_tables = add_allow_tables


# ---------------- Config Migration Utilities (v0.7.x -> v0.8.0+) ----------------

LEGACY_KEY_PATTERNS = [
    (re.compile(r"^(\s*)(?:read_metadata|auto_metadata)\s*=\s*true(\s*(?:#.*)?)$", re.IGNORECASE), r'\1allow_metadata = ["read"]\2'),
    (re.compile(r"^(\s*)(?:read_metadata|auto_metadata)\s*=\s*false(\s*(?:#.*)?)$", re.IGNORECASE), r'\1allow_metadata = []\2'),
    (re.compile(r"^(\s*)read_data\s*=\s*true(\s*(?:#.*)?)$", re.IGNORECASE), r'\1allow_data = ["read"]\2'),
    (re.compile(r"^(\s*)read_data\s*=\s*false(\s*(?:#.*)?)$", re.IGNORECASE), r'\1allow_data = []\2'),
    (re.compile(r"^(\s*)auto_tables\s*=", re.IGNORECASE), r'\1allow_tables ='),
    (re.compile(r"^(\s*)always_ask_tables\s*=", re.IGNORECASE), r'\1restricted_tables ='),
    (re.compile(r"^(\s*)sensitive_columns\s*=", re.IGNORECASE), r'\1restricted_columns ='),
]

LEGACY_KEY_NAMES = {
    "read_metadata", "auto_metadata", "read_data",
    "auto_tables", "always_ask_tables", "sensitive_columns",
}


def detect_legacy_keys(content: str) -> list[str]:
    """Return list of legacy configuration keys found in content."""
    detected = set()
    for line in content.splitlines():
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("#"):
            continue
        key = line_clean.split("=", 1)[0].strip()
        if key in LEGACY_KEY_NAMES:
            detected.add(key)
    return sorted(detected)


def migrate_config_content(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Transform legacy configuration lines to v0.8.0 format while preserving comments."""
    lines = content.splitlines(keepends=True)
    new_lines = []
    changes = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            new_lines.append(line)
            continue

        migrated_line = line
        for pat, repl in LEGACY_KEY_PATTERNS:
            if pat.search(line):
                migrated_line = pat.sub(repl, line)
                changes.append((line.strip(), migrated_line.strip()))
                break
        new_lines.append(migrated_line)

    return "".join(new_lines), changes


def migrate_config_file(interactive: bool = True) -> bool:
    """Migrate config.toml from legacy format to v0.8.0 format."""
    if not CONFIG.exists():
        print(f"Config file not found: {CONFIG}")
        return False

    content = CONFIG.read_text(encoding="utf-8")
    legacy_keys = detect_legacy_keys(content)
    if not legacy_keys:
        print(f"✓ {CONFIG} is already up to date (no legacy keys detected).")
        return True

    print(f"\nDetected {len(legacy_keys)} legacy key(s) in {CONFIG}:")
    for k in legacy_keys:
        print(f"  - {k}")

    if interactive:
        try:
            choice = input("\nMigrate configuration to v0.8.0 format? [Y/n]: ").strip().lower()
            if choice not in ("", "y", "yes"):
                print("Migration cancelled.")
                return False
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return False

    migrated_content, changes = migrate_config_content(content)

    # 1. Backup existing config
    bak_path = CONFIG.with_suffix(".toml.bak")
    try:
        bak_path.write_text(content, encoding="utf-8")
        if os.name == "posix":
            bak_path.chmod(0o600)
    except Exception as e:
        print(f"Error creating backup {bak_path}: {e}")
        return False

    # 2. Save migrated config
    try:
        CONFIG.write_text(migrated_content, encoding="utf-8")
        if os.name == "posix":
            CONFIG.chmod(0o600)
    except Exception as e:
        print(f"Error writing migrated config {CONFIG}: {e}")
        return False

    print(f"\n✓ Successfully migrated {CONFIG} to v0.8.0 format:")
    for old_val, new_val in changes:
        print(f"  • {old_val}  →  {new_val}")
    print(f"✓ Backup created at {bak_path}\n")
    return True


DEFAULT_DSNS = {
    "postgres": "postgres://ro_user:password@localhost:5432/dbname",
    "oracle": "localhost:1521/ORCL",
    "mysql": "mysql://ro_user:password@localhost:3306/dbname",
}


def add_entry(
    driver: str,
    name: str,
    dsn: str,
    description: str = "",
    user: str = "",
    password: str = "",
    allow_metadata: list[str] | None = None,
    allow_data: list[str] | None = None,
    allow_tables: list[str] | None = None,
    restricted_tables: list[str] | None = None,
    restricted_columns: list[str] | None = None,
) -> bool:
    import json
    driver = DRIVER_ALIASES.get(driver.lower().strip(), driver.lower().strip())
    if driver not in SUPPORTED_DRIVERS:
        print(f"Error: Unsupported driver '{driver}'. Supported drivers: {', '.join(SUPPORTED_DRIVERS)}")
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
    if allow_metadata is not None:
        block.append(f"allow_metadata = {json.dumps(allow_metadata)}\n")
    if allow_data is not None:
        block.append(f"allow_data = {json.dumps(allow_data)}\n")
    if allow_tables:
        block.append(f"allow_tables = {json.dumps(allow_tables)}\n")
    if restricted_tables:
        block.append(f"restricted_tables = {json.dumps(restricted_tables)}\n")
    if restricted_columns:
        block.append(f"restricted_columns = {json.dumps(restricted_columns)}\n")

    updated_full = raw_lines + block
    save_config_lines(updated_full)
    print(f"✓ Added [{key}] to {CONFIG}")
    return True


def prompt_add_interactive() -> bool:
    print("\n--- Add New Database ---")
    try:
        drv_choice = input("Driver [1: postgres/pgsql (default), 2: oracle/ora, 3: mysql/my]: ").strip().lower()
        if drv_choice in ("2", "oracle", "ora"):
            driver = "oracle"
        elif drv_choice in ("3", "mysql", "my"):
            driver = "mysql"
        else:
            driver = "postgres"

        name = input(f"Database name (e.g. {'payroll-prod' if driver == 'oracle' else 'inventory-core'}): ").strip()
        if not name:
            print("Cancelled: Name cannot be empty.")
            return False

        default_dsn = DEFAULT_DSNS[driver]
        dsn = input(f"DSN / Connection string [e.g. {default_dsn}]: ").strip()
        if not dsn:
            print("Cancelled: DSN cannot be empty.")
            return False

        desc = input("Description (optional): ").strip()
        user = ""
        if driver == "oracle":
            user = input("User (required for Oracle if not in DSN): ").strip()

        pw = input("Password [plain, or 'env:NAME', or 'file:PATH', Enter to skip]: ").strip()
        allow_tables_in = input("Allow tables [optional, comma-separated e.g. mst_*,ref_*]: ").strip()
        allow_tables = [t.strip() for t in allow_tables_in.split(",") if t.strip()] if allow_tables_in else None

        return add_entry(driver, name, dsn, description=desc, user=user, password=pw, allow_tables=allow_tables)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return False


def view_raw_config():
    ensure_config_exists()
    custom_editor = os.environ.get("EDITOR")
    if custom_editor:
        print(f"\nOpening {CONFIG} in {custom_editor}...")
        try:
            subprocess.run([custom_editor, str(CONFIG)], check=False)
            return
        except Exception as e:
            print(f"Could not launch editor {custom_editor}: {e}")

    if sys.platform == "win32":
        print(f"\nOpening {CONFIG} in notepad...")
        try:
            subprocess.Popen(
                ["notepad", str(CONFIG)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            return
        except Exception as e:
            if hasattr(os, "startfile"):
                try:
                    os.startfile(str(CONFIG))
                    return
                except Exception:
                    pass
            print(f"Could not launch editor notepad: {e}")
    else:
        editor = "nano"
        print(f"\nOpening {CONFIG} in {editor}...")
        try:
            subprocess.run([editor, str(CONFIG)])
            return
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

        has_legacy = bool(detect_legacy_keys(CONFIG.read_text(encoding="utf-8"))) if CONFIG.exists() else False

        print("\nActions:")
        print("  [1-N] Toggle active/disabled")
        print("  [a]   Add new database")
        print("  [d]   Delete database")
        if has_legacy:
            print("  [m]   Migrate legacy config to v0.8.0")
        print("  [v]   Open in editor / view raw")
        print("  [q]   Quit")

        prompt_opts = "1-N, a, d, m, v, q" if has_legacy else "1-N, a, d, v, q"
        try:
            choice = input(f"\nSelect option [{prompt_opts}]: ").strip().lower()
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
        elif choice == "m":
            migrate_config_file(interactive=True)
        elif choice in ("v", "e"):
            view_raw_config()
        else:
            print("Unknown action.")
