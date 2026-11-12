import argparse
import sys
from pathlib import Path

# Ensure db-grep directory is on sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from updater import get_current_version

__version__ = get_current_version()


def parse_selection_tokens(user_input: str, total_items: int) -> list[int]:
    selected_indices = []
    tokens = [t.strip() for t in user_input.replace(";", " ").replace(",", " ").split() if t.strip()]

    for token in tokens:
        if "-" in token:
            parts = token.split("-", 1)
            if parts[0].isdigit() and parts[1].isdigit():
                start = int(parts[0])
                end = int(parts[1])
                if start > end:
                    start, end = end, start
                for idx in range(start, end + 1):
                    if 1 <= idx <= total_items:
                        selected_indices.append(idx - 1)
        elif token.isdigit():
            idx = int(token)
            if 1 <= idx <= total_items:
                selected_indices.append(idx - 1)

    # Return deduplicated indices preserving order
    seen = set()
    result = []
    for i in selected_indices:
        if i not in seen:
            seen.add(i)
            result.append(i)
    return result


def run_check_config_cli():
    import server
    report = server.inspect_config()

    print("\n--- db-grep Configuration Check ---")
    print(f"File: {report['config_file']}")
    if report.get("permissions"):
        print(f"Permissions: {report['permissions']}")
    print(f"Monitor Port: {report.get('monitor_port')}")

    if report["warnings"]:
        print("\nWarnings:")
        for w in report["warnings"]:
            print(f"  ⚠ {w}")

    if report["errors"]:
        print("\nErrors:")
        for e in report["errors"]:
            print(f"  ✗ {e}")

    print("\nRegistered Databases:")
    for d in report["databases"]:
        status = "✓ OK" if d["valid"] else "✗ INVALID"
        print(f"  [{status}] {d['key']} ({d['driver']})")
        if d.get("description"):
            print(f"         {d['description']}")
        if d.get("auth_source"):
            print(f"         Auth: {d['auth_source']}")
        if "auto_metadata" in d:
            print(f"         Auto-Metadata: {d['auto_metadata']}")
        if d.get("auto_tables"):
            print(f"         Auto-Tables: {', '.join(d['auto_tables'])}")
        for err in d.get("errors", []):
            print(f"         Error: {err}")

    if report["valid"]:
        print("\nResult: Configuration is VALID.\n")
        sys.exit(0)
    else:
        print("\nResult: Configuration is INVALID. Please correct the errors above.\n")
        sys.exit(1)


def run_check_connection_cli(targets: list[str] | str):
    import server

    try:
        all_dbs = server.databases()
    except Exception as e:
        print(f"Cannot load database configurations: {e}")
        sys.exit(1)

    db_keys = list(all_dbs.keys())
    if not db_keys:
        print("No databases configured in config.toml")
        sys.exit(0)

    # Normalize targets to list of strings
    if isinstance(targets, str):
        target_list = [targets]
    else:
        target_list = list(targets)

    selected_keys = []

    # If no targets passed, enter interactive selection
    if not target_list:
        print("\n--- db-grep Interactive Connection Check ---")
        print("Available Databases:")
        for idx, key in enumerate(db_keys, start=1):
            desc = all_dbs[key].get("description", "")
            desc_str = f" - {desc}" if desc else ""
            print(f"  [{idx}] {key:<25}{desc_str}")

        print("  [a] all                       - Check all databases")
        print("  [q] quit                      - Cancel")

        try:
            choice = input("\nSelect database to test [default: all, e.g. 1, 1,3, 1-3, or 'all']: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            sys.exit(0)

        if not choice or choice.lower() in ("a", "all", "*"):
            selected_keys = db_keys
        elif choice.lower() in ("q", "quit", "exit"):
            print("Cancelled.")
            sys.exit(0)
        else:
            indices = parse_selection_tokens(choice, len(db_keys))
            if not indices:
                print("No valid database index selected.")
                sys.exit(1)
            selected_keys = [db_keys[i] for i in indices]
    elif len(target_list) == 1 and target_list[0].lower() in ("all", "*"):
        selected_keys = db_keys
    else:
        # Flatten all tokens across multiple positional items and comma separations
        tokens = []
        for item in target_list:
            tokens.extend([t.strip() for t in item.replace(",", " ").split() if t.strip()])

        for t in tokens:
            for resolved in server.resolve_db_keys_for_check(t, all_dbs):
                if resolved not in selected_keys:
                    selected_keys.append(resolved)

    print(f"\nChecking {len(selected_keys)} database connection(s)...")
    has_failure = False

    for key in selected_keys:
        cfg = all_dbs[key]
        res = server.ping_database(key, cfg)
        if res["status"] == "connected":
            print(f"  ✓ {res['db']:<25} [{res['type']}] Connected ({res['latency_ms']} ms) - Query: '{res['query']}'")
        else:
            has_failure = True
            print(f"  ✗ {res['db']:<25} [{res['type']}] Failed - Query: '{res['query']}'\n     Error: {res['error']}")

    print()
    sys.exit(1 if has_failure else 0)


def main():
    parser = argparse.ArgumentParser(
        prog="db-grep",
        description=f"db-grep v{__version__} - Database MCP CLI: Run MCP Server or Terminal Monitor",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--server",
        action="store_true",
        help="Run MCP stdio server for AI clients (Claude/opencode)",
    )
    group.add_argument(
        "--monitor",
        action="store_true",
        help="Run approval monitor interactive CLI",
    )
    group.add_argument(
        "--init",
        action="store_true",
        help="Initialize default config, rules, and register with AI clients",
    )
    group.add_argument(
        "--config",
        nargs="*",
        metavar="CMD",
        help="Manage databases in config.toml (interactive dashboard, or subcommands: list, add, disable, enable, delete, edit)",
    )
    group.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration syntax, driver definitions, permissions, and credentials",
    )
    group.add_argument(
        "--check-connection",
        nargs="*",
        metavar="DB",
        help="Test physical database connectivity (no args = interactive, or pass 'all', or multiple DB names separated by spaces)",
    )
    group.add_argument(
        "--check-update",
        action="store_true",
        help="Check for latest releases and changelog on GitHub",
    )
    group.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade db-grep to the latest version from GitHub",
    )

    parser.add_argument(
        "--client",
        choices=[
            "claude",
            "claude-desktop",
            "opencode",
            "antigravity",
            "cursor",
            "codex",
            "roo-code",
            "zed",
            "pi",
            "all",
            "none",
        ],
        help="Target AI client to configure when using --init (skip interactive prompt)",
    )

    args = parser.parse_args()

    if args.server:
        import server
        server.start()
    elif args.monitor:
        import monitor
        monitor.start()
    elif args.init:
        import installer
        installer.run_init(target_harness=args.client)
    elif args.config is not None:
        import config_manager
        cfg_args = args.config
        if not cfg_args:
            config_manager.run_interactive_dashboard()
        else:
            sub = cfg_args[0].lower()
            if sub in ("list", "ls"):
                raw, entries = config_manager.read_config_entries()
                if not entries:
                    print("No databases configured.")
                for idx, e in enumerate(entries, 1):
                    status = "✓ ACTIVE" if e.enabled else f"# DISABLED ({e.reason})" if e.reason else "# DISABLED"
                    print(f"[{idx}] {e.key:<28} [{status}]")
            elif sub == "disable":
                if len(cfg_args) < 2:
                    print("Usage: db-grep --config disable <DB> [reason]")
                else:
                    target = cfg_args[1]
                    reason = " ".join(cfg_args[2:]) if len(cfg_args) > 2 else ""
                    config_manager.disable_entry(target, reason)
            elif sub == "enable":
                if len(cfg_args) < 2:
                    print("Usage: db-grep --config enable <DB>")
                else:
                    config_manager.enable_entry(cfg_args[1])
            elif sub in ("del", "delete", "rm"):
                if len(cfg_args) < 2:
                    print("Usage: db-grep --config delete <DB>")
                else:
                    config_manager.delete_entry(cfg_args[1], confirm=False)
            elif sub == "add":
                config_manager.prompt_add_interactive()
            elif sub in ("view", "edit"):
                config_manager.view_raw_config()
            else:
                print(f"Unknown config command: {sub}")
                print("Available commands: list, add, disable, enable, delete, edit")
    elif args.check_config:
        run_check_config_cli()
    elif args.check_connection is not None:
        run_check_connection_cli(args.check_connection)
    elif args.check_update:
        import updater
        updater.run_check_update_cli(prompt_upgrade=True)
    elif args.upgrade:
        import updater
        updater.execute_upgrade()
    else:
        print(f"db-grep v{__version__}\n")
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
