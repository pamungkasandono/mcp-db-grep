# db-grep

Database MCP server with interactive approval monitor for AI assistants.

---

## Why db-grep?

AI coding assistants can silently run queries and read your database without you knowing what data was fetched.

**db-grep stops blind database access.**

Every database request goes to a separate terminal monitor. You see the exact SQL query, target database, and context. You press `y` to allow or `n` to reject before any data enters the AI context.

If you reject (`n`), the AI stops immediately, tells you which query was rejected, and explains why that data was needed.

---

## Quick Start (3 Steps)

### 1. Install CLI
```bash
pip install git+https://github.com/pamungkasandono/mcp-db-grep.git
```
*(or use `pipx install git+...` / `uv tool install git+...`)*

### 2. Run Setup
```bash
db-grep --init
```
This command will:
- Detect your AI client (`opencode`, `claude` CLI, `claude-desktop`).
- Ask which client to connect.
- Install safety rules so AI stops and explains when queries are rejected.
- Create config file at `~/.config/db-grep/config.toml`.

*Non-interactive shortcut:*
```bash
db-grep --init --client opencode   # configure only Opencode
db-grep --init --client claude     # configure only Claude Code CLI
db-grep --init --client all        # configure all detected clients
```

### 3. Add Your Database
Edit `~/.config/db-grep/config.toml`:
```toml
[monitor]
port = 8765

[db.my-postgres]
type = "postgres"
description = "Main application database"
dsn = "postgres://user@localhost:5432/my_db"
password = "your_password"

# Oracle example:
# [db.my-oracle]
# type = "oracle"
# description = "Company Oracle DB"
# dsn = "host:1521/SERVICE_NAME"
# user = "ro_user"
# password_env = "ORACLE_PW"
```

---

## Daily Usage

1. **Terminal 1: Start Monitor**
   ```bash
   db-grep --monitor
   ```
   Keep this terminal open while working.

2. **Terminal 2: Use Your AI Assistant**
   ```bash
   opencode
   # or
   claude
   ```
   Ask questions about your data as usual:
   > *"Show me 5 latest active users from my-postgres"*

3. **Approve or Reject in Terminal 1:**
   ```text
   ━━ 14:10:05 [A] query @ pgsql:my-postgres ━━
   my-project · get latest users
   SELECT id, username, email FROM users LIMIT 5;
   Allow? [y/n]
   ```
   - Press **`y`**: Query runs, result table is displayed, rows are sent to AI.
   - Press **`n`**: Query cancelled, AI stops and explains why the query was needed.

---

## Local Development

```bash
python3 -m venv .venv
pip install -e .
just monitor
just server
```

## License
MIT
