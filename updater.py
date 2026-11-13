import json
import os
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, version

GITHUB_REPO = "pamungkasandono/mcp-db-grep"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GIT_INSTALL_URL = f"git+https://github.com/{GITHUB_REPO}.git"


def get_current_version() -> str:
    try:
        return version("db-grep")
    except PackageNotFoundError:
        return "0.2.1"


def parse_semver(ver_str: str) -> tuple[int, int, int]:
    cleaned = ver_str.lstrip("v").strip()
    parts = cleaned.split(".")
    try:
        return (
            int(parts[0]) if len(parts) > 0 else 0,
            int(parts[1]) if len(parts) > 1 else 0,
            int(parts[2]) if len(parts) > 2 else 0,
        )
    except ValueError:
        return (0, 0, 0)


def fetch_latest_release() -> dict:
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"User-Agent": "db-grep-updater"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_updates() -> dict:
    current = get_current_version()
    try:
        data = fetch_latest_release()
        latest_tag = data.get("tag_name", "0.0.0")
        latest_ver = latest_tag.lstrip("v").strip()
        body = data.get("body", "")

        curr_tuple = parse_semver(current)
        latest_tuple = parse_semver(latest_ver)

        has_update = latest_tuple > curr_tuple

        return {
            "success": True,
            "current_version": current,
            "latest_version": latest_ver,
            "tag_name": latest_tag,
            "has_update": has_update,
            "changelog": body,
            "release_url": data.get("html_url", ""),
        }
    except Exception as e:
        return {
            "success": False,
            "current_version": current,
            "error": str(e),
        }


def _find_running_db_grep_pids() -> list[tuple[int, str]]:
    """Return list of (pid, cmdline) for other running db-grep processes."""
    self_pid = os.getpid()
    parent_pid = os.getppid()
    results: list[tuple[int, str]] = []

    if sys.platform == "win32":
        ps_cmd = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -eq 'db-grep.exe' -or $_.CommandLine -like '*db-grep*' } | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.Name) $($_.CommandLine)\" }"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in res.stdout.splitlines():
                if "|" not in line:
                    continue
                pid_str, cmdline = line.split("|", 1)
                if pid_str.strip().isdigit():
                    pid = int(pid_str.strip())
                    if pid not in (self_pid, parent_pid) and "powershell" not in cmdline.lower():
                        results.append((pid, cmdline.strip()[:80]))
        except Exception:
            pass
    else:
        try:
            res = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=5)
            for line in res.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) < 2 or not parts[0].isdigit():
                    continue
                pid, cmdline = int(parts[0]), parts[1]
                if pid in (self_pid, parent_pid):
                    continue
                if "db-grep" in cmdline and "ps -eo" not in cmdline:
                    results.append((pid, cmdline[:80]))
        except Exception:
            pass

    return results


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def execute_upgrade() -> bool:
    running = _find_running_db_grep_pids()
    if running:
        print(f"\n⚠ Warning: Found {len(running)} running db-grep process(es) in the background:")
        for pid, cmdline in running:
            print(f"  - PID {pid}: {cmdline}")
        print("These processes must be stopped before upgrading (to release file locks on db-grep).")
        try:
            ans = input("Stop all running db-grep processes and continue upgrade? [y/n]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nUpgrade cancelled.")
            return False
        if ans != "y":
            print("Upgrade cancelled.")
            return False
        _kill_pids([pid for pid, _ in running])
        print("✓ Stopped background db-grep processes.")

    print(f"Upgrading db-grep from {GIT_INSTALL_URL}...")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", GIT_INSTALL_URL]

    if sys.platform == "win32":
        # On Windows, db-grep.exe cannot be overwritten while this process is still running.
        # Launch pip after a 1s delay so the wrapper db-grep.exe exits and releases the lock first.
        quoted_cmd = subprocess.list2cmdline(cmd)
        subprocess.Popen(["cmd", "/c", f"ping 127.0.0.1 -n 2 >nul & {quoted_cmd}"])
        print("Detaching updater so Windows can overwrite db-grep.exe...")
        return True

    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n✓ db-grep successfully upgraded! Restart your terminal or clients to use the new version.")
        return True
    else:
        print(f"\n✗ Upgrade failed with exit code {res.returncode}. You can manually run:")
        print(f"  pip install --upgrade {GIT_INSTALL_URL}")
        return False


def run_check_update_cli(prompt_upgrade: bool = True):
    print("Checking for updates from GitHub...")
    info = check_for_updates()

    if not info["success"]:
        print(f"Could not check for updates: {info.get('error')}")
        sys.exit(1)

    curr = info["current_version"]
    latest = info["latest_version"]

    if not info["has_update"]:
        print(f"✓ db-grep is up to date (version {curr}).")
        return

    print(f"\n★ New version available: {curr} → {latest}")
    if info.get("changelog"):
        print("\nChangelog:")
        for line in info["changelog"].strip().splitlines():
            print(f"  {line}")

    if info.get("release_url"):
        print(f"\nRelease details: {info['release_url']}")

    if prompt_upgrade:
        try:
            ans = input("\nDo you want to upgrade now? [y/n]: ").strip().lower()
            if ans == "y":
                execute_upgrade()
            else:
                print("Upgrade skipped.")
        except (KeyboardInterrupt, EOFError):
            print("\nUpgrade skipped.")
