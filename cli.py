import argparse
import sys
from pathlib import Path

# Ensure db-grep directory is on sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def main():
    parser = argparse.ArgumentParser(
        prog="db-grep",
        description="Database MCP CLI: Run MCP Server or Terminal Monitor",
    )
    group = parser.add_mutually_exclusive_group(required=True)
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


if __name__ == "__main__":
    main()
