import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import tomllib
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

GITHUB_REPO = "pamungkasandono/mcp-db-grep"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RAW_CHANGELOG_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/CHANGELOG.md"
GIT_INSTALL_URL = f"git+https://github.com/{GITHUB_REPO}.git"


def get_current_version() -> str:
    # 1. In source checkout / development, pyproject.toml is the Single Source of Truth
    pyproject = Path(__file__).resolve().parent / "pyproject.toml"
    if pyproject.exists():
        try:
            with open(pyproject, "rb") as f:
                ver = tomllib.load(f).get("project", {}).get("version")
                if ver:
                    return str(ver)
        except Exception:
            pass

    # 2. When installed as package (wheel / site-packages)
    try:
        return version("db-grep")
    except PackageNotFoundError:
        return "0.0.0"


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


def _fetch_latest_via_web_redirect() -> dict:
    """Fallback: Get latest release tag via GitHub web redirect (not subject to api.github.com rate limits)."""
    url = f"https://github.com/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        final_url = resp.geturl()
        if "/tag/" in final_url:
            tag_name = final_url.split("/tag/")[-1].strip()
            return {
                "tag_name": tag_name,
                "body": "",
                "html_url": final_url,
            }
    raise ValueError(f"Could not extract tag from redirect URL: {final_url}")


def _fetch_latest_via_git() -> dict:
    """Fallback: Query git tags directly via git ls-remote."""
    repo_url = f"https://github.com/{GITHUB_REPO}.git"
    res = subprocess.run(
        ["git", "ls-remote", "--tags", repo_url],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if res.returncode != 0:
        raise RuntimeError(f"git ls-remote failed with exit code {res.returncode}")

    tags = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("refs/tags/"):
            tag = parts[1].replace("refs/tags/", "").replace("^{}", "").strip()
            if tag:
                tags.add(tag)
    if not tags:
        raise ValueError("No tags found via git ls-remote")

    sorted_tags = sorted(tags, key=parse_semver)
    latest_tag = sorted_tags[-1]
    return {
        "tag_name": latest_tag,
        "body": "",
        "html_url": f"https://github.com/{GITHUB_REPO}/releases/tag/{latest_tag}",
    }


def fetch_latest_release() -> dict:
    headers = {"User-Agent": "db-grep-updater"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Fallback if rate limited (403) or other HTTP error
        try:
            return _fetch_latest_via_web_redirect()
        except Exception:
            pass
        try:
            return _fetch_latest_via_git()
        except Exception:
            pass
        raise e
    except Exception as e:
        # Fallback for network issues contacting api.github.com
        try:
            return _fetch_latest_via_web_redirect()
        except Exception:
            pass
        try:
            return _fetch_latest_via_git()
        except Exception:
            pass
        raise e


def extract_changelog_between(changelog_text: str, current_ver: str, target_ver: str) -> str:
    """Extract sections from changelog markdown for versions > current_ver and <= target_ver."""
    curr_tuple = parse_semver(current_ver)
    target_tuple = parse_semver(target_ver)

    version_section_re = re.compile(r"^##\s+\[([^\]]+)\](?:\s+-\s+(\d{4}-\d{2}-\d{2}))?", re.MULTILINE)
    matches = list(version_section_re.finditer(changelog_text))
    if not matches:
        return ""

    included_sections = []
    for i, match in enumerate(matches):
        ver_str = match.group(1).strip()
        if ver_str.lower() == "unreleased":
            continue
        try:
            ver_tuple = parse_semver(ver_str)
        except Exception:
            continue

        if curr_tuple < ver_tuple <= target_tuple:
            start_pos = match.start()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(changelog_text)
            included_sections.append(changelog_text[start_pos:end_pos].strip())

    return "\n\n".join(included_sections)


def fetch_multi_version_changelog(current_ver: str, target_ver: str, fallback_body: str = "") -> str:
    """Fetch remote CHANGELOG.md and extract notes for all versions > current_ver and <= target_ver."""
    headers = {"User-Agent": "db-grep-updater"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(RAW_CHANGELOG_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            extracted = extract_changelog_between(text, current_ver, target_ver)
            if extracted.strip():
                return extracted.strip()
    except Exception:
        pass

    return fallback_body.strip()


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

        changelog = ""
        if has_update:
            changelog = fetch_multi_version_changelog(current, latest_ver, fallback_body=body)

        return {
            "success": True,
            "current_version": current,
            "latest_version": latest_ver,
            "tag_name": latest_tag,
            "has_update": has_update,
            "changelog": changelog,
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
        try:
            import config_manager
            if config_manager.CONFIG.exists():
                legacy = config_manager.detect_legacy_keys(config_manager.CONFIG.read_text(encoding="utf-8"))
                if legacy:
                    print(f"\n⚠ Detected legacy configuration keys in {config_manager.CONFIG}: {', '.join(legacy)}")
                    config_manager.migrate_config_file(interactive=True)
        except Exception:
            pass
        return True
    else:
        print(f"\n✗ Upgrade failed with exit code {res.returncode}. You can manually run:")
        print(f"  pip install --upgrade {GIT_INSTALL_URL}")
        return False


def render_markdown_changelog(md_text: str, max_width: int = 85) -> str:
    """Format markdown changelog cleanly for terminal display with colors and hanging indents."""
    if not md_text or not md_text.strip():
        return ""

    BOLD = "\033[1m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    RESET = "\033[0m"

    term_w = shutil.get_terminal_size((80, 20)).columns
    width = max(60, min(term_w - 2, max_width))

    lines = md_text.splitlines()
    out = []

    for line in lines:
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue

        # Version header: ## [0.5.0] - 2026-11-18
        m_ver = re.match(r"^##\s+\[([^\]]+)\](?:\s+-\s+(\d{4}-\d{2}-\d{2}))?", stripped)
        if m_ver:
            ver, date = m_ver.group(1), m_ver.group(2)
            title = f"━━ [v{ver}]" + (f" ({date}) " if date else " ")
            bar_len = max(5, width - len(title))
            bar = "━" * bar_len
            out.append(f"\n{BOLD}{CYAN}{title}{bar}{RESET}")
            continue

        # Category header: ### Added / Changed / Fixed
        m_cat = re.match(r"^###\s+(.*)", stripped)
        if m_cat:
            cat = m_cat.group(1).strip()
            cat_lower = cat.lower()
            color = GREEN if "add" in cat_lower else (YELLOW if "change" in cat_lower else MAGENTA)
            out.append(f"  {BOLD}{color}[{cat}]{RESET}")
            continue

        # Check bullet level
        leading_spaces = len(raw) - len(raw.lstrip())
        is_sub = leading_spaces >= 2 or raw.startswith("  -") or raw.startswith("    -") or raw.startswith("\t-")
        content = stripped

        if content.startswith(("-", "*")):
            content = content[1:].strip()
            content = re.sub(r"\*\*([^*]+)\*\*", r"\033[1m\1\033[0m", content)
            content = re.sub(r"`([^`]+)`", r"\033[33m\1\033[0m", content)

            init_indent = "    • " if is_sub else "  • "
            sub_indent = "      " if is_sub else "    "

            wrapped = textwrap.fill(
                content,
                width=width,
                initial_indent=init_indent,
                subsequent_indent=sub_indent,
                break_long_words=False,
                break_on_hyphens=False,
            )
            out.append(wrapped)
        else:
            content = re.sub(r"\*\*([^*]+)\*\*", r"\033[1m\1\033[0m", content)
            content = re.sub(r"`([^`]+)`", r"\033[33m\1\033[0m", content)
            indent = "    " if is_sub else "  "
            wrapped = textwrap.fill(
                content,
                width=width,
                initial_indent=indent,
                subsequent_indent=indent,
            )
            out.append(wrapped)

    return "\n".join(out).strip()


def run_check_update_cli(prompt_upgrade: bool = True):
    print("Checking for updates from GitHub...")
    info = check_for_updates()

    if not info["success"]:
        print(f"Could not check for updates: {info.get('error')}")
        print("\nTip: You can upgrade directly without checking by running:")
        print("  db-grep update -y   (or: db-grep update --yes)")
        sys.exit(1)

    curr = info["current_version"]
    latest = info["latest_version"]

    if not info["has_update"]:
        print(f"✓ db-grep is up to date (version {curr}).")
        return

    print(f"\nNew version available: {curr} → {latest}")
    if info.get("changelog"):
        print("\nChangelog:")
        rendered = render_markdown_changelog(info["changelog"])
        print(rendered)

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
