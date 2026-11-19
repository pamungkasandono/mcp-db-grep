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


def run_check_config_cli(exit_on_complete: bool = True) -> bool:
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
        if "allow_metadata" in d:
            print(f"         Allow-Metadata: {', '.join(d['allow_metadata']) if isinstance(d['allow_metadata'], list) else d['allow_metadata']}")
        if "allow_data" in d:
            print(f"         Allow-Data: {', '.join(d['allow_data']) if isinstance(d['allow_data'], list) else d['allow_data']}")
        if d.get("allow_tables"):
            print(f"         Allow-Tables: {', '.join(d['allow_tables'])}")
        if d.get("restricted_tables"):
            print(f"         Restricted-Tables: {', '.join(d['restricted_tables'])}")
        if d.get("restricted_columns"):
            print(f"         Restricted-Columns: {', '.join(d['restricted_columns'])}")
        for err in d.get("errors", []):
            print(f"         Error: {err}")

    if report["valid"]:
        print("\nResult: Configuration is VALID.\n")
        if exit_on_complete:
            sys.exit(0)
        return True
    else:
        print("\nResult: Configuration is INVALID. Please correct the errors above.")
        import config_manager
        if config_manager.CONFIG.exists():
            try:
                legacy = config_manager.detect_legacy_keys(config_manager.CONFIG.read_text(encoding="utf-8"))
                if legacy:
                    print(f"\n💡 Hint: Found {len(legacy)} legacy key(s) ({', '.join(legacy)}). Run 'db-grep config migrate' to auto-upgrade.")
            except Exception:
                pass
        print()
        if exit_on_complete:
            sys.exit(1)
        return False


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
        print("\n--- db-grep Interactive Database Ping ---")
        print("Available Databases:")
        for idx, key in enumerate(db_keys, start=1):
            desc = all_dbs[key].get("description", "")
            desc_str = f" - {desc}" if desc else ""
            print(f"  [{idx}] {key:<25}{desc_str}")

        print("  [a] all                       - Check all databases")
        print("  [q] quit                      - Cancel")

        try:
            choice = input("\nSelect database to ping [default: all, e.g. 1, 1,3, 1-3, or 'all']: ").strip()
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
    print(f"\nPinging {len(results)} database connection(s)...")
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
                        print("\nRe-pinging Oracle connections...")
                        retry_results = server.execute_connection_check(failed_legacy_oracle, all_dbs=all_dbs)
                        has_failure = _print_connection_results(retry_results)
            except (KeyboardInterrupt, EOFError):
                pass

    print()
    sys.exit(1 if has_failure else 0)


def normalize_cli_args(args: list[str]) -> list[str]:
    """
    Normalizes lowercase shorthand flags (-s, -m, -p, -c, -i, -u, -d) to their respective subcommands.
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
        "-p": ["ping"],
        "--ping": ["ping"],
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
    parser.add_argument(
        "-y",
        "--yes",
        "-f",
        "--force",
        action="store_true",
        dest="yes",
        help="Automatic yes to prompts",
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

    # ping (aliases: p)
    p_ping = subparsers.add_parser(
        "ping",
        aliases=["p"],
        help="Ping databases to verify connectivity & measure latency (pass database names or omit for interactive menu)",
    )
    p_ping.add_argument(
        "targets",
        nargs="*",
        metavar="DB",
        help="Target database name(s), aliases, or 'all'",
    )

    # config (aliases: c)
    p_cfg = subparsers.add_parser(
        "config",
        aliases=["c"],
        help="Manage databases in config.toml (subcommands: list, check, add, migrate, edit, disable, enable, delete)",
        description="Manage databases and access controls in config.toml. Omit subcommand to open the interactive dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
subcommands:
  check               Validate configuration syntax, file permissions, and access rules
  list (ls)           List all active and disabled databases
  add                 Add a new database connection interactively
  migrate             Auto-upgrade legacy config.toml keys to modern format
  edit (view)         Open config.toml in default editor (Notepad / nano)
  disable <DB> [MSG]  Temporarily disable a database (with optional maintenance reason)
  enable <DB>         Re-enable a disabled database
  delete <DB>         Delete a database entry permanently

examples:
  db-grep config               # Open interactive dashboard
  db-grep config check         # Validate config.toml
  db-grep config list          # List configured databases
  db-grep config add           # Interactive wizard to add a DB
  db-grep config migrate -y    # Upgrade legacy config without prompt
  db-grep config disable prod  # Temporarily disable 'prod'
""",
    )
    p_cfg.add_argument(
        "-y",
        "--yes",
        action="store_true",
        dest="yes",
        help="Automatic yes to prompts",
    )
    p_cfg.add_argument(
        "action",
        nargs="*",
        metavar="ACTION",
        help="Config subcommand (check, list, add, migrate, edit, disable, enable, delete)",
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

    # Format subcommands as "[alias] name" for clean, uniform alignment
    for action in parser._actions:
        if hasattr(action, "_choices_actions"):
            for subaction in action._choices_actions:
                import re
                m = re.match(r"^([a-z]+) \(([a-z]+)\)$", subaction.metavar or "")
                if m:
                    subaction.metavar = f"[{m.group(2)}] {m.group(1)}"

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

    elif cmd in ("ping", "p"):
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
            elif sub in ("migrate", "upgrade"):
                auto_yes = "-y" in cfg_args or "--yes" in cfg_args or getattr(args, "yes", False)
                config_manager.migrate_config_file(interactive=not auto_yes)
            elif sub in ("view", "edit"):
                config_manager.view_raw_config()
            else:
                print(f"Unknown config command: {sub}")
                print("Available commands: list, add, check, migrate, disable, enable, delete, edit")
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
