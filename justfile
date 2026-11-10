default:
    @just --list

# Run monitor UI
monitor:
    .venv/bin/python monitor.py

# Run MCP server directly (stdio)
server:
    .venv/bin/python server.py

# Install dependencies into .venv
install:
    .venv/bin/pip install "mcp<2" psycopg oracledb

# Syntax check
check:
    .venv/bin/python -m py_compile server.py monitor.py
