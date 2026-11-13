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
pip install git+https://github.com/pamungkasandono/mcp-db-grep.git && db-grep -i
```
*(or via `pipx`: `pipx install git+https://github.com/pamungkasandono/mcp-db-grep.git && db-grep -i`)*

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
db-grep -i -a claude        # configure Claude Code CLI (--init --client claude)
db-grep -i -a antigravity   # configure Antigravity
db-grep -i -a cursor        # configure Cursor
db-grep -i -a all           # configure all harnesses
```

### 2. Add Your Database
Edit `~/.config/db-grep/config.toml`:
```toml
[monitor]
port = 48260
approval_timeout_s = 110      # auto-reject after 110s without response

[defaults]
auto_metadata = true          # list_tables & describe_table without y/n
max_rows = 100
timeout_ms = 10000

# ── Format 1: Full DSN, raw credentials (ideal for local dev)
[db.postgres.ecommerce-dev]
description = "Development E-Commerce Database"
dsn = "postgres://ro_user:rahasia@localhost:5432/ecommerce_dev"
auto_tables = ["mst_*", "ref_*", "app_configs"]

# ── Format 2: DSN + credentials from environment (shorthand)
[db.postgres.inventory-core]
description = "Core Inventory Management"
dsn = "postgres://localhost:5432/inventory?sslmode=require"
user = "ro_user"
password = "env:INVENTORY_PW"

# ── Format 3: Inline password in DSN from env or file (always use |urlenc)
[db.postgres.analytics-app]
description = "Analytics Database"
dsn = "postgres://ro_user:${env:ANALYTICS_PW|urlenc}@localhost:5432/analytics"

# ── Oracle Format A: Connect string + user + secret from file
[db.oracle.payroll-prod]
description = "Corporate Payroll Database"
dsn = "localhost:1521/PROD"  # or remote: "db-host:1521/PROD"
user = "ro_user"
password = "file:~/.config/db-grep/secrets/payroll"
schema = "HR"
auto_metadata = false        # override: table inspection requires y/n

# ── Oracle Format B: Inline user and password inside connect string
[db.oracle.emergency-loan]
description = "Emergency Loan Service"
dsn = "ro_user/${env:EMLOAN_PW}@localhost:1521/EMLOAN"
```
*(Supports universal interpolation `${env:VAR}`, `${file:/path}`, `${env:VAR|urlenc}`, and shorthand `env:VAR` / `file:/path`).*

---

## Configuration Manager (`db-grep --config` / `-c`)

Manage databases without memorizing file paths or manual TOML editing:

```bash
# Interactive dashboard (view status, toggle enable/disable, add, delete, edit):
db-grep -c                     # or: db-grep --config

# Direct CLI commands:
db-grep -c list                # or: db-grep --config list
db-grep -c disable inventory-core "weekly migration"
db-grep -c enable inventory-core
db-grep -c delete old-service
db-grep -c add
db-grep -c edit
```

---

## Diagnostics & Health Checks

### Check Configuration (`config.toml`)
Verify file permissions, syntax, required fields, and environment variables:
```bash
db-grep -C                     # or: db-grep --check-config
```
*(Also available as AI MCP tool `check_config`)*

### Check Physical Database Connectivity
Test database handshake and latency using lightweight ping queries (`SELECT 1;` for Postgres/MySQL, `SELECT 1 FROM DUAL` for Oracle):
```bash
# Interactive selection (supports 1, 1,3, 1-3, or 'all'):
db-grep -t                     # or: db-grep --check-connection

# Non-interactive CLI:
db-grep -t all
db-grep -t ecommerce-dev
db-grep -t inventory-core                       # runs all drivers matching 'inventory-core'
db-grep -t postgres:inventory-core oracle:payroll-prod
db-grep -t pgsql:inventory-core ora:payroll-prod my:orders-app
```
*(Passing a short name without prefix checks all matching databases across drivers; use `postgres:<name>` / `pgsql:<name>`, `oracle:<name>` / `ora:<name>`, or `mysql:<name>` / `my:<name>` to target a specific driver. Also available as AI MCP tool `check_connection(db="all")`, without interactive monitor approval).*

---

## Daily Usage

1. **Terminal 1: Start Monitor**
   ```bash
   db-grep -m                  # or: db-grep --monitor
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
   Allow? [y]es once / [a]uto-approve session / [n]o: 
   ```
   - Press **`y`**: Approve this single query; result table is displayed and rows are sent to AI.
   - Press **`a`**: Enable **Auto-Approve Session** for `[A]`; all subsequent queries in this session run automatically until you lock it again.
   - Press **`n`**: Query cancelled; AI stops immediately and explains why the query was needed.
   - Press **`l`** *(anytime while `⚡ AUTO-APPROVE ON` is active)*: Lock auto-approve and restore manual `[y/n]` approval:
     - **1 active session**: Locks immediately on pressing `l`.
     - **2–4 active sessions**: Select session directly by pressing its key (`A` / `B` / `C` / `D`) or `*` / `Enter` to lock all.
     - **5+ active sessions**: Displays a numbered session list supporting multi-select (`1`, `1,3`, `1-5`, or `all`).

*Forgot to start the monitor?*
Don't worry! When a query runs, `db-grep` waits up to 25 seconds for you to open `db-grep -m`. If not opened in time, the AI will pause safely and ask you to launch the monitor—it is strictly blocked from reading credentials or bypassing your database directly.

---

## Updates & Upgrades

Check for latest releases and changelog directly from your terminal:
```bash
db-grep -u                     # or: db-grep --check-update
```
Or upgrade immediately:
```bash
db-grep -U                     # or: db-grep --upgrade
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
