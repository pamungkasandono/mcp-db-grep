import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import cli
import config_manager
import driver_manager
import oracle_driver
import server


class TestDriverDeepIntegration(unittest.TestCase):
    def test_cli_dpy3015_interactive_prompt_accepted(self):
        """Verify that when DPY-3015 occurs, user answering 'y' triggers driver installation and retries."""
        fake_initial_results = [
            {
                "db": "oracle:legacy-hr",
                "type": "oracle",
                "status": "failed",
                "error": "DPY-3015: password verifier type 0x939 is not supported by python-oracledb in thin mode",
                "query": "SELECT 1 FROM DUAL",
            }
        ]
        fake_retry_results = [
            {
                "db": "oracle:legacy-hr",
                "type": "oracle",
                "status": "connected",
                "latency_ms": 12,
                "query": "SELECT 1 FROM DUAL",
            }
        ]

        with patch("server.databases", return_value={"oracle:legacy-hr": {"type": "oracle"}}), \
             patch("server.execute_connection_check", side_effect=[fake_initial_results, fake_retry_results]) as mock_exec, \
             patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="y"), \
             patch("driver_manager.is_driver_ready", return_value=False), \
             patch("driver_manager.install_driver", return_value=True) as mock_install, \
             patch("sys.exit") as mock_exit:

            cli.run_check_connection_cli("oracle:legacy-hr")

            # Check that install was called
            mock_install.assert_called_once_with("oracle")
            # Check that connection check was retried
            self.assertEqual(mock_exec.call_count, 2)
            mock_exit.assert_called_with(0)

    def test_cli_dpy3015_interactive_prompt_declined(self):
        """Verify that when user declines driver download, it exits with failure without installing."""
        fake_initial_results = [
            {
                "db": "oracle:legacy-hr",
                "type": "oracle",
                "status": "failed",
                "error": "DPY-3015: password verifier type 0x939 is not supported by python-oracledb in thin mode",
                "query": "SELECT 1 FROM DUAL",
            }
        ]

        with patch("server.databases", return_value={"oracle:legacy-hr": {"type": "oracle"}}), \
             patch("server.execute_connection_check", return_value=fake_initial_results) as mock_exec, \
             patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"), \
             patch("driver_manager.is_driver_ready", return_value=False), \
             patch("driver_manager.install_driver") as mock_install, \
             patch("sys.exit") as mock_exit:

            cli.run_check_connection_cli("oracle:legacy-hr")

            mock_install.assert_not_called()
            self.assertEqual(mock_exec.call_count, 1)
            mock_exit.assert_called_with(1)

    def test_server_oracle_connect_wraps_dpy3015(self):
        """Verify that server.Oracle.connect transforms DPY-3015 into an informative message."""
        import oracledb

        oracle_db = server.Oracle("oracle:test_db", {"type": "oracle", "dsn": "host:1521/ORCL", "user": "test", "password": "pw"})

        with patch("oracle_driver.ensure_thick_mode"), \
             patch("oracledb.connect", side_effect=oracledb.NotSupportedError("DPY-3015: password verifier type 0x939 is not supported")):
            with self.assertRaises(oracledb.DatabaseError) as ctx:
                oracle_db.connect()

            self.assertIn("DPY-3015", str(ctx.exception))
            self.assertIn("db-grep --setup-driver oracle", str(ctx.exception))

    def test_server_oracle_connect_non_dpy3015_passthrough(self):
        """Verify that other Oracle errors (e.g. ORA-01017) are re-raised without tip."""
        import oracledb

        oracle_db = server.Oracle("oracle:test_db", {"type": "oracle", "dsn": "host:1521/ORCL", "user": "test", "password": "pw"})

        with patch("oracle_driver.ensure_thick_mode"), \
             patch("oracledb.connect", side_effect=oracledb.DatabaseError("ORA-01017: invalid username/password")):
            with self.assertRaises(oracledb.DatabaseError) as ctx:
                oracle_db.connect()

            self.assertIn("ORA-01017", str(ctx.exception))
            self.assertNotIn("db-grep --setup-driver", str(ctx.exception))

    def test_zip_slip_security_mitigation(self):
        """Verify that archives with path traversal elements (Zip Slip) do not escape destination directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest_dir = Path(tmp_dir) / "client"
            dest_dir.mkdir()

            # Create a zip with a malicious entry
            zip_path = Path(tmp_dir) / "evil.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("legit.so", "valid content")
                z.writestr("../../evil.txt", "malicious content")

            # Extract using the hardened extraction logic
            resolved_dest = dest_dir.resolve()
            with zipfile.ZipFile(zip_path, "r") as z:
                for member in z.infolist():
                    target_path = (dest_dir / member.filename).resolve()
                    if not str(target_path).startswith(str(resolved_dest)):
                        continue  # Zip slip prevented!
                    z.extract(member, dest_dir)

            self.assertTrue((dest_dir / "legit.so").exists())
            self.assertFalse((Path(tmp_dir) / "evil.txt").exists())

    def test_symlink_traversal_security_mitigation(self):
        """Verify that _fix_extracted_symlinks ignores traversal targets."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            client_dir = Path(tmp_dir)

            # Create legitimate symlink placeholder
            legit = client_dir / "libclntsh.so"
            legit_target = client_dir / "libclntsh.so.23.1"
            legit_target.touch()
            legit.write_text("libclntsh.so.23.1")

            # Create malicious traversal placeholders
            evil1 = client_dir / "evil_rel.so"
            evil1.write_text("../../etc/passwd")

            evil2 = client_dir / "evil_abs.so"
            evil2.write_text("/etc/shadow")

            oracle_driver._fix_extracted_symlinks(client_dir)

            # Legit should become a symlink
            self.assertTrue(legit.is_symlink())
            self.assertEqual(os.readlink(legit), "libclntsh.so.23.1")

            # Malicious should NOT become symlinks
            self.assertFalse(evil1.is_symlink())
            self.assertFalse(evil2.is_symlink())

    def test_config_manager_token_normalization(self):
        """Verify that DRIVER_ALIASES imported from driver_manager correctly normalizes db tokens."""
        self.assertEqual(config_manager.normalize_db_token("ora:prod-db"), "oracle:prod-db")
        self.assertEqual(config_manager.normalize_db_token("pgsql:billing"), "postgres:billing")
        self.assertEqual(config_manager.normalize_db_token("my:inventory"), "mysql:inventory")
        self.assertEqual(config_manager.normalize_db_token("postgres:direct"), "postgres:direct")

    def test_cli_subprocess_setup_driver_status(self):
        """Test full execution of db-grep --setup-driver via CLI."""
        res = subprocess.run(
            [sys.executable, "cli.py", "--setup-driver"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Database Driver Status", res.stdout)
        self.assertIn("postgres", res.stdout)
        self.assertIn("mysql", res.stdout)
        self.assertIn("oracle", res.stdout)

    def test_cli_subprocess_setup_driver_invalid(self):
        """Test that db-grep --setup-driver invalid_driver returns code 1."""
        res = subprocess.run(
            [sys.executable, "cli.py", "--setup-driver", "invalid_driver_name"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("Error: Unknown driver", res.stdout)


if __name__ == "__main__":
    unittest.main()
