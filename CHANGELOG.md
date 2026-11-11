# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
