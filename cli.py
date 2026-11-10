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

    args = parser.parse_args()

    if args.server:
        import server
        server.start()
    elif args.monitor:
        import monitor
        monitor.start()


if __name__ == "__main__":
    main()
