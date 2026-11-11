import argparse
import sys
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

# Ensure db-grep directory is on sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    __version__ = version("db-grep")
except PackageNotFoundError:
    __version__ = "0.1.0"


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

    parser.add_argument(
        "--client",
        choices=["opencode", "claude", "claude-desktop", "all", "none"],
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
    else:
        print(f"db-grep v{__version__}\n")
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
