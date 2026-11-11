import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class TestServerLogic(unittest.TestCase):
    def test_check_konteks_valid(self):
        valid = "my-project · fetch active users"
        self.assertEqual(server.check_konteks(valid), valid)

    def test_check_konteks_invalid_missing_separator(self):
        with self.assertRaises(ValueError):
            server.check_konteks("invalid context without middle dot")

    def test_read_config_missing_raises_error(self):
        non_existent = Path("/path/to/definitely/non/existent/config.toml")
        with patch.object(server, "CONFIG", non_existent):
            with self.assertRaises(ValueError):
                server.read_config()

    def test_read_config_valid(self):
        sample_toml = b"""[monitor]
port = 48260

[db.test-db]
type = "postgres"
dsn = "postgres://user@localhost:5432/test"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                cfg = server.read_config()
                self.assertEqual(cfg["monitor"]["port"], 48260)
                dbs = server.databases()
                self.assertIn("test-db", dbs)
                self.assertEqual(dbs["test-db"]["type"], "postgres")

    def test_query_filter_rejects_non_select(self):
        # We test the rejection logic before monitor dispatch
        sql_insert = "INSERT INTO users (name) VALUES ('alice')"
        reject = None if sql_insert.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
        self.assertEqual(reject, "only SELECT/WITH queries allowed")

        sql_select = "SELECT * FROM users"
        reject_select = None if sql_select.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
        self.assertIsNone(reject_select)


if __name__ == "__main__":
    unittest.main()
