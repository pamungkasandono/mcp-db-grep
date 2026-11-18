"""
Oracle Instant Client Manager for db-grep.

Provides zero-config, self-contained Oracle Instant Client management:
- Auto-detects local user-space client directory (~/.config/db-grep/instantclient)
- Automatically initializes Thick mode in python-oracledb for legacy (10g) support
- Automatically resolves Unix symlinks in extracted zip archives
- Automatically handles distribution-specific libraries (e.g., Ubuntu 24.04 libaio.so.1t64)
- Preloads dependencies on Linux without requiring root / ldconfig / LD_LIBRARY_PATH
- One-click auto-download and extraction from official Oracle CDN
"""

import ctypes
import glob
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def get_instantclient_base_dir() -> Path:
    config_env = os.environ.get("DB_GREP_CONFIG", os.environ.get("DB_MCP_CONFIG"))
    if config_env:
        base = Path(config_env).expanduser().parent
    else:
        base = Path("~/.config/db-grep").expanduser()
    return base / "instantclient"


def find_oracle_client_dir() -> Path | None:
    """Finds the directory containing Oracle client shared libraries."""
    base_dir = get_instantclient_base_dir()
    if base_dir.exists():
        patterns = ["libclntsh.so*", "libclntsh.dylib*", "oci.dll"]
        for p in patterns:
            matches = list(base_dir.rglob(p))
            if matches:
                return matches[0].parent

    for env_var in ("OCI_LIB_DIR", "ORACLE_HOME"):
        val = os.environ.get(env_var)
        if val and Path(val).exists():
            lib_p = Path(val)
            if (lib_p / "lib").exists():
                return lib_p / "lib"
            return lib_p

    return None


def is_driver_installed() -> bool:
    return find_oracle_client_dir() is not None


def _auto_resolve_libaio(client_dir: Path):
    """
    On Ubuntu 24.04+ / Debian 13+, libaio was renamed to libaio.so.1t64 (time64 transition).
    Oracle Instant Client looks for libaio.so.1.
    If libaio.so.1 is missing in the system or client directory, dynamically resolve the system's
    libaio using ctypes.util.find_library and create a local symlink inside the user's client directory.
    """
    if not sys.platform.startswith("linux"):
        return

    local_libaio = client_dir / "libaio.so.1"
    if local_libaio.exists():
        return

    import ctypes.util

    # 1. Search dynamically using ctypes.util.find_library
    candidates = []
    found_name = ctypes.util.find_library("aio")
    if found_name:
        candidates.append(found_name)
    candidates.extend(["libaio.so.1t64", "libaio.so.1"])

    search_dirs = [
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/usr/lib/aarch64-linux-gnu"),
        Path("/lib/x86_64-linux-gnu"),
        Path("/lib/aarch64-linux-gnu"),
        Path("/usr/lib64"),
        Path("/usr/lib"),
        Path("/lib64"),
        Path("/lib"),
    ]

    for cand_name in candidates:
        cand_p = Path(cand_name)
        if cand_p.is_absolute() and cand_p.exists():
            try:
                local_libaio.symlink_to(cand_p)
                return
            except Exception:
                pass

        for s_dir in search_dirs:
            full_path = s_dir / cand_name
            if full_path.exists():
                try:
                    local_libaio.symlink_to(full_path)
                    return
                except Exception:
                    pass


def _fix_extracted_symlinks(client_dir: Path):
    """
    Standard Python zipfile extracts Unix symlinks as small text files containing the target name.
    This converts any such placeholder text files into real filesystem symlinks, safely validating
    that targets do not escape the client directory (anti-traversal).
    """
    if not hasattr(os, "symlink"):
        return

    resolved_base = client_dir.resolve()

    for item in list(client_dir.glob("*")):
        if item.is_file() and not item.is_symlink() and item.stat().st_size < 100:
            try:
                content = item.read_text(encoding="utf-8", errors="ignore").strip()
                # Security check: disallow absolute paths or parent directory traversals
                if ".." in content or content.startswith("/") or content.startswith("\\"):
                    continue

                target = (client_dir / content).resolve()
                if target.exists() and str(target).startswith(str(resolved_base)) and content != item.name:
                    item.unlink()
                    item.symlink_to(content)
            except Exception:
                pass


def ensure_oracle_env():
    """
    On Linux, ld.so only reads LD_LIBRARY_PATH at process startup.
    If local Oracle Instant Client directory exists and is not in LD_LIBRARY_PATH,
    re-execute the current process in-place with LD_LIBRARY_PATH set so dlopen resolves
    all dependent shared libraries seamlessly.
    """
    if not sys.platform.startswith("linux") or os.environ.get("_DB_GREP_LD_REEXEC"):
        return

    # Don't re-exec if running under test runners or python -m
    argv0 = sys.argv[0] if sys.argv else ""
    if any(runner in argv0 for runner in ("pytest", "unittest")):
        return

    client_dir = find_oracle_client_dir()
    if not client_dir:
        return

    _fix_extracted_symlinks(client_dir)
    _auto_resolve_libaio(client_dir)
    client_dir_str = str(client_dir)
    cur_ld = os.environ.get("LD_LIBRARY_PATH", "")

    if client_dir_str not in cur_ld.split(":"):
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = f"{client_dir_str}:{cur_ld}" if cur_ld else client_dir_str
        env["_DB_GREP_LD_REEXEC"] = "1"
        try:
            if shutil.which(argv0):
                args = [argv0] + sys.argv[1:]
                os.execve(shutil.which(argv0), args, env)
            else:
                args = [sys.executable] + sys.argv
                os.execve(sys.executable, args, env)
        except Exception:
            pass


def ensure_thick_mode() -> bool:
    """
    Attempts to initialize python-oracledb in Thick mode if a local or system
    Oracle Instant Client is available. Returns True if Thick mode is active.
    """
    import oracledb

    if not oracledb.is_thin_mode():
        return True

    client_dir = find_oracle_client_dir()
    if not client_dir:
        return False

    client_dir_str = str(client_dir)
    _fix_extracted_symlinks(client_dir)
    _auto_resolve_libaio(client_dir)

    try:
        oracledb.init_oracle_client(lib_dir=client_dir_str)
        return True
    except oracledb.ProgrammingError:
        return True
    except Exception as e:
        sys.stderr.write(f"[db-grep] Warning: Failed to init Oracle client from {client_dir}: {e}\n")
        return False


def get_download_info() -> tuple[str, str]:
    """Returns (download_url, archive_name) for current OS and architecture."""
    plat = sys.platform
    arch = platform.machine().lower()

    if plat.startswith("linux"):
        if arch in ("aarch64", "arm64"):
            url = "https://download.oracle.com/otn_software/linux/instantclient/instantclient-basiclite-linux-arm64.zip"
            return url, "instantclient-linux-arm64.zip"
        else:
            url = "https://download.oracle.com/otn_software/linux/instantclient/instantclient-basiclite-linuxx64.zip"
            return url, "instantclient-linux-x64.zip"

    elif plat == "darwin":
        if arch in ("arm64", "aarch64"):
            url = "https://download.oracle.com/otn_software/mac/instantclient/instantclient-basiclite-macos-arm64.dmg"
            return url, "instantclient-macos-arm64.dmg"
        else:
            url = "https://download.oracle.com/otn_software/mac/instantclient/instantclient-basic-macos.zip"
            return url, "instantclient-macos-x64.zip"

    elif plat == "win32":
        url = "https://download.oracle.com/otn_software/nt/instantclient/instantclient-basiclite-windows.zip"
        return url, "instantclient-windows.zip"

    raise RuntimeError(f"Unsupported OS/architecture for automated Oracle client download: {plat} ({arch})")


def _download_with_progress(url: str, dest_path: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 db-grep/installer"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 64 * 1024

        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    percent = downloaded * 100 // total_size
                    mb_cur = downloaded / (1024 * 1024)
                    mb_total = total_size / (1024 * 1024)
                    sys.stdout.write(f"\r  Downloading Oracle Instant Client: {percent:3d}% ({mb_cur:.1f}/{mb_total:.1f} MB)")
                    sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()


def download_and_install_driver() -> Path:
    """Downloads official Oracle Instant Client BasicLite and extracts it to user-space directory."""
    url, filename = get_download_info()
    dest_dir = get_instantclient_base_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)

    print(f"Installing Oracle Instant Client for {sys.platform} ({platform.machine()})...")
    print(f"Target directory: {dest_dir}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_archive = Path(tmp_dir) / filename
        _download_with_progress(url, tmp_archive)

        print("  Extracting files...")
        if filename.endswith(".zip"):
            resolved_dest = dest_dir.resolve()
            with zipfile.ZipFile(tmp_archive, "r") as z:
                for member in z.infolist():
                    target_path = (dest_dir / member.filename).resolve()
                    # Security check: prevent zip slip (writing outside dest_dir)
                    if not str(target_path).startswith(str(resolved_dest)):
                        continue

                    mode = member.external_attr >> 16
                    is_symlink = stat.S_ISLNK(mode)
                    if is_symlink:
                        link_target = z.read(member).decode("utf-8").strip()
                        # Security check: disallow symlink targets pointing outside client dir
                        if ".." in link_target or link_target.startswith("/") or link_target.startswith("\\"):
                            continue
                        if target_path.exists():
                            target_path.unlink()
                        try:
                            target_path.symlink_to(link_target)
                        except Exception:
                            pass
                    else:
                        z.extract(member, dest_dir)
                        if mode:
                            try:
                                target_path.chmod(mode)
                            except Exception:
                                pass
        elif filename.endswith(".dmg") and sys.platform == "darwin":
            mount_point = Path(tmp_dir) / "mnt"
            mount_point.mkdir(exist_ok=True)
            subprocess.run(["hdiutil", "attach", str(tmp_archive), "-mountpoint", str(mount_point), "-nobrowse", "-quiet"], check=True)
            try:
                for item in mount_point.iterdir():
                    if item.is_dir() and item.name.startswith("instantclient"):
                        shutil.copytree(item, dest_dir / item.name, dirs_exist_ok=True)
                        break
            finally:
                subprocess.run(["hdiutil", "detach", str(mount_point), "-quiet"], check=False)

    found = find_oracle_client_dir()
    if not found:
        raise RuntimeError(f"Oracle client files were extracted, but could not locate shared libraries in {dest_dir}")

    _fix_extracted_symlinks(found)
    _auto_resolve_libaio(found)

    print(f"✓ Oracle Instant Client ready at: {found}")
    return found


def setup_oracle_driver_cli() -> bool:
    """CLI handler for Oracle driver setup via db-grep --setup-driver oracle."""
    print("--- db-grep Oracle Instant Client Setup ---")
    current = find_oracle_client_dir()
    if current:
        _fix_extracted_symlinks(current)
        _auto_resolve_libaio(current)
        print(f"Oracle Instant Client is already installed at:\n  {current}")
        try:
            ans = input("\nDo you want to re-download and re-install? [y/N]: ").strip().lower()
            if ans not in ("y", "yes"):
                print("Aborted.")
                return True
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return False

    try:
        download_and_install_driver()
        print("✓ Oracle Instant Client setup completed successfully.")
        print("  Legacy (10g) Oracle databases can now connect seamlessly.")
        return True
    except Exception as e:
        print(f"✗ Setup failed: {e}")
        return False
