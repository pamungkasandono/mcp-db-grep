# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Prevented false-positive raw secret detection when `${env:...}` or `${file:...}` references are used in DSN username, host, or port without an inline plaintext password.
- Validated Oracle `user` requirement against the resolved DSN so full Oracle DSNs loaded via `env:` / `file:` (`user/pass@host:port/service`) work without a duplicate `user` key.
- Removed unreachable duplicate `try/except/finally` block in `server.ping_database()` and deduplicated temporary config setup in `tests/test_server_logic.py` for cross-platform file locking compatibility.

### Changed
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
