# db-grep

Database MCP server with interactive approval monitor for AI assistants.

## Instalasi Cepat (Pengguna / Non-Python Developer)

### Cara 1: Menggunakan pip
```bash
pip install git+https://github.com/pamungkasandono/mcp-db-grep.git
```
*(Perintah `db-grep` langsung tersedia secara global di terminal)*

### Cara 2: Menggunakan pipx (Disarankan untuk CLI Python terisolasi)
```bash
pipx install git+https://github.com/pamungkasandono/mcp-db-grep.git
```

### Cara 3: Menggunakan uv / uvx
```bash
uv tool install git+https://github.com/pamungkasandono/mcp-db-grep.git
```

---

## Konfigurasi
File konfigurasi berada di `~/.config/db-grep/config.toml` (chmod 600).

Contoh konfigurasi:
```toml
[monitor]
port = 8765

[db.bekal-akun]
type = "postgres"
description = "Database Akun Bekal"
dsn = "postgres://postgres@localhost:5432/bekal"
password = "your_password"
```

---

## Cara Pakai

1. **Jalankan Monitor (di terminal terpisah sebelum chat AI):**
   ```bash
   db-grep --monitor
   ```

2. **Daftarkan ke Claude / Opencode:**
   - **Claude CLI:**
     ```bash
     claude mcp add db -- db-grep --server
     ```
   - **Opencode (`opencode.jsonc`):**
     ```json
     "mcp": {
       "db": {
         "type": "local",
         "command": ["db-grep", "--server"],
         "enabled": true
       }
     }
     ```

---

## Pengembangan Lokal (Contributor)
```bash
python3 -m venv .venv
pip install -e .
just monitor
just server
```

## Standar Pengembangan
- **Commit**: Mengikuti [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).
- **Changelog**: Mengikuti [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
- **Versioning**: Mengikuti [Semantic Versioning 2.0.0](https://semver.org/).

## Lisensi
MIT

## Standar Pengembangan
- **Commit**: Mengikuti [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).
- **Changelog**: Mengikuti [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
- **Versioning**: Mengikuti [Semantic Versioning 2.0.0](https://semver.org/).

## Lisensi
MIT
