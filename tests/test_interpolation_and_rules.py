import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class TestInterpolationAndRules(unittest.TestCase):
    def test_interpolation_env_and_urlenc(self):
        with patch.dict(os.environ, {"SECRET_PW": "p@ss/word!#1"}):
            resolved = server.resolve("postgres://user:${env:SECRET_PW|urlenc}@localhost/db")
            self.assertEqual(resolved, "postgres://user:p%40ss%2Fword%21%231@localhost/db")

    def test_interpolation_shorthand_env_and_raw(self):
        with patch.dict(os.environ, {"SECRET_PW": "supersecret"}):
            self.assertEqual(server.resolve("env:SECRET_PW", shorthand=True), "supersecret")
            self.assertEqual(server.resolve("raw:env:literal", shorthand=True), "env:literal")

    def test_interpolation_escaped_dollar(self):
        self.assertEqual(server.resolve("price is $$50"), "price is $50")

    def test_interpolation_file(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tmp:
            tmp.write("file_secret_content\n")
            tmp_path = Path(tmp.name)

        try:
            if os.name == "posix":
                tmp_path.chmod(0o600)
            resolved = server.resolve(f"${{file:{tmp_path}}}")
            self.assertEqual(resolved, "file_secret_content")
            # Also shorthand file:
            resolved_short = server.resolve(f"file:{tmp_path}", shorthand=True)
            self.assertEqual(resolved_short, "file_secret_content")
            # Inline DSN with file and urlenc
            resolved_inline = server.resolve(f"postgres://ro_user:${{file:{tmp_path}|urlenc}}@localhost/app")
            self.assertEqual(resolved_inline, "postgres://ro_user:file_secret_content@localhost/app")
            # Oracle inline DSN with env
            with patch.dict(os.environ, {"ORA_PW": "oracle_secret_pw"}):
                resolved_ora = server.resolve("ro_user/${env:ORA_PW}@localhost:1521/PROD")
                self.assertEqual(resolved_ora, "ro_user/oracle_secret_pw@localhost:1521/PROD")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_raw_secret_detection_and_chmod_600(self):
        sample_raw = b"""[monitor]
port = 48260

[db.postgres.raw-db]
dsn = "postgres://user:rawpass@localhost/db"
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample_raw)
            tmp_path = Path(tmp.name)

        try:
            if os.name == "posix":
                tmp_path.chmod(0o644)
                with patch.object(server, "CONFIG", tmp_path):
                    with self.assertRaises(ValueError) as ctx:
                        server.read_config()
                    self.assertIn("chmod 600", str(ctx.exception))

                # After fixing to 600, it should succeed
                tmp_path.chmod(0o600)
                with patch.object(server, "CONFIG", tmp_path):
                    cfg = server.read_config()
                    self.assertIn("db", cfg)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_env_only_does_not_force_chmod_600(self):
        sample_env = b"""[monitor]
port = 48260

[db.postgres.env-db]
dsn = "postgres://localhost/db"
password = "env:APP_PW"
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample_env)
            tmp_path = Path(tmp.name)

        try:
            if os.name == "posix":
                tmp_path.chmod(0o644)
                with patch.object(server, "CONFIG", tmp_path):
                    # Should NOT raise ValueError because it's only an env reference!
                    cfg = server.read_config()
                    self.assertIn("db", cfg)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_defaults_inheritance_and_auto_tables_isolation(self):
        sample_toml = b"""[defaults]
auto_metadata = false
max_rows = 50
timeout_ms = 5000

[db.postgres.app]
dsn = "postgres://localhost/app"
auto_tables = ["mst_*", "ref_*"]

[db.postgres.sens]
dsn = "postgres://localhost/sens"
# auto_tables not set
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample_toml)
            tmp_path = Path(tmp.name)

        try:
            if os.name == "posix":
                tmp_path.chmod(0o600)
            with patch.object(server, "CONFIG", tmp_path):
                dbs = server.databases()
                app = dbs["postgres:app"]
                sens = dbs["postgres:sens"]

                # Inherited from [defaults]
                self.assertFalse(app["auto_metadata"])
                self.assertEqual(app["max_rows"], 50)
                self.assertEqual(app["timeout_ms"], 5000)
                self.assertEqual(app["auto_tables"], ["mst_*", "ref_*"])

                # sens inherits defaults, but auto_tables is NEVER inherited
                self.assertFalse(sens["auto_metadata"])
                self.assertEqual(sens["max_rows"], 50)
                self.assertEqual(sens["auto_tables"], [])
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_unknown_key_fails_fast_per_db(self):
        sample_toml = b"""[db.postgres.good]
dsn = "postgres://localhost/good"

[db.postgres.bad]
dsn = "postgres://localhost/bad"
typo_field = "oops"
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample_toml)
            tmp_path = Path(tmp.name)

        try:
            if os.name == "posix":
                tmp_path.chmod(0o600)
            with patch.object(server, "CONFIG", tmp_path):
                dbs = server.databases()
                self.assertIn("postgres:good", dbs)
                self.assertNotIn("postgres:bad", dbs)
                self.assertIn("postgres:bad", server.DISABLED_DATABASES)
                self.assertIn("Unknown configuration key", server.DISABLED_DATABASES["postgres:bad"])
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_sqlglot_table_extraction(self):
        sql = "SELECT u.name, r.role FROM mst_users u JOIN ref_roles r ON u.role_id = r.id WHERE u.active = true"
        tables = server.extract_tables(sql)
        self.assertEqual(tables, {"mst_users", "ref_roles"})

        # CTE should be excluded from tables
        sql_cte = "WITH temp_cte AS (SELECT id FROM app_cache) SELECT * FROM temp_cte JOIN mst_data ON temp_cte.id = mst_data.id"
        tables_cte = server.extract_tables(sql_cte)
        self.assertEqual(tables_cte, {"app_cache", "mst_data"})

    def test_auto_tables_pattern_matching(self):
        patterns = ["mst_*", "ref_*", "config"]
        self.assertTrue(server.matches_auto_tables("mst_users", patterns))
        self.assertTrue(server.matches_auto_tables("REF_LOOKUP", patterns))
        self.assertTrue(server.matches_auto_tables("config", patterns))
        self.assertFalse(server.matches_auto_tables("salary_records", patterns))
        self.assertFalse(server.matches_auto_tables("users", patterns))

    def test_secret_redaction_in_db_errors(self):
        cfg = {
            "type": "postgres",
            "dsn": "postgres://user:${env:SECRET_KEY|urlenc}@db.internal:5432/app",
            "password": "env:SECRET_KEY",
        }
        with patch.dict(os.environ, {"SECRET_KEY": "SuperSecretPass123"}):
            db = server.Postgres("postgres:app", cfg)
            raw_dsn = db.get_dsn()
            self.assertIn("SuperSecretPass123", raw_dsn)
            self.assertIn("SuperSecretPass123", db.secrets)

            # Error message containing the secret
            err = f"connection failed: FATAL password authentication failed for user with pass SuperSecretPass123 at db.internal"
            redacted = db.redact(err)
            self.assertNotIn("SuperSecretPass123", redacted)
            self.assertIn("***", redacted)


if __name__ == "__main__":
    unittest.main()
