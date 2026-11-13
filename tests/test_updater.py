import unittest
from unittest.mock import MagicMock, patch

import updater


class TestUpdater(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
