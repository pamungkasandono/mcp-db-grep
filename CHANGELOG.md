# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.1] - 2026-11-19

### Fixed
- **Multi-Version Changelog Aggregator**: Fixed missing release notes when users jump multiple versions during `db-grep -u` / `--check-update`. Automatically fetches the upstream `CHANGELOG.md` via CDN and extracts all intervening version release notes between the currently installed version and the target release, with seamless fallback to release body if offline.

### Changed
- **Pure-Python Terminal Markdown Renderer**: Built a zero-dependency ANSI markdown formatter in `updater.py` with cyan version divider bars, colored category badges (`[Added]`, `[Changed]`, `[Fixed]`), hanging indented bullets (`•`), auto-detected terminal width wrapping, and styled inline code/bold elements.

### Removed
- Removed the star symbol (`★`) from the update notification header.

## [0.5.0] - 2026-11-18

### Added
- **Dual-Phase `--monitor` (History Replay + Full Live Stream)**:
  - **"Before" Phase**: Automatically replays recent database activity from `audit.jsonl` upon launching `db-grep --monitor`. Displays visual badges (`[PAUSED-OFFLINE]`, `[EXECUTED]`, `[AUTO-ALLOW]`, `[HEALTH-CHECK]`, `[METADATA]`, `[CONFIG-CHECK]`, `[REJECTED-BY-USER]`, `[BLOCKED]`, `[DB-ERROR]`) with query snippets, execution metrics, and offline pause notices.
  - **"After" Phase**: Streams live actions in real time, expanding beyond queries to include live non-blocking streaming of `check_connection` pings with database latency stats, `list_databases`, and `check_config`.
  - Added configurable `history_limit` setting in `config.toml` under `[monitor]` (default: `20`).
- **Enriched Audit Logging**:
  - Unified and enriched `audit.jsonl` records in `server.py` with `tool`, `db`, `context`, `sql`, and target summaries across all events (`blocked`, `auto_allow`, `denied`, `error`, `executed`, `check_connection`, `list_databases`).

## [0.4.0] - 2026-11-18

### Added
- Added `find_objects` MCP tool for fast, server-side pattern and keyword searches across database objects (`TABLE`, `VIEW`, `SYNONYM`, `MATERIALIZED VIEW`) across all accessible schemas without cap-limit truncation.
- Intelligent pattern formatting: automatically wraps unadorned search terms in `%...%`, maps glob `*` wildcards to `%`, and preserves user-specified wildcards (`%`).
- Multi-dialect search query adapters:
  - **Oracle (`ora`)**: Queries `ALL_OBJECTS` with automatic exclusion of internal system schemas (`SYS`, `SYSTEM`, etc.) and universal backward-compatible row pagination (`ROWNUM`).
  - **PostgreSQL (`pg`)**: Case-insensitive filtering on `information_schema.tables`, excluding system catalogs (`pg_catalog`, `information_schema`).
  - **MySQL (`my`)**: Searches `information_schema.tables`, excluding internal system schemas (`mysql`, `performance_schema`, `information_schema`, `sys`).
- Extended Oracle `list_tables` to include `VIEW` objects via `UNION` with `ALL_VIEWS`.
- Added automatic synonym resolution to Oracle `describe_table` via `ALL_SYNONYMS`.
- Added automatic `schema.table` parsing and quote stripping in `describe_table`.
- Included `find_objects` under the default `auto_metadata = true` auto-approval rule.

### Changed
- **Standardization**: Refactored the core MCP parameter and payload name from `konteks` to standard English `context` (`Context = Annotated[...]`), while maintaining backward compatibility aliases (`Konteks = Context`, `check_konteks = check_context`).
- Standardized sample DSN passwords (`rahasia` → `secret`) and dummy database names across documentation, templates, and test fixtures to standard English terminology.

## [0.3.1] - 2026-11-18

### Fixed
- Handled GitHub API rate limit errors (`HTTP Error 403: rate limit exceeded`) gracefully during `db-grep --check-update`:
  - Added support for `GITHUB_TOKEN` and `GH_TOKEN` environment variables to authenticate GitHub REST API requests (5,000 req/hr).
  - Implemented automatic two-tier fallback: resolving latest version via GitHub web redirect (`releases/latest`) and direct Git protocol queries (`git ls-remote --tags`).
  - Added actionable CLI hints pointing to direct `db-grep -U` / `db-grep --upgrade` if update checks fail.

## [0.3.0] - 2026-11-18

### Added
- Multi-driver management via generic `--setup-driver [DRIVER]` CLI flag to inspect built-in drivers (`postgres`, `mysql`) and manage optional external client drivers (`oracle`).
- Automated, self-contained Oracle Instant Client downloader and installer in user-space (`~/.config/db-grep/instantclient`), eliminating the need for `sudo`, OS-level packages, or system directory pollution.
- Automatic Thick mode activation in `python-oracledb` for seamless support of legacy Oracle 10g password verifiers (`0x939` / `DPY-3015`) without modifying database passwords or configuration.
- Interactive on-demand driver setup during `db-grep --check-connection` and `db-grep --init`: automatically offers to download missing drivers when a legacy 10g Oracle database is detected, and retries the connection check seamlessly.
- Hardened archive extraction with Zip Slip protection and symlink traversal validation to prevent files from writing outside the target user directory.
- Dynamic shared library resolution (`ctypes.util.find_library`) for Linux distributions (including Ubuntu 24.04+ `libaio.so.1t64` auto-symlink) and process-level in-place environment bootstrapping.
- Dedicated unit and integration test suite covering driver lifecycle, security mitigations, and `DPY-3015` error wrapping (expanding test suite to 90 tests).

### Changed
- Centralized all driver metadata and alias mappings into `driver_manager.py` as a Single Source of Truth, reused by `config_manager.py` and `cli.py`.
- Refactored CLI and server exception handling to catch targeted driver errors (`oracledb.Error`) and provide actionable hints for legacy Oracle authentication.

## [0.2.1] - 2026-11-13

### Fixed
- Added pre-upgrade process detection, user confirmation warning, and automatic background process termination (`db-grep --server` / `--monitor`) in `updater.py`, plus delayed Windows `pip` execution to prevent `[WinError 32]` file-lock errors on `db-grep.exe`.

## [0.2.0] - 2026-11-13

### Fixed
- Prevented terminal hang on Windows after closing Notepad in `db-grep --config edit` (`view_raw_config()`) by launching `notepad` detached without inheriting console stdio handles (`subprocess.Popen` with `DEVNULL` / `os.startfile` fallback).
- Standardized OS-resolved config paths (`get_opencode_config_path`, `get_antigravity_config_path`, `get_pi_config_path`) and menu column spacing across all harness entries in `installer.py`.
- Prevented false-positive raw secret detection when `${env:...}` or `${file:...}` references are used in DSN username, host, or port without an inline plaintext password.
- Validated Oracle `user` requirement against the resolved DSN so full Oracle DSNs loaded via `env:` / `file:` (`user/pass@host:port/service`) work without a duplicate `user` key.
- Removed unreachable duplicate `try/except/finally` block in `server.ping_database()` and deduplicated temporary config setup in `tests/test_server_logic.py` for cross-platform file locking compatibility.

### Changed
- Registered MCP server under explicit `db-grep` name (instead of generic `db`) across all 9 AI client harnesses in `installer.py`, with automatic migration of legacy `db-grep` entries named `db`.
- Added driver shorthand aliases (`pgsql` → `postgres`, `ora` → `oracle`, `my` → `mysql`) across CLI commands (`--check-connection`, `--config`) and MCP database resolvers.
- Automatically URL-encode (`quote`) credentials interpolated or embedded inside URI DSNs (`postgres://`, `postgresql://`, `mysql://`) via `resolve()` and `normalize_dsn_credentials()`, eliminating the need for manual `|urlenc` while remaining backward-compatible.
- Added pre-driver DSN syntax validation (`validate_dsn_syntax`), malformed interpolation syntax checks, and `shorthand=True` support (`env:VAR`, `file:/path`) for `dsn` fields in `server.py`.
- Unified database configuration validation, default inheritance, DSN secret extraction (`_extract_dsn_passwords`), and raw-secret scanning (`_config_has_raw_secrets`, `_validate_db_entry`) across `read_config()`, `databases()`, `inspect_config()`, and `DB.get_dsn()` in `server.py`.
- Consolidated cross-platform config path helpers (`_appdata_config_path`, `_home_config_path`), harness detection (`_detect_path_or_bin`), and JSON MCP registration (`_install_json_mcp`) in `installer.py`.
- Unified database key resolution (`resolve_db_keys`) in `server.py` and reused `server.execute_connection_check()` inside `cli.run_check_connection_cli()`.
- Deduplicated section parsing loop, entry lookup (`_load_target_entry`), and duplicate not-found error messages in `config_manager.py`.
- Centralized package version resolution in `cli.py` by reusing `updater.get_current_version()`.
- **BREAKING**: Replaced flat `[db.<name>]` configuration format with driver-scoped `[db.<driver>.<name>]` tables for minimal maintenance and zero TOML key collisions.
- `check_connection` / `--check-connection` now tests all matching databases across drivers when given an unprefixed short name, while driver prefixes (`postgres:`/`pgsql:`, `oracle:`/`ora:`, `mysql:`/`my:`) target a specific driver.

### Added
- Single-letter shorthand flags (`-s`, `-m`, `-i`, `-c`, `-C`, `-t`, `-u`, `-U`, `-a`) across all CLI options in `cli.py` with expanded, crystal-clear help documentation (`-h` / `--help`).
- Session-level auto-approve toggle (`[a]uto-approve session`) and on-demand lock hotkey (`[l]`) in `db-grep --monitor` with instant lock for 1 session, direct single-key picker for 2–4 sessions, and numbered multi-select menu for 5+ sessions.
- MySQL driver support (`[db.mysql.<name>]`) via `pymysql` with read-only session enforcement, URI DSN validation (`mysql://...`), `sqlglot` MySQL dialect table extraction, and `--config add` interactive support.
- `--config` interactive controller and CLI subcommands (`list`, `add`, `disable`, `enable`, `delete`, `edit`) to manage databases without manual file edits.
- Comment-based non-destructive disable/enable preserving credentials with optional audit reason tags (`# [DISABLED: reason] [db.<driver>.<name>]`).
- Universal secret interpolation `${env:VAR}`, `${file:/path}`, `${env:VAR|urlenc}`, and shorthand `env:VAR` / `file:/path`.
- Enforced `chmod 600` on POSIX when config contains raw secrets or `file:` references.
- `[defaults]` section supporting inheritance for `auto_metadata`, `max_rows`, and `timeout_ms`.
- Per-database `auto_metadata` and table whitelist `auto_tables` with AST-level table extraction via `sqlglot`.
- Secret redaction layer replacing resolved credentials with `***` across all driver errors, logs, and monitor outputs.
- Per-database fault tolerance ensuring invalid database definitions do not halt or disable other valid connections.
- `--check-connection` multi-target execution accepting multiple database names separated by spaces in a single command.
- `--check-update` and `--upgrade` CLI flags to inspect GitHub releases and upgrade in place.
- Direct one-liner install and initialize instructions in `README.md`.
- Comprehensive uninstall instructions in `README.md`.
- Diagnostic tools `check_config` and `check_connection` available as MCP tools and CLI flags (`--check-config` and `--check-connection`).
- Interactive CLI connection tester with flexible index, comma, and range selection syntax (`1`, `1,3`, `1-3`, `all`).
- Explicit ping queries (`SELECT 1;` for Postgres, `SELECT 1 FROM DUAL` for Oracle) running without monitor approval blocking.
- Driver-scoped database tables (`[db.<driver>.<name>]`) supporting identical database names across different database types without TOML key collisions.
- Dual-lookup resolver supporting both exact `type:name` and unique short `name` with helpful collision warnings.
- Extended `list_databases` tool to automatically surface type-prefixed keys when name collisions exist.
- Automated unit test suite under `tests/` covering CLI flags, port fallback, server logic, and multi-harness installer.
- Added `just test` runner command using Python stdlib `unittest`.
- Dynamic port fallback across safe private range `48260`–`48270` when preferred port is occupied.
- State file synchronization (`~/.config/db-grep/monitor.port`) ensuring server and monitor auto-connect without manual configuration.
- Startup logic to safely overwrite/replace any stale or leftover `monitor.port` from previous crashes.
- Changed default monitor port to unique safe port `48260`.
- 25-second graceful connection retry loop in `server.py` waiting for monitor to open before raising error.
- Strict anti-bypass guardrail rules (`MONITOR_OFFLINE`) forbidding AI from reading `.env` files or creating direct DB scripts.
- Extended multi-harness installer support to 9 AI coding clients in structured order:
  1. Claude Code CLI
  2. Claude Desktop
  3. OpenCode
  4. Antigravity (`~/.gemini/config/mcp_config.json`)
  5. Cursor (`~/.cursor/mcp.json`)
  6. Codex CLI (`~/.codex/config.toml`)
  7. Roo Code (VS Code Extension `cline_mcp_settings.json`)
  8. Zed Editor (`settings.json`)
  9. Pi Coding Agent (`~/.pi/agent/mcp.json`)
- Smart selection logic in `db-grep --init` defaulting only to detected harnesses.
- Non-interactive flag `--client` supporting all 9 harness identifiers.

## [0.1.0] - 2026-11-11

### Added
- Core MCP server implementation with FastMCP for PostgreSQL and Oracle DB.
- TCP approval bridge with interactive terminal monitor UI (`db-grep --monitor`).
- Single CLI entrypoint (`cli.py`) supporting `--server`, `--monitor`, and `--init`.
- Version flag (`-v`, `--version`) and version header in help screen.
- Interactive multi-harness setup (`db-grep --init`) with client detection (`opencode`, `claude`, `claude-desktop`).
- Bundled instruction distribution (`instructions/db-grep.md`) installed to `~/.config/db-grep/instructions.md`.
- Strict AI denial protocol and feedback prompts on monitor rejection.
- Global executable wrapper at `~/.local/bin/db-grep`.
- Centralized TOML configuration reader at `~/.config/db-grep/config.toml` (with legacy fallback).
- Standard packaging with `pyproject.toml` supporting direct installation via `pip`, `pipx`, and `uv`.
- Task runner support via `justfile` and automated test syntax checks.
