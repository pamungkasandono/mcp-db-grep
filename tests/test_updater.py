import unittest
from unittest.mock import MagicMock, patch

import updater


class TestUpdater(unittest.TestCase):
    def test_get_current_version_reads_pyproject_ssot(self):
        ver = updater.get_current_version()
        self.assertEqual(ver, "0.10.1")

    def test_get_current_version_fallback_package_or_zero(self):
        with patch.object(updater, "Path") as mock_path:
            mock_path.return_value.resolve.return_value.parent.__truediv__.return_value.exists.return_value = False
            with patch("updater.version", return_value="1.2.3"):
                self.assertEqual(updater.get_current_version(), "1.2.3")
            with patch("updater.version", side_effect=updater.PackageNotFoundError):
                self.assertEqual(updater.get_current_version(), "0.0.0")

    def test_parse_semver(self):
        self.assertEqual(updater.parse_semver("0.1.0"), (0, 1, 0))
        self.assertEqual(updater.parse_semver("v0.1.2"), (0, 1, 2))
        self.assertEqual(updater.parse_semver("v1.2.3-beta"), (0, 0, 0))
        self.assertEqual(updater.parse_semver("invalid"), (0, 0, 0))

    def test_check_for_updates_up_to_date(self):
        mock_data = {
            "tag_name": "v0.1.0",
            "body": "No changes",
            "html_url": "https://github.com/pamungkasandono/mcp-db-grep/releases/tag/v0.1.0",
        }
        with patch.object(updater, "fetch_latest_release", return_value=mock_data):
            with patch.object(updater, "get_current_version", return_value="0.1.0"):
                info = updater.check_for_updates()
                self.assertTrue(info["success"])
                self.assertFalse(info["has_update"])
                self.assertEqual(info["latest_version"], "0.1.0")

    def test_check_for_updates_new_version_available(self):
        mock_data = {
            "tag_name": "v0.2.0",
            "body": "- Major feature additions",
            "html_url": "https://github.com/pamungkasandono/mcp-db-grep/releases/tag/v0.2.0",
        }
        with patch.object(updater, "fetch_latest_release", return_value=mock_data):
            with patch.object(updater, "fetch_multi_version_changelog", return_value="- Major feature additions"):
                with patch.object(updater, "get_current_version", return_value="0.1.0"):
                    info = updater.check_for_updates()
                    self.assertTrue(info["success"])
                    self.assertTrue(info["has_update"])
                    self.assertEqual(info["latest_version"], "0.2.0")
                    self.assertIn("Major feature additions", info["changelog"])

    def test_check_for_updates_network_failure(self):
        with patch.object(updater, "fetch_latest_release", side_effect=Exception("Network error")):
            info = updater.check_for_updates()
            self.assertFalse(info["success"])
            self.assertIn("Network error", info["error"])

    def test_execute_upgrade_warns_and_kills_running_processes_on_confirm(self):
        with patch.object(updater, "_find_running_db_grep_pids", return_value=[(1001, "db-grep --server"), (1002, "db-grep --monitor")]), \
             patch("builtins.input", return_value="y"), \
             patch.object(updater, "_kill_pids") as mock_kill, \
             patch.object(updater.sys, "platform", "linux"), \
             patch.object(updater.subprocess, "run", return_value=MagicMock(returncode=0)) as mock_run:
            ok = updater.execute_upgrade()
            self.assertTrue(ok)
            mock_kill.assert_called_once_with([1001, 1002])
            mock_run.assert_called_once()

    def test_execute_upgrade_cancels_when_user_declines_kill(self):
        with patch.object(updater, "_find_running_db_grep_pids", return_value=[(1001, "db-grep --server")]), \
             patch("builtins.input", return_value="n"), \
             patch.object(updater, "_kill_pids") as mock_kill, \
             patch.object(updater.subprocess, "run") as mock_run:
            ok = updater.execute_upgrade()
            self.assertFalse(ok)
            mock_kill.assert_not_called()
            mock_run.assert_not_called()

    def test_execute_upgrade_windows_uses_delayed_detached_pip(self):
        with patch.object(updater, "_find_running_db_grep_pids", return_value=[]), \
             patch.object(updater.sys, "platform", "win32"), \
             patch.object(updater.subprocess, "Popen") as mock_popen, \
             patch.object(updater.subprocess, "run") as mock_run:
            ok = updater.execute_upgrade()
            self.assertTrue(ok)
            mock_popen.assert_called_once()
            mock_run.assert_not_called()

    def test_find_running_pids_posix_and_windows(self):
        # 1. POSIX (Linux / macOS) ps output parsing, excluding self and parent PID
        ps_out = (
            f"  {updater.os.getpid()} /usr/bin/python3 cli.py -U\n"
            f"  {updater.os.getppid()} /bin/bash\n"
            "  2001 /home/user/.local/bin/db-grep --server\n"
            "  2002 /home/user/.local/bin/db-grep --monitor\n"
            "  2003 ps -eo pid=,args=\n"
        )
        with patch.object(updater.sys, "platform", "linux"), \
             patch.object(updater.subprocess, "run", return_value=MagicMock(stdout=ps_out)):
            pids = updater._find_running_db_grep_pids()
            self.assertEqual([p for p, _ in pids], [2001, 2002])

        # 2. Windows PowerShell CIM output parsing
        win_out = (
            f"{updater.os.getpid()}|db-grep.exe db-grep -U\n"
            "3001|db-grep.exe \"C:\\Scripts\\db-grep.exe\" --server\n"
            "3002|powershell.exe Get-CimInstance Win32_Process db-grep\n"
        )
        with patch.object(updater.sys, "platform", "win32"), \
             patch.object(updater.subprocess, "run", return_value=MagicMock(stdout=win_out)):
            pids_win = updater._find_running_db_grep_pids()
            self.assertEqual([p for p, _ in pids_win], [3001])


    def test_fetch_latest_via_git_parses_tags(self):
        sample_git_output = (
            "7b4c847 refs/tags/v0.1.0\n"
            "cbf2373 refs/tags/v0.1.0^{}\n"
            "926d5b8 refs/tags/v0.2.0\n"
            "37a95a3 refs/tags/v0.2.1\n"
            "8cf6a7f refs/tags/v0.3.0\n"
            "a663b17 refs/tags/v0.3.0^{}\n"
        )
        with patch.object(updater.subprocess, "run", return_value=MagicMock(returncode=0, stdout=sample_git_output)):
            data = updater._fetch_latest_via_git()
            self.assertEqual(data["tag_name"], "v0.3.0")
            self.assertIn("v0.3.0", data["html_url"])

    def test_fetch_latest_release_fallback_on_403(self):
        import urllib.error
        http_403 = urllib.error.HTTPError("url", 403, "rate limit exceeded", {}, None)
        with patch("urllib.request.urlopen", side_effect=http_403):
            with patch.object(updater, "_fetch_latest_via_web_redirect", return_value={"tag_name": "v0.3.0", "body": "", "html_url": "url"}) as mock_web:
                data = updater.fetch_latest_release()
                self.assertEqual(data["tag_name"], "v0.3.0")
                mock_web.assert_called_once()

    def test_fetch_latest_release_fallback_to_git_when_web_fails(self):
        import urllib.error
        http_403 = urllib.error.HTTPError("url", 403, "rate limit exceeded", {}, None)
        with patch("urllib.request.urlopen", side_effect=http_403):
            with patch.object(updater, "_fetch_latest_via_web_redirect", side_effect=Exception("Web failed")):
                with patch.object(updater, "_fetch_latest_via_git", return_value={"tag_name": "v0.3.0", "body": "", "html_url": "url"}) as mock_git:
                    data = updater.fetch_latest_release()
                    self.assertEqual(data["tag_name"], "v0.3.0")
                    mock_git.assert_called_once()

    def test_extract_changelog_between_multi_version(self):
        sample_changelog = """# Changelog

## [Unreleased]

## [0.5.0] - 2026-11-18
### Added
- Dual-Phase monitor

## [0.4.0] - 2026-11-18
### Added
- find_objects tool

## [0.3.1] - 2026-11-18
### Fixed
- Rate limit handling

## [0.3.0] - 2026-11-18
### Added
- Oracle driver management
"""
        # Jump from 0.3.1 to 0.5.0: should include 0.5.0 and 0.4.0, but NOT 0.3.1 or 0.3.0
        res = updater.extract_changelog_between(sample_changelog, "0.3.1", "0.5.0")
        self.assertIn("## [0.5.0]", res)
        self.assertIn("Dual-Phase monitor", res)
        self.assertIn("## [0.4.0]", res)
        self.assertIn("find_objects tool", res)
        self.assertNotIn("## [0.3.1]", res)
        self.assertNotIn("Rate limit handling", res)
        self.assertNotIn("## [0.3.0]", res)

    def test_extract_changelog_between_single_version(self):
        sample_changelog = """## [0.5.0] - 2026-11-18
### Added
- Feature 5

## [0.4.0] - 2026-11-18
### Added
- Feature 4
"""
        res = updater.extract_changelog_between(sample_changelog, "0.4.0", "0.5.0")
        self.assertIn("## [0.5.0]", res)
        self.assertIn("Feature 5", res)
        self.assertNotIn("## [0.4.0]", res)
        self.assertNotIn("Feature 4", res)

    def test_fetch_multi_version_changelog_fallback_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Network down")):
            res = updater.fetch_multi_version_changelog("0.3.1", "0.5.0", fallback_body="Fallback release note")
            self.assertEqual(res, "Fallback release note")

    def test_fetch_multi_version_changelog_prefers_tag_url(self):
        sample = b"## [0.8.1] - 2026-11-19\n### Added\n- Feature 81\n\n## [0.8.0] - 2026-11-19\n### Added\n- Feature 80"
        mock_resp = MagicMock()
        mock_resp.read.return_value = sample
        mock_resp.__enter__.return_value = mock_resp

        requested_urls = []
        def fake_urlopen(req, *args, **kwargs):
            requested_urls.append(req.full_url)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = updater.fetch_multi_version_changelog("0.7.0", "0.8.1")
            self.assertTrue(requested_urls[0].endswith("/v0.8.1/CHANGELOG.md"))
            self.assertIn("## [0.8.1]", res)
            self.assertIn("## [0.8.0]", res)

    def test_fetch_multi_version_changelog_injects_fallback_if_target_missing(self):
        # Simulates stale changelog missing 0.8.1 but containing 0.8.0
        sample_stale = b"## [0.8.0] - 2026-11-19\n### Added\n- Feature 80"
        mock_resp = MagicMock()
        mock_resp.read.return_value = sample_stale
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = updater.fetch_multi_version_changelog("0.7.0", "0.8.1", fallback_body="Release note for 0.8.1")
            self.assertIn("## [0.8.1]", res)
            self.assertIn("Release note for 0.8.1", res)
            self.assertIn("## [0.8.0]", res)

    def test_render_markdown_changelog(self):
        sample = """## [0.5.0] - 2026-11-18
### Added
- **Feature A**: description of `code_snippet`
  - Sub-feature bullet
"""
        rendered = updater.render_markdown_changelog(sample, max_width=80)
        self.assertIn("[v0.5.0]", rendered)
        self.assertIn("[Added]", rendered)
        self.assertIn("Feature A", rendered)
        self.assertIn("code_snippet", rendered)
        self.assertNotIn("`code_snippet`", rendered)  # backtick should be stripped and styled
        self.assertNotIn("**Feature A**", rendered)   # raw ** should be stripped and styled
        self.assertIn("• ", rendered)                # bullet point converted to •


if __name__ == "__main__":
    unittest.main()
