import io
import unittest
from unittest.mock import MagicMock, patch

import driver_manager


class TestDriverManager(unittest.TestCase):
    def test_normalize_driver_name(self):
        self.assertEqual(driver_manager.normalize_driver_name("pgsql"), "postgres")
        self.assertEqual(driver_manager.normalize_driver_name("POSTGRES"), "postgres")
        self.assertEqual(driver_manager.normalize_driver_name("my"), "mysql")
        self.assertEqual(driver_manager.normalize_driver_name("ora"), "oracle")
        self.assertEqual(driver_manager.normalize_driver_name("unknown_db"), "unknown_db")

    def test_get_driver_status_builtin(self):
        pg = driver_manager.get_driver_status("postgres")
        self.assertTrue(pg["ready"])
        self.assertFalse(pg["needs_external"])
        self.assertIn("no external driver needed", pg["details"])

        my = driver_manager.get_driver_status("mysql")
        self.assertTrue(my["ready"])
        self.assertFalse(my["needs_external"])

    def test_get_driver_status_oracle(self):
        with patch("oracle_driver.is_driver_installed", return_value=False), \
             patch("oracle_driver.find_oracle_client_dir", return_value=None):
            ora = driver_manager.get_driver_status("oracle")
            self.assertTrue(ora["needs_external"])
            self.assertFalse(ora["installed"])

        with patch("oracle_driver.is_driver_installed", return_value=True), \
             patch("oracle_driver.find_oracle_client_dir", return_value="/tmp/oracle/client"):
            ora_installed = driver_manager.get_driver_status("oracle")
            self.assertTrue(ora_installed["installed"])
            self.assertEqual(ora_installed["path"], "/tmp/oracle/client")

    def test_is_driver_ready(self):
        self.assertTrue(driver_manager.is_driver_ready("postgres"))
        self.assertTrue(driver_manager.is_driver_ready("mysql"))
        with patch("oracle_driver.is_driver_installed", return_value=True):
            self.assertTrue(driver_manager.is_driver_ready("oracle"))
        with patch("oracle_driver.is_driver_installed", return_value=False):
            self.assertFalse(driver_manager.is_driver_ready("oracle"))

    def test_setup_driver_builtin_output(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            ok = driver_manager.setup_driver("postgres")
        self.assertTrue(ok)
        self.assertIn("No external driver needed", out.getvalue())

    def test_setup_driver_status_all(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            ok = driver_manager.setup_driver(None)
        self.assertTrue(ok)
        val = out.getvalue()
        self.assertIn("postgres", val)
        self.assertIn("mysql", val)
        self.assertIn("oracle", val)

    def test_setup_driver_unknown(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            ok = driver_manager.setup_driver("unknown_driver_xyz")
        self.assertFalse(ok)
        self.assertIn("Unknown driver", out.getvalue())


if __name__ == "__main__":
    unittest.main()
