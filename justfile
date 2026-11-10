default:
    @just --list

# Run monitor UI
monitor:
    db-mcp --monitor

# Run MCP server directly (stdio)
server:
    db-mcp --server

# Install dependencies into .venv
install:
    .venv/bin/pip install "mcp<2" psycopg oracledb

# Syntax check
check:
    .venv/bin/python -m py_compile cli.py server.py monitor.py
