# Contributing to db-grep

Thank you for contributing to `db-grep`! To maintain high code quality, clean documentation, and a consistent developer and user experience, please follow these guidelines.

---

## Changelog Guidelines (`CHANGELOG.md`)

The `CHANGELOG.md` file is a **user-facing release document**, not a git commit log or developer diary. Every entry must be curated for people who install, configure, and use `db-grep`.

### Core Principle
> **If a change does not affect how an end user or AI assistant interacts with `db-grep`, it does NOT belong in the changelog.**

---

### What to Include (DOs)

1. **User-Facing Features (`Added`)**:
   - New CLI commands, subcommands, or flags (e.g. `db-grep test`, `db-grep update -y`).
   - New MCP tools or schemas available to AI assistants (e.g. `find_objects`).
   - New supported database drivers or harnesses (e.g. MySQL support, Codex integration).
2. **Behavioral or Workflow Changes (`Changed`)**:
   - Breaking changes or command renames (e.g. `init` $\rightarrow$ `install`).
   - Changes to default configuration values, timeouts, or ports.
   - Performance optimizations that noticeably improve user-visible speed or startup latency.
3. **User-Visible Bug Fixes (`Fixed`)**:
   - Fixes for crashes, hangs, unhandled exceptions, or file-lock errors (e.g. Windows `[WinError 32]`).
   - Fixes for connection authentication errors, timeouts, or rate limits.
4. **Security Enhancements (`Security`)**:
   - Protections against path traversal, Zip Slip, or credential leakage in logs.

---

### What to Exclude (DON'Ts / Anti-Patterns)

Never include any of the following in `CHANGELOG.md`:

| Prohibited Category | Examples to Avoid | Why Excluded |
| :--- | :--- | :--- |
| **Private Function Names** | `_extract_dsn_passwords()`, `_config_has_raw_secrets`, `_install_json_mcp()` | Users interact with the CLI and MCP protocol, never with private Python helpers. |
| **Internal Code Refactoring** | *"Centralized metadata in `driver_manager.py`"*, *"Standardized SSOT version resolution via `tomllib`"* | Architectural refactoring that doesn't change user commands or configuration syntax is internal noise. |
| **Test Suite Metrics & Chores** | *"Added unit tests (expanded suite to 90 tests)"*, *"Fixed mock fixture in `test_server_logic.py`"* | Test additions are developer validation, not product capabilities. |
| **Cosmetic & Micro Tweaks** | *"Removed star symbol (`★`) from header"*, *"Renamed dummy test password from `rahasia` to `secret`"* | Trivial cosmetic details clutter release notes and dilute important announcements. |
| **Documentation README Edits** | *"Reorganized README.md to highlight CLI config"*, *"Fixed typo in docs"* | README wording changes are self-evident on GitHub; do not log them as software changes. |
| **Duplicate / Fragmented Entries** | Logging the same feature under both `Added` and `Changed` (e.g. 4 separate bullets for port 48260). | Consolidate related sub-tasks into a single, comprehensive bullet point. |

---

### Formatting & Style Rules

1. **Standard Categories**: Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/):
   - `Added` for new features.
   - `Changed` for changes in existing functionality.
   - `Deprecated` for soon-to-be removed features.
   - `Removed` for now removed features.
   - `Fixed` for any bug fixes.
   - `Security` for vulnerability fixes.
2. **Language**: **Strictly English only**.
3. **Structure**:
   - Start with a **Bold Title** summarizing the capability or change.
   - Follow with 1–2 concise sentences explaining the user benefit or syntax example.
   - Keep bullet points tight (maximum 1–3 sub-bullets for complex features).
4. **Drafting in `[Unreleased]`**:
   - When staging changes under `[Unreleased]`, merge and consolidate micro-commits into unified feature summaries before tagging a release.

---

### Example: Bad vs. Good Entry

#### ❌ Bad (Noisy, Internal, Cluttered):
```markdown
### Changed
- Refactored `cli.py` and `driver_manager.py` to use `ensure_driver_env()` lazily and imported `tomllib` to parse `pyproject.toml` version dynamically instead of `importlib.metadata`.
- Removed `_extract_dsn_passwords` duplicate calls in `server.py`.
- Updated test suite in `tests/test_cli.py` to add 5 new assertions (total 113 tests).
- Reorganized README.md.
```

#### ✅ Good (Clean, Impactful, User-Centric):
```markdown
### Changed
- **Zero-Overhead Driver Bootstrapping**:
  - Made external driver scanning lazy: filesystem inspection and Instant Client resolution now only execute when launching `server` or `test`. Commands like `monitor`, `install`, `update`, and `config` boot instantly without driver disk I/O.
```
