import json
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
        return "0.2.0"


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


def execute_upgrade() -> bool:
    print(f"Upgrading db-grep from {GIT_INSTALL_URL}...")

    # Determine command based on environment (pip vs pipx vs direct python)
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", GIT_INSTALL_URL]

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
