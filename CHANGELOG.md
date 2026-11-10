# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Interactive setup command `db-grep --init` with client harness detection (`opencode`, `claude`, `claude-desktop`).
- Bundled instruction distribution `instructions/db-grep.md` automatically wired during init.
- `--client` flag for scripted non-interactive client registration.
- Standard `pyproject.toml` packaging supporting direct `pip install git+...` and `pipx install`.
- Editable install mode in `just install`.
- Single CLI entrypoint (`cli.py`) supporting `--server` and `--monitor` modes.
- Global executable wrapper `~/.local/bin/db-grep`.
- Opencode and Claude configuration support with `db-grep --server`.
- Strict AI denial protocol and feedback prompts on monitor rejection.

## [0.1.0] - 2026-11-11

### Added
- Core MCP server implementation with FastMCP for PostgreSQL and Oracle DB.
- TCP approval bridge with terminal monitor UI.
- Centralized TOML configuration reader at `~/.config/db-mcp/config.toml`.
- Justfile task runner and git configuration.
