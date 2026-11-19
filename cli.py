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


def _print_connection_results(results: list[dict]) -> bool:
    has_failure = False
    for res in results:
        if res["status"] == "connected":
            print(f"  ✓ {res['db']:<25} [{res['type']}] Connected ({res['latency_ms']} ms) - Query: '{res['query']}'")
        else:
            has_failure = True
            print(f"  ✗ {res['db']:<25} [{res['type']}] Failed - Query: '{res['query']}'\n     Error: {res['error']}")
    return has_failure


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
    target_list = [targets] if isinstance(targets, str) else list(targets)

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
            target_list = ["all"]
        elif choice.lower() in ("q", "quit", "exit"):
            print("Cancelled.")
            sys.exit(0)
        else:
            indices = parse_selection_tokens(choice, len(db_keys))
            if not indices:
                print("No valid database index selected.")
                sys.exit(1)
            target_list = [db_keys[i] for i in indices]

    results = server.execute_connection_check(target_list, all_dbs=all_dbs)
    print(f"\nChecking {len(results)} database connection(s)...")
    has_failure = _print_connection_results(results)

    failed_legacy_oracle = [
        res["db"] for res in results
        if res["status"] != "connected" and "DPY-3015" in (res.get("error") or "")
    ]
    if failed_legacy_oracle and sys.stdin.isatty():
        import driver_manager
        if not driver_manager.is_driver_ready("oracle"):
            print("\n[!] Oracle database requires legacy 10g client driver.")
            try:
                ans = input("Would you like db-grep to download the Oracle Instant Client driver now? [Y/n]: ").strip().lower()
                if ans in ("", "y", "yes"):
                    if driver_manager.install_driver("oracle"):
                        print("\nRe-testing Oracle connections...")
                        retry_results = server.execute_connection_check(failed_legacy_oracle, all_dbs=all_dbs)
                        has_failure = _print_connection_results(retry_results)
            except (KeyboardInterrupt, EOFError):
                pass

    print()
    sys.exit(1 if has_failure else 0)


def normalize_cli_args(args: list[str]) -> list[str]:
    """
    Normalizes lowercase shorthand flags (-s, -m, -t, -c, -i, -u, -d) to their respective subcommands.
    Eliminates risky case-sensitivity traps (-c vs -C, -u vs -U).
    """
    if not args:
        return []

    first = args[0]
    rest = args[1:]

    FLAG_MAP = {
        "-s": ["server"],
        "--server": ["server"],
        "-m": ["monitor"],
        "--monitor": ["monitor"],
        "-t": ["test"],
        "--test": ["test"],
        "-c": ["config"],
        "--config": ["config"],
        "-i": ["install"],
        "--install": ["install"],
        "-u": ["update"],
        "--update": ["update"],
        "-d": ["driver"],
        "--driver": ["driver"],
    }

    if first in FLAG_MAP:
        return list(FLAG_MAP[first]) + rest
    return args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="db-grep",
        description=f"db-grep v{__version__} - Database MCP CLI: Run MCP Server, Terminal Monitor, and Manage Connections",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program's version number and exit",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # server (aliases: s)
    subparsers.add_parser(
        "server",
        aliases=["s"],
        help="Run MCP stdio server for AI clients (Claude, Cursor, Codex, OpenCode, Antigravity, etc.)",
    )

    # monitor (aliases: m)
    subparsers.add_parser(
        "monitor",
        aliases=["m"],
        help="Run interactive terminal approval monitor to review and approve/reject queries",
    )

    # test (aliases: t)
    p_test = subparsers.add_parser(
        "test",
        aliases=["t"],
        help="Test physical database connectivity & latency (pass database names or omit for interactive menu)",
    )
    p_test.add_argument(
        "targets",
        nargs="*",
        metavar="DB",
        help="Target database name(s), aliases, or 'all'",
    )

    # config (aliases: c)
    p_cfg = subparsers.add_parser(
        "config",
        aliases=["c"],
        help="Manage databases in config.toml (subcommands: list, add, check, edit, disable, enable, delete)",
    )
    p_cfg.add_argument(
        "action",
        nargs="*",
        metavar="ACTION",
        help="Config subcommand (omit for interactive dashboard)",
    )

    # install (aliases: i)
    p_inst = subparsers.add_parser(
        "install",
        aliases=["i"],
        help="Install db-grep MCP configuration into AI clients (e.g. codex, claude, all)",
    )
    p_inst.add_argument(
        "client",
        nargs="?",
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
        default=None,
        metavar="CLIENT",
        help="Target AI client to configure (omit for interactive selection)",
    )

    # update (aliases: u)
    p_up = subparsers.add_parser(
        "update",
        aliases=["u"],
        help="Check for updates and changelog, or directly upgrade package (-y / -f)",
    )
    p_up.add_argument(
        "-y",
        "--yes",
        "-f",
        "--force",
        action="store_true",
        dest="yes",
        help="Directly upgrade without checking or confirmation",
    )

    # driver (aliases: d)
    p_drv = subparsers.add_parser(
        "driver",
        aliases=["d"],
        help="Inspect or configure database drivers (e.g. 'oracle', or omit to show status)",
    )
    p_drv.add_argument(
        "driver",
        nargs="?",
        default="status",
        metavar="DRIVER",
        help="Driver name (e.g. 'oracle') or omit to show status",
    )

    return parser


def main():
    raw_args = sys.argv[1:]
    norm_args = normalize_cli_args(raw_args)

    parser = build_parser()
    args = parser.parse_args(norm_args)

    cmd = args.command

    if cmd in ("server", "s"):
        import driver_manager
        driver_manager.ensure_driver_env()
        import server
        server.start()

    elif cmd in ("monitor", "m"):
        import monitor
        monitor.start()

    elif cmd in ("test", "t"):
        import driver_manager
        driver_manager.ensure_driver_env()
        run_check_connection_cli(args.targets)

    elif cmd in ("config", "c"):
        import config_manager
        cfg_args = args.action
        if not cfg_args:
            config_manager.run_interactive_dashboard()
        else:
            sub = cfg_args[0].lower()
            if sub in ("check", "verify", "validate"):
                run_check_config_cli()
            elif sub in ("list", "ls"):
                raw, entries = config_manager.read_config_entries()
                if not entries:
                    print("No databases configured.")
                for idx, e in enumerate(entries, 1):
                    status = "✓ ACTIVE" if e.enabled else f"# DISABLED ({e.reason})" if e.reason else "# DISABLED"
                    print(f"[{idx}] {e.key:<28} [{status}]")
            elif sub == "disable":
                if len(cfg_args) < 2:
                    print("Usage: db-grep config disable <DB> [reason]")
                else:
                    target = cfg_args[1]
                    reason = " ".join(cfg_args[2:]) if len(cfg_args) > 2 else ""
                    config_manager.disable_entry(target, reason)
            elif sub == "enable":
                if len(cfg_args) < 2:
                    print("Usage: db-grep config enable <DB>")
                else:
                    config_manager.enable_entry(cfg_args[1])
            elif sub in ("del", "delete", "rm"):
                if len(cfg_args) < 2:
                    print("Usage: db-grep config delete <DB>")
                else:
                    config_manager.delete_entry(cfg_args[1], confirm=False)
            elif sub == "add":
                config_manager.prompt_add_interactive()
            elif sub in ("view", "edit"):
                config_manager.view_raw_config()
            else:
                print(f"Unknown config command: {sub}")
                print("Available commands: list, add, check, disable, enable, delete, edit")
                sys.exit(1)

    elif cmd in ("install", "i"):
        import installer
        installer.run_install(target_harness=args.client)

    elif cmd in ("update", "u"):
        import updater
        if args.yes:
            updater.execute_upgrade()
        else:
            updater.run_check_update_cli(prompt_upgrade=True)

    elif cmd in ("driver", "d"):
        import driver_manager
        ok = driver_manager.setup_driver(args.driver)
        if not ok:
            sys.exit(1)

    else:
        print(f"db-grep v{__version__}\n")
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
