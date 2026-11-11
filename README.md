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

### 1. Install & Initialize in One Step
```bash
pip install git+https://github.com/pamungkasandono/mcp-db-grep.git && db-grep --init
```
*(or via `pipx`: `pipx install git+https://github.com/pamungkasandono/mcp-db-grep.git && db-grep --init`)*

This will install the tool and immediately prompt you to select your AI coding assistant.

**Supported AI Clients & Harnesses:**
1. **Claude Code CLI** (`claude mcp add`)
2. **Claude Desktop** (`claude_desktop_config.json`)
3. **OpenCode** (`opencode.jsonc`)
4. **Antigravity** (`~/.gemini/config/mcp_config.json`)
5. **Cursor** (`~/.cursor/mcp.json`)
6. **Codex CLI** (`~/.codex/config.toml`)
7. **Roo Code** (VS Code Extension `cline_mcp_settings.json`)
8. **Zed Editor** (`settings.json`)
9. **Pi** (`~/.pi/agent/mcp.json`)

*Non-interactive shortcut:*
```bash
db-grep --init --client claude        # configure Claude Code CLI
db-grep --init --client antigravity   # configure Antigravity
db-grep --init --client cursor        # configure Cursor
db-grep --init --client all           # configure all harnesses
```

### 2. Add Your Database
Edit `~/.config/db-grep/config.toml`:
```toml
[monitor]
port = 48260  # unique safe port (auto-fallback across 48260-48270 if busy)

# Postgres: [db.postgres.<name>]
[db.postgres.my-postgres]
description = "Main application database"
dsn = "postgres://user@localhost:5432/my_db"
password = "your_password"

# Oracle: [db.oracle.<name>]
[db.oracle.legacy-oracle]
description = "Company Oracle DB"
dsn = "host:1521/SERVICE_NAME"
user = "ro_user"
password_env = "ORACLE_PW"

# Multiple databases with the same name across different types work cleanly:
[db.postgres.company-core]
dsn = "postgres://user@localhost:5432/company"

[db.oracle.company-core]
dsn = "host:1521/SERVICE_NAME"
user = "ro_user"
```
*(When identical names exist across types, AI can address them by type prefix e.g. `postgres:company-core` or `oracle:company-core`).*

---

## Diagnostics & Health Checks

### Check Configuration (`config.toml`)
Verify file permissions, syntax, required fields, and environment variables:
```bash
db-grep --check-config
```
*(Also available as AI MCP tool `check_config`)*

### Check Physical Database Connectivity
Test database handshake and latency using lightweight ping queries (`SELECT 1;` for Postgres, `SELECT 1 FROM DUAL` for Oracle):
```bash
# Interactive selection (supports 1, 1,3, 1-3, or 'all'):
db-grep --check-connection

# Non-interactive CLI:
db-grep --check-connection all
db-grep --check-connection bekal-akun
db-grep --check-connection postgres:company-core,oracle:company-core
```
*(Also available as AI MCP tool `check_connection(db="all")`. Does not require interactive monitor approval).*

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

*Forgot to start the monitor?*
Don't worry! When a query runs, `db-grep` waits up to 25 seconds for you to open `db-grep --monitor`. If not opened in time, the AI will pause safely and ask you to launch the monitor—it is strictly blocked from reading credentials or bypassing your database directly.

---

## Updates & Upgrades

Check for latest releases and changelog directly from your terminal:
```bash
db-grep --check-update
```
Or upgrade immediately:
```bash
db-grep --upgrade
```

---

## Uninstall

To cleanly remove `db-grep` from your system:

1. **Remove package:**
   ```bash
   pip uninstall db-grep
   # or: pipx uninstall db-grep
   ```

2. **Clean configuration, instructions, and audit logs (optional):**
   ```bash
   rm -rf ~/.config/db-grep
   ```

3. **Unregister MCP from clients (if applicable):**
   - Claude Code CLI: `claude mcp remove db`
   - Other clients: Remove the `"db"` block from their config file (`mcp.json`, `mcp_config.json`, etc.)

---

## Local Development

```bash
python3 -m venv .venv
pip install -e .
just monitor
just server
just test      # run automated test suite
```

## License
MIT
