# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **BREAKING**: Replaced flat `[db.<name>]` configuration format with driver-scoped `[db.<driver>.<name>]` tables for minimal maintenance and zero TOML key collisions.
- Removed `pgsql:` prefix alias; always use explicit `postgres:<name>` or `oracle:<name>` when specifying driver type.
- `check_connection` / `--check-connection` now tests all matching databases across drivers when given an unprefixed short name, while `postgres:<name>` or `oracle:<name>` targets a specific driver.

### Added
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
