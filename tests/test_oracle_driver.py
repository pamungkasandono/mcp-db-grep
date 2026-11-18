import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import oracle_driver


class TestOracleDriver(unittest.TestCase):
    def test_get_instantclient_base_dir_default(self):
        with patch.dict(os.environ, {}, clear=True):
            p = oracle_driver.get_instantclient_base_dir()
            self.assertTrue(str(p).endswith(".config/db-grep/instantclient") or "instantclient" in str(p))

    def test_get_instantclient_base_dir_custom_config(self):
        with patch.dict(os.environ, {"DB_GREP_CONFIG": "/tmp/custom/config.toml"}):
            p = oracle_driver.get_instantclient_base_dir()
            self.assertEqual(p, Path("/tmp/custom/instantclient"))

    def test_get_download_info(self):
        with patch("sys.platform", "linux"), patch("platform.machine", return_value="x86_64"):
            url, name = oracle_driver.get_download_info()
            self.assertIn("linuxx64.zip", url)
            self.assertEqual(name, "instantclient-linux-x64.zip")

        with patch("sys.platform", "linux"), patch("platform.machine", return_value="aarch64"):
            url, name = oracle_driver.get_download_info()
            self.assertIn("linux-arm64.zip", url)
            self.assertEqual(name, "instantclient-linux-arm64.zip")

        with patch("sys.platform", "win32"), patch("platform.machine", return_value="AMD64"):
            url, name = oracle_driver.get_download_info()
            self.assertIn("windows.zip", url)
            self.assertEqual(name, "instantclient-windows.zip")

        with patch("sys.platform", "darwin"), patch("platform.machine", return_value="arm64"):
            url, name = oracle_driver.get_download_info()
            self.assertIn("macos-arm64.dmg", url)
            self.assertEqual(name, "instantclient-macos-arm64.dmg")

        with patch("sys.platform", "darwin"), patch("platform.machine", return_value="x86_64"):
            url, name = oracle_driver.get_download_info()
            self.assertIn("macos.zip", url)
            self.assertEqual(name, "instantclient-macos-x64.zip")

    def test_find_oracle_client_dir_not_found(self):
        with patch("oracle_driver.get_instantclient_base_dir", return_value=Path("/tmp/nonexistent_test_dir_12345")):
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(oracle_driver.find_oracle_client_dir())
                self.assertFalse(oracle_driver.is_driver_installed())

    def test_find_oracle_client_dir_found(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sub_dir = tmp_path / "instantclient_23_4"
            sub_dir.mkdir(parents=True)
            dummy_lib = sub_dir / "libclntsh.so"
            dummy_lib.touch()

            with patch("oracle_driver.get_instantclient_base_dir", return_value=tmp_path):
                found = oracle_driver.find_oracle_client_dir()
                self.assertEqual(found, sub_dir)
                self.assertTrue(oracle_driver.is_driver_installed())


if __name__ == "__main__":
    unittest.main()
