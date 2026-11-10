# db-grep

Database MCP server with interactive approval monitor for AI assistants.

## Instalasi & Setup Otomatis

### 1. Install CLI
```bash
pip install git+https://github.com/pamungkasandono/mcp-db-grep.git
```
*(atau menggunakan `pipx install git+...` / `uv tool install git+...`)*

### 2. Inisialisasi & Hubungkan ke AI Client (Interactive Setup)
Cukup jalankan:
```bash
db-grep --init
```
Perintah ini akan:
- Mendeteksi harness/klien AI yang terpasang di komputer Anda (`opencode`, `claude` CLI, `claude-desktop`).
- Menanyakan pilihan harness yang ingin Anda hubungkan secara interaktif (bisa pilih salah satu atau semua).
- Menginstal aturan instruksi (`~/.config/db-grep/instructions.md`) agar AI berhenti dan menjelaskan tujuan query saat Anda menekan `n` (tolak) di monitor.
- Membuat template konfigurasi di `~/.config/db-grep/config.toml`.

*Tips: Jika ingin otomatis tanpa prompt interaktif:*
```bash
db-grep --init --client opencode   # hanya untuk Opencode
db-grep --init --client claude     # hanya untuk Claude Code CLI
db-grep --init --client all        # untuk semua klien yang terdeteksi
```

---

## Konfigurasi Database
Edit file `~/.config/db-grep/config.toml` (chmod 600):
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

2. **Gunakan di Chat AI (Opencode / Claude):**
   Minta AI membaca database seperti biasa:
   > *"Tampilkan 5 user terbaru dari database bekal-akun"*
   
   Saat query butuh izin, terminal monitor akan meminta konfirmasi `[y/n]`. Jika Anda tolak (`n`), AI akan berhenti dan menjelaskan untuk apa query tersebut.

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
