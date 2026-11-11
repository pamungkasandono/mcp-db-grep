default:
    @just --list

# Run monitor UI
monitor:
    db-grep --monitor

# Run MCP server directly (stdio)
server:
    db-grep --server

# Install dependencies into .venv
install:
    .venv/bin/pip install -e .

# Syntax check
check:
    .venv/bin/python -m py_compile cli.py server.py monitor.py installer.py

# Run test suite
test:
    .venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
