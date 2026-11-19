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

### 1. Install & Register in One Step
```bash
pip install git+https://github.com/pamungkasandono/mcp-db-grep.git && db-grep install
```
*(or via `pipx`: `pipx install git+https://github.com/pamungkasandono/mcp-db-grep.git && db-grep install`)*

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
db-grep install claude        # configure Claude Code CLI
db-grep install antigravity   # configure Antigravity
db-grep install cursor        # configure Cursor
db-grep install codex         # configure Codex CLI
db-grep install all           # configure all detected harnesses
```

### 2. Add Your Database (Recommended: CLI)

The easiest and safest way to add and configure databases is via the configuration CLI:

```bash
db-grep config add             # or shorthand: db-grep -c add
```

This interactive wizard guides you step-by-step:
1. Select the database driver (`postgres`, `oracle`, `mysql`).
2. Provide a unique connection name.
3. Enter DSN / host connection details and credentials (supports raw, environment variables, or files).
4. Configure optional settings (default schema, description, auto-allowed tables).

You can inspect, enable, disable, or delete databases anytime via the interactive dashboard:
```bash
db-grep config                 # open interactive configuration dashboard (or: db-grep -c)
db-grep config list            # list all active and disabled databases
db-grep config check           # validate configuration syntax & credentials
db-grep config migrate         # auto-upgrade legacy config.toml to latest schema
db-grep config disable inventory-core "maintenance window"
db-grep config enable inventory-core
db-grep config delete old-service
db-grep config edit            # open config.toml in default editor (Notepad / nano)
```

---

### Optional: Manual Configuration (`config.toml`)

If you prefer editing the configuration directly or automating environments via script/dotfiles, edit `config.toml`:
* **Linux / macOS**: `~/.config/db-grep/config.toml`
* **Windows**: `%APPDATA%\db-grep\config.toml`

#### Minimal Template:
A fresh installation creates a clean configuration file without clutter:
```toml
[monitor]
port = 48260
approval_timeout_s = 110      # auto-reject after 110s without response
history_limit = 20           # replay recent audit activity before monitor opened (0 = off)

[defaults]
allow_metadata = ["read"]     # list_tables, find_objects, search_columns & describe_table without prompt
allow_data = []               # data queries require approval unless covered by allow_tables
max_rows = 1000               # default maximum rows returned per query
timeout_ms = 10000            # default query timeout in milliseconds
```

#### Database Section Syntax:
Define databases under driver-scoped tables: `[db.<driver>.<name>]`, where `<driver>` is `postgres`, `oracle`, or `mysql`.

```toml
# ── PostgreSQL Examples ─────────────────────────────────────────────
# Format 1: Full DSN with credentials (ideal for local development)
[db.postgres.ecommerce-dev]
description = "Development E-Commerce Database"
dsn = "postgres://ro_user:secret@localhost:5432/ecommerce_dev"
allow_tables = ["mst_*", "ref_*", "app_configs"]
restricted_tables = ["payroll_*", "user_identity"]
restricted_columns = ["salary*", "*.nik", "*.ssn"]

# Format 2: DSN + credentials from environment variable (recommended for production)
[db.postgres.inventory-core]
description = "Core Inventory Management"
dsn = "postgres://localhost:5432/inventory?sslmode=require"
user = "ro_user"
password = "env:INVENTORY_PW"

# Format 3: Inline password interpolation (special characters are auto URL-encoded)
[db.postgres.analytics-app]
description = "Analytics Database"
dsn = "postgres://ro_user:${env:ANALYTICS_PW}@localhost:5432/analytics"

# ── Oracle Examples ─────────────────────────────────────────────────
# Format A: Connect string (host:port/service) + user + secret from file
[db.oracle.payroll-prod]
description = "Corporate Payroll Database"
dsn = "localhost:1521/PROD"  # or remote: "db-host:1521/PROD"
user = "ro_user"
password = "file:~/.config/db-grep/secrets/payroll"
schema = "HR"
allow_metadata = []          # override: table inspection also requires approval

# Format B: Inline user and password in connect string
[db.oracle.emergency-loan]
description = "Emergency Loan Service"
dsn = "ro_user/${env:EMLOAN_PW}@localhost:1521/EMLOAN"

# ── MySQL Examples ──────────────────────────────────────────────────
# URI DSN with environment variable interpolation
[db.mysql.orders-app]
description = "Orders MySQL Database"
dsn = "mysql://ro_user:${env:ORDERS_PW}@localhost:3306/orders_db"
allow_tables = ["mst_*", "ref_*"]
```

#### Configuration Options Reference:

| Field | Type | Required | Description | Example |
| :--- | :--- | :---: | :--- | :--- |
| `dsn` | `str` | **Yes** | Connection string, URI, or `host:port/service` | `"postgres://localhost:5432/mydb"` |
| `user` | `str` | Optional | Database username (if not embedded in `dsn`) | `"ro_user"` or `"env:DB_USER"` |
| `password` | `str` | Optional | Password or secret interpolation reference | `"secret"`, `"env:VAR"`, `"file:/path"` |
| `password_env` | `str` | Optional | Direct environment variable name holding password | `"MY_DB_PASSWORD"` |
| `description` | `str` | Optional | Human-readable description shown in monitor & AI | `"Primary customer database"` |
| `schema` | `str` | Optional | Default schema/owner | `"public"` (Postgres), `"HR"` (Oracle) |
| `allow_metadata` | `list` | Optional | Capabilities permitted on metadata exploration tools | `["read"]` |
| `allow_data` | `list` | Optional | Capabilities permitted on raw data queries | `["read"]` or `[]` |
| `allow_tables` | `list` | Optional | Glob patterns for tables that bypass monitor prompt | `["mst_*", "ref_*"]` |
| `restricted_tables` | `list` | Optional | Glob patterns for tables that strictly require approval | `["payroll_*"]` |
| `restricted_columns` | `list` | Optional | Glob patterns for columns that strictly require approval | `["salary*", "*.ssn"]` |
| `max_rows` | `int` | Optional | Max rows returned per query (overrides defaults) | `500` |
| `timeout_ms` | `int` | Optional | Query execution timeout in milliseconds | `15000` |

#### Safe Secret Interpolation:
Never hardcode production passwords. `db-grep` resolves secrets on-demand without writing them to disk:
* **Environment Variable Shorthand**: `password = "env:INVENTORY_PW"`
* **Environment Variable Syntax**: `dsn = "postgres://ro_user:${env:DB_PW}@localhost/db"` *(special characters are automatically URL-encoded in URI DSNs)*
* **File Reference Shorthand**: `password = "file:~/.config/db-grep/secrets/db_pass"`
* **File Reference Syntax**: `password = "${file:/path/to/secret}"`

---

## Diagnostics & Health Checks

### Check Configuration (`config.toml`)
Verify file permissions, syntax, required fields, and environment variables:
```bash
db-grep config check           # or shorthand: db-grep -c check
```
*(Also available as AI MCP tool `check_config`)*

### Ping Physical Database Connectivity
Ping database handshake and measure latency using lightweight queries (`SELECT 1;` for Postgres/MySQL, `SELECT 1 FROM DUAL` for Oracle):
```bash
# Interactive selection (supports 1, 1,3, 1-3, or 'all'):
db-grep ping                   # or shorthand: db-grep -p

# Non-interactive CLI:
db-grep ping all
db-grep ping ecommerce-dev
db-grep ping inventory-core                       # pings all drivers matching 'inventory-core'
db-grep ping postgres:inventory-core oracle:payroll-prod
db-grep ping pgsql:inventory-core ora:payroll-prod my:orders-app
```
*(Passing a short name without prefix pings all matching databases across drivers; use `postgres:<name>` / `pgsql:<name>`, `oracle:<name>` / `ora:<name>`, or `mysql:<name>` / `my:<name>` to target a specific driver. Also available as AI MCP tool `check_connection(db="all")`, without interactive monitor approval).*

---

## Daily Usage

1. **Terminal 1: Start Monitor**
   ```bash
   db-grep monitor             # or shorthand: db-grep -m
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
Don't worry! When a query runs, `db-grep` waits up to 25 seconds for you to open `db-grep monitor`. If not opened in time, the AI will pause safely and ask you to launch the monitor—it is strictly blocked from reading credentials or bypassing your database directly.

---

## Updates & Upgrades

Check for latest releases and changelog directly from your terminal:
```bash
db-grep update                 # or shorthand: db-grep -u
```
Or upgrade immediately without checking:
```bash
db-grep update -y              # or: db-grep update -f / db-grep update --yes
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
   - Claude Code CLI: `claude mcp remove db-grep`
   - Other clients: Remove the `"db-grep"` block from their config file (`mcp.json`, `mcp_config.json`, `opencode.jsonc`, etc.)

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
