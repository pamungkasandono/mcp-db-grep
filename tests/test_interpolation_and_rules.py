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
        with patch.dict(os.environ, {"SECRET_PW": "p@ss/word!#1", "DB_HOST": "db.internal"}):
            # Explicit |urlenc still works
            resolved = server.resolve("postgres://user:${env:SECRET_PW|urlenc}@localhost/db")
            self.assertEqual(resolved, "postgres://user:p%40ss%2Fword%21%231@localhost/db")

            # Without |urlenc: automatically URL-encoded when inside URI userinfo!
            resolved_auto = server.resolve("postgres://user:${env:SECRET_PW}@${env:DB_HOST}:5432/db")
            self.assertEqual(resolved_auto, "postgres://user:p%40ss%2Fword%21%231@db.internal:5432/db")

            resolved_mysql_auto = server.resolve("mysql://ro_user:${env:SECRET_PW}@${env:DB_HOST}:3306/orders_db")
            self.assertEqual(resolved_mysql_auto, "mysql://ro_user:p%40ss%2Fword%21%231@db.internal:3306/orders_db")

    def test_auto_normalize_full_dsn_with_special_chars_in_password(self):
        # Full DSN in env containing raw '@', ':', '/', '#' inside the password
        raw_full = "mysql://ro_user:p@ss:w/rd#1@db.internal:3306/orders_db"
        with patch.dict(os.environ, {"ORDERS_FULL_DSN": raw_full}):
            cfg = {"type": "mysql", "dsn": "env:ORDERS_FULL_DSN"}
            db = server.MySQL("mysql:orders", cfg)
            dsn = db.get_dsn()
            self.assertEqual(dsn, "mysql://ro_user:p%40ss%3Aw%2Frd%231@db.internal:3306/orders_db")
            self.assertEqual(db.default_schema, "orders_db")
            # Both raw and URL-encoded password forms must be redacted
            self.assertIn("p@ss:w/rd#1", db.secrets)
            self.assertIn("p%40ss%3Aw%2Frd%231", db.secrets)
            self.assertNotIn("p@ss:w/rd#1", db.redact("Failed login for p@ss:w/rd#1"))
            self.assertNotIn("p%40ss%3Aw%2Frd%231", db.redact("URI mysql://ro_user:p%40ss%3Aw%2Frd%231@db.internal"))

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

    def test_full_dsn_via_shorthand_env_and_file(self):
        sample_toml = b"""[db.postgres.pg_env]
dsn = "env:PG_FULL_DSN"

[db.oracle.ora_env]
dsn = "env:ORA_FULL_DSN"
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample_toml)
            tmp_path = Path(tmp.name)

        try:
            env_vars = {
                "PG_FULL_DSN": "postgres://ro_user:PgInlineSecret99@db.example.com:5432/prod",
                "ORA_FULL_DSN": "hr_ro/OraInlineSecret88@ora.example.com:1521/ORCL",
            }
            with patch.dict(os.environ, env_vars):
                with patch.object(server, "CONFIG", tmp_path):
                    dbs = server.databases()
                    self.assertIn("postgres:pg_env", dbs)
                    self.assertIn("oracle:ora_env", dbs)

                    pg_db = server.Postgres("postgres:pg_env", dbs["postgres:pg_env"])
                    self.assertEqual(pg_db.get_dsn(), env_vars["PG_FULL_DSN"])
                    self.assertIn("PgInlineSecret99", pg_db.secrets)

                    ora_db = server.Oracle("oracle:ora_env", dbs["oracle:ora_env"])
                    self.assertEqual(ora_db.get_dsn(), env_vars["ORA_FULL_DSN"])
                    self.assertIn("OraInlineSecret88", ora_db.secrets)

                    report = server.inspect_config()
                    self.assertTrue(report["valid"])
                    self.assertFalse(report["has_raw_secrets"])
                    for d in report["databases"]:
                        self.assertEqual(d["auth_source"], "env")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_partial_dsn_env_user_host_port_and_raw_secret_detection(self):
        # Username, host, port from env WITHOUT password in DSN -> must NOT be flagged as raw secret
        cfg_safe = {
            "type": "postgres",
            "dsn": "postgres://${env:DB_USER}@${env:DB_HOST}:${env:DB_PORT}/mydb",
        }
        self.assertFalse(server.has_raw_secret(cfg_safe))

        # Both user and password from env -> must NOT be flagged as raw secret
        cfg_both_env = {
            "type": "postgres",
            "dsn": "postgres://${env:DB_USER}:${env:DB_PW|urlenc}@${env:DB_HOST}:${env:DB_PORT}/mydb",
        }
        self.assertFalse(server.has_raw_secret(cfg_both_env))

        # User from env, but password is raw plaintext -> MUST be flagged as raw secret
        cfg_raw_pw = {
            "type": "postgres",
            "dsn": "postgres://${env:DB_USER}:PlainSecret123@${env:DB_HOST}:5432/mydb",
        }
        self.assertTrue(server.has_raw_secret(cfg_raw_pw))

    def test_pre_driver_dsn_and_interpolation_syntax_validation(self):
        # 1. Malformed / unclosed interpolation syntax
        with self.assertRaises(ValueError):
            server.resolve("postgres://${env:UNCLOSED@localhost/db")
        with self.assertRaises(ValueError):
            server.resolve("postgres://${invalid_scheme:VAR}@localhost/db")
        with self.assertRaises(ValueError):
            server.resolve("env:", shorthand=True)

        # 2. Invalid PostgreSQL DSN syntax rejected before hitting driver
        for bad_pg_dsn in (
            "not-a-valid-dsn",
            "mysql://localhost:3306/db",
            "postgres://",
            "postgres:///mydb",
            "postgres://localhost:notaport/mydb",
            "postgres://localhost:99999/mydb",
        ):
            with self.assertRaises(ValueError, msg=f"Expected ValueError for {bad_pg_dsn}"):
                server.validate_dsn_syntax("postgres", bad_pg_dsn)
            db = server.Postgres("postgres:bad", {"type": "postgres", "dsn": bad_pg_dsn})
            with self.assertRaises(ValueError):
                db.get_dsn()

        # 3. Invalid Oracle DSN syntax rejected before hitting driver
        for bad_ora_dsn in (
            "postgres://localhost:5432/orcl",
            "ro_user/secret@",
            "localhost:notaport/ORCL",
            "localhost:1521/",
            "user@host1@host2/ORCL",
        ):
            with self.assertRaises(ValueError, msg=f"Expected ValueError for {bad_ora_dsn}"):
                server.validate_dsn_syntax("oracle", bad_ora_dsn)
            db = server.Oracle("oracle:bad", {"type": "oracle", "dsn": bad_ora_dsn, "user": "ro"})
            with self.assertRaises(ValueError):
                db.get_dsn()


if __name__ == "__main__":
    unittest.main()
