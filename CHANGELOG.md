# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- NOTE: Review CONTRIBUTING.md for strict changelog rules before editing this file. -->

## [Unreleased]

## [0.9.0] - 2026-11-20

### Added
- **Foreign Key Relationship Mapping (`describe_relations`)**:
  - Added new MCP tool to map both incoming and outgoing foreign key relationships for any table across PostgreSQL, MySQL, and Oracle.
  - Returns directional relations (`OUTGOING` vs `INCOMING`), constraint names, referenced tables and columns, and `ON DELETE` / `ON UPDATE` actions to guide safe schema migrations and join planning.
- **Index Inspection Tool (`list_indexes`)**:
  - Added new MCP tool to inspect existing indexes, column ordering, uniqueness, and index types (e.g. `BTREE`, `NORMAL`) on tables across supported database engines.
- **DDL Definition Retrieval (`get_object_ddl`)**:
  - Added new MCP tool to extract exact `CREATE TABLE` and `CREATE VIEW` definitions directly from database catalogs (`pg_get_viewdef`, `SHOW CREATE`, `DBMS_METADATA.GET_DDL`), aiding view query analysis and migration authoring.

### Changed
- **Enriched Column Schema Metadata in `describe_table`**:
  - Expanded standard `describe_table` output to include exact data type precision and scale (e.g. `varchar(255)`, `numeric(15,2)`), constraint markers (`[PK]`, `[FK -> target]`, `[UNIQUE]`), default values (`[DEFAULT: val]`), and column comments.
  - Retains compact string format (`COL: TYPE [TAGS] -- comment`) to minimize LLM token overhead while providing complete structural context.
- **Token-Efficient Columnar Format for Tool Results**:
  - Switched query and object search tools (`query`, `sample_table`, `find_objects`, `search_columns`, `list_tables`) to return compact columnar tables (`{"columns": [...], "rows": [[...], ...]}`), cutting LLM context token usage by 50%–70%.
- **Unified Concise Schema Format for `describe_table`**:
  - Standardized `columns` in `describe_table` to concise string representations (`COL: TYPE [NOT NULL]` in standard mode, and `COL: TYPE` when `compact=True`).
  - Eliminates repetitive nested JSON dictionary keys, cutting metadata prompt tokens by ~50% to ~65% while keeping root diagnostic fields (`status`, `error_hint`) structured and reliable.
- **Hybrid Table / Vertical Card Display in Monitor (`\\x auto` style)**:
  - Dynamically detects terminal column width via `shutil.get_terminal_size()`.
  - Renders compact horizontal tables when columns fit cleanly within terminal width.
  - Automatically switches to aligned vertical card records (`─── [1/N] ───`) when queries return wide columns, eliminating line wrap corruption and unreadable terminal clutter.
- **CLI Help & Subcommand Discoverability UX**:
  - Aligned command shorthands to the front (`[s] server`, `[m] monitor`, `[p] ping`, `[c] config`, `[i] install`, `[u] update`, `[d] driver`) in root `--help` for uniform visual scanning.
  - Added interactive `[c] Check / validate configuration` action directly into the `db-grep config` dashboard menu.
  - Enhanced `db-grep config --help` with full descriptions of all subcommands (`check`, `list`, `add`, `migrate`, `edit`, `disable`, `enable`, `delete`) and practical copy-paste examples.

### Fixed
- **Tag-Based Changelog Resolution**: Switched `fetch_multi_version_changelog` to query release tag snapshots (`/v<tag>/CHANGELOG.md`) before falling back to `main`. Bypasses Fastly CDN 5-minute caching on GitHub raw branch URLs and ensures newly published releases are never omitted when updating across version jumps.

## [0.8.1] - 2026-11-19

### Added
- **Automated Configuration Migration Tool (`db-grep config migrate`)**:
  - Automatically detects and migrates legacy pre-v0.8.0 configuration keys (`read_metadata`, `auto_metadata`, `read_data`, `auto_tables`, `always_ask_tables`, `sensitive_columns`) to the modern capability list schema (`allow_metadata`, `allow_data`, `allow_tables`, `restricted_tables`, `restricted_columns`).
  - Safe in-place migration that preserves user comments, formatting, and indentation.
  - Automatically creates a backup (`config.toml.bak`) with strict POSIX permissions (`0o600`) before applying any modifications.
  - Interactive integration in `db-grep update`: prompts user to auto-migrate legacy configs immediately after a package upgrade.
  - Smart hints in `db-grep config check`: displays actionable migration tips when legacy keys are detected.
  - Interactive migration option (`[m]`) directly in the `db-grep config` interactive dashboard.

## [0.8.0] - 2026-11-19

### Changed
- **Unified & Granular Access Control Configuration (Breaking Change)**:
  - Replaced inconsistent key naming and boolean permissions with capability lists and symmetric policy conventions:
    - `allow_metadata: list[str]` (e.g. `["read"]`): Extensible capability list governing metadata tools (`list_tables`, `find_objects`, `search_columns`, `describe_table`).
    - `allow_data: list[str]` (e.g. `["read"]` or `[]`): Extensible capability list governing direct `query` executions.
    - `allow_tables: list[str]`: Table patterns permitted for auto-approved data access (replaces `auto_tables`).
    - `restricted_tables: list[str]`: Table patterns strictly requiring manual human approval on every access (replaces `always_ask_tables`).
    - `restricted_columns: list[str]`: Column patterns strictly requiring manual human approval on every access (replaces `sensitive_columns`).
  - Interactive `[a]lways allow` prompt in `db-grep monitor` appends approved tables to `allow_tables` in `config.toml`.
  - Updated CLI configuration inspector and interactive `db-grep config add` wizard to reflect the new access control schema.

### Added
- **Granular Database Access Control Engine**:
  - **Decoupled Metadata vs Data Permissions**: Split schema exploration (`allow_metadata = ["read"]`) from row queries (`allow_data = []`).
  - **Column-Level Access Control (`restricted_columns`)**: Protect specific sensitive columns (e.g. `salary*`, `user.nik`, `password_hash`). Any query referencing restricted columns strictly triggers manual approval even if the table is in `allow_tables`. Guarded wildcard (`SELECT *`) queries against tables containing restricted columns.
  - **Strict Table Override (`restricted_tables`)**: High-priority table list (e.g. `pegawai_salary`, `user_identity`) that mandates manual human confirmation every time, overriding `allow_tables` and `allow_data`.
  - **Interactive Terminal Persistence**: Replaced blunt session-wide auto-approve with per-table auto-allow. Pressing `[a]lways allow` in `db-grep monitor` automatically appends the queried table(s) to `allow_tables` in `config.toml` without manual file editing. If restricted data is touched, auto-allow persistence is automatically disabled.
  - **Contextual Terminal Monitor Display**: Enhanced the monitor prompt to explicitly display accessed tables, queried columns, and trigger reasons (e.g. `[RESTRICTED COLUMN]`, `[RESTRICTED TABLE]`, or `[UNAPPROVED TABLE]`).

## [0.7.0] - 2026-11-19

### Added
- **`search_columns` MCP Tool**: Fast cross-table and cross-schema column search (`*pattern*`, `%...%`) to quickly locate where specific business fields (e.g. `CIF_NUMBER`, `NIK`, `KODE_CABANG`) are stored across PostgreSQL, MySQL, and Oracle.
- **`sample_table` MCP Tool**: 1-click preview of 1–50 actual data rows (default: 5) from a table or view without manually authoring SQL or thinking about database limit dialects. Automatically applies `ROWNUM <= N` for Oracle and `LIMIT N` for PostgreSQL/MySQL.
- **Whitelist `auto_metadata` Alignment**: Included both `search_columns` and `sample_table` under `auto_metadata = true` for frictionless exploration without repetitive manual approval prompts.
- **`describe_table` Token Optimization & Compact Mode**: Added optional `compact: bool = False` parameter to `describe_table` to output concise column names and data types, reducing payload size by up to ~70% on wide enterprise tables (100+ columns).
- **Compilation Status & Diagnostics on `describe_table`**: Returns object status (`VALID` or `INVALID`) by querying `all_objects` in Oracle. Detects broken views (e.g. `DATA_TYPE = 'UNDEFINED'`) upfront and attaches diagnostic guidance before failing with `ORA-04063`.
- **Educational Dialect Hints for Oracle Queries**: Intercepts `ORA-00933` syntax errors when a query against Oracle includes `LIMIT <n>`, providing an actionable hint to use `FETCH FIRST <n> ROWS ONLY` (Oracle 12c+) or `WHERE ROWNUM <= <n>`.

### Changed
- **Interactive Harness Selection UI**: Redesigned `db-grep install` menu with aligned tabular columns, intelligent path shortening (`~`), responsive terminal wrapping, and ANSI color-coded status badges:
  - `✓ configured` (green) for up-to-date clients.
  - `▲ update needed` (yellow) for clients running legacy `db` server names or deprecated `--server` flags.
  - `○ ready to install` (cyan) for detected clients ready for initial setup.
  - `· not installed` (dim) for harnesses not found on the system.

## [0.6.0] - 2026-11-19

### Changed
- **Modern Subcommand Architecture & Ergonomic CLI**:
  - Restructured CLI to a clean command hierarchy (`server`, `monitor`, `ping`, `config`, `install`, `update`, `driver`) with symmetrical first-letter shorthand flags (`-s`, `-m`, `-p`, `-c`, `-i`, `-u`, `-d`).
  - Added `ping` command (`-p`) to quickly probe database connectivity and measure latency.
  - Renamed `init` to `install` for intuitive multi-client integration at any workflow stage (`db-grep install codex`, `db-grep install claude`).
  - Consolidated update workflows into `db-grep update` with direct upgrade flags (`-y` / `-f`) to bypass confirmation prompts.
  - Integrated configuration diagnostics under `db-grep config check`.
- **Zero-Overhead Driver Bootstrapping**:
  - Made external driver scanning lazy: filesystem inspection and Instant Client resolution now only run when executing `server` or `ping`. Commands like `monitor`, `install`, `update`, and `config` boot instantly without driver disk I/O.
- **Minimal Default Configuration**:
  - Simplified default `config.toml` template generated by `db-grep install` to essential settings, pointing directly to `db-grep config add`.

## [0.5.1] - 2026-11-19

### Added
- **Zero-Dependency Terminal Markdown Renderer**: Formatted changelog output in `db-grep update` with styled version headers, colored badges (`[Added]`, `[Changed]`, `[Fixed]`), and auto-wrapped indented lists.

### Fixed
- **Multi-Version Changelog Aggregator**: Fixed missing release notes when upgrading across multiple version jumps during `db-grep update`. Automatically aggregates intermediate release notes between current and target versions.

## [0.5.0] - 2026-11-18

### Added
- **Dual-Phase Approval Monitor (`db-grep monitor`)**:
  - Automatically replays recent database activity from `audit.jsonl` upon opening the monitor, showing recent query status, execution latency, and offline pause events.
  - Live streams non-blocking database pings, latency metrics, and metadata queries in addition to standard SQL queries.
  - Added configurable `history_limit` setting in `config.toml` under `[monitor]` (default: `20`).
- **Session Auto-Approve & Instant Lock**:
  - Added session auto-approval toggle (`a`) and on-demand lock hotkey (`l`) with multi-session selection menus.
- **Enriched Audit Logging**: Unified audit event records across blocked, approved, rejected, and metadata inspection events.

## [0.4.0] - 2026-11-18

### Added
- **`find_objects` MCP Tool**: Fast, server-side pattern and wildcard search (`%...%`, `*`) across tables, views, and synonyms across all accessible schemas without cap-limit truncation.
- Multi-dialect support for PostgreSQL, MySQL, and Oracle object exploration.
- Extended Oracle `list_tables` and `describe_table` to automatically include views and synonyms.
- Included `find_objects` under the default `auto_metadata = true` rule.

### Changed
- Standardized MCP context parameter naming from `konteks` to standard English `context` with backward compatibility aliases.

## [0.3.1] - 2026-11-18

### Fixed
- Robust update checking with GitHub API rate limit handling, `GITHUB_TOKEN` support, and automatic two-tier fallback (web release redirect and Git protocol tag query).

## [0.3.0] - 2026-11-18

### Added
- **Automated Oracle Instant Client Management**: User-space downloader and installer (`~/.config/db-grep/instantclient`) for seamless Thick mode support of legacy Oracle 10g databases (`DPY-3015`) without `sudo` or system directory modification.
- Dedicated driver management command (`db-grep driver oracle`).
- Interactive on-demand driver setup during connection tests when a legacy Oracle database is detected.
- Hardened archive extraction with Zip Slip and symlink traversal mitigations.

## [0.2.1] - 2026-11-13

### Fixed
- Windows file-lock mitigation: detected running background server/monitor processes and offered automatic termination prior to package upgrade to prevent `[WinError 32]`.

## [0.2.0] - 2026-11-13

### Added
- **Driver-Scoped Database Configuration**: `[db.<driver>.<name>]` syntax supporting identical connection names across different database types without collisions.
- **Multi-Database Support**: Added native MySQL support alongside PostgreSQL and Oracle.
- **Interactive Configuration Manager**: `db-grep config` CLI to add, list, enable, disable, and edit database connections without manual file edits.
- **Database Connection Diagnostics**: `db-grep test` command for lightweight latency and connectivity tests.
- **Universal Secret Interpolation**: Secure password resolution via `${env:VAR}`, `${file:/path}`, and shorthand references with automatic URL-encoding in URI DSNs.
- **Secret Redaction Layer**: Automatically masks credentials (`***`) across logs, monitor displays, and error messages.
- **Dynamic Port Allocation**: Automatic monitor port fallback across safe private ports (`48260`–`48270`) with state synchronization (`monitor.port`).
- **Multi-Harness AI Client Integration**: One-step MCP configuration across 9 supported AI assistants (Claude Code, Claude Desktop, Cursor, Codex, OpenCode, Antigravity, Roo Code, Zed, Pi).

### Fixed
- Prevented terminal hang on Windows when opening configuration files in external editors.
- Fixed DSN parsing for credentials with special characters.

## [0.1.0] - 2026-11-11

### Added
- Initial release: Core MCP server with FastMCP for PostgreSQL and Oracle.
- Interactive terminal approval monitor UI bridge.
- Strict anti-bypass security guardrails (`MONITOR_OFFLINE`).
