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


if __name__ == "__main__":
    unittest.main()
