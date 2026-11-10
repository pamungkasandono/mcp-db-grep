# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Renamed project and CLI to `db-grep`.
- Default config location moved to `~/.config/db-grep/config.toml` (with fallback).
- Global binary command updated to `db-grep`.

### Added
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
