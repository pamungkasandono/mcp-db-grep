# db-mcp

Database MCP server with interactive approval monitor for AI assistants.

## Prasyarat
- Python 3.11+
- Virtual environment with `mcp<2`, `psycopg`, `oracledb`
- `just` task runner

## Instalasi
```bash
python3 -m venv .venv
just install
```

## Konfigurasi
File konfigurasi berada di `~/.config/db-mcp/config.toml` (chmod 600).

## Penggunaan / Menjalankan Task
```bash
# Jalankan monitor di terminal terpisah (via global command)
db-mcp --monitor

# Jalankan MCP server secara langsung
db-mcp --server

# Atau via task runner:
just monitor
just server
```

## Standar Pengembangan
- **Commit**: Mengikuti [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).
- **Changelog**: Mengikuti [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
- **Versioning**: Mengikuti [Semantic Versioning 2.0.0](https://semver.org/).

## Lisensi
MIT
