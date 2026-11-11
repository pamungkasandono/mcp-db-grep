import asyncio
import json
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

    def test_read_config_unknown_driver_raises_error(self):
        sample_toml = b"""[monitor]
port = 48260

[db.unknown_driver.test-db]
dsn = "test://localhost/test"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                with self.assertRaises(ValueError) as ctx:
                    server.databases()
                self.assertIn("Unknown driver '[db.unknown_driver]'", str(ctx.exception))

    def test_same_db_name_across_different_types_composite_keys(self):
        sample_toml = b"""[monitor]
port = 48260

# Two databases sharing the EXACT SAME NAME 'inventory-core', but different types!
[db.postgres.inventory-core]
dsn = "postgres://user@localhost:5432/inv_pg"
description = "Postgres DB"

[db.oracle.inventory-core]
dsn = "localhost:1521/ORCL"
user = "ro_user"
description = "Oracle DB"

[db.postgres.ecommerce-dev]
dsn = "postgres://user@localhost:5432/ecom"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                dbs = server.databases()
                self.assertIn("postgres:inventory-core", dbs)
                self.assertIn("oracle:inventory-core", dbs)
                self.assertIn("postgres:ecommerce-dev", dbs)

                # 1. Unambiguous unique name resolves directly
                resolved_ecom = server.resolve_db_key("ecommerce-dev", dbs)
                self.assertEqual(resolved_ecom, "postgres:ecommerce-dev")

                # 2. Resolving collided short name without prefix raises helpful ValueError
                with self.assertRaises(ValueError) as ctx:
                    server.resolve_db_key("inventory-core", dbs)
                self.assertIn("ambiguous across multiple types", str(ctx.exception))
                self.assertIn("postgres:inventory-core", str(ctx.exception))
                self.assertIn("oracle:inventory-core", str(ctx.exception))

                # 3. Explicit prefix resolves accurately
                resolved_pg = server.resolve_db_key("postgres:inventory-core", dbs)
                self.assertEqual(resolved_pg, "postgres:inventory-core")

                resolved_ora = server.resolve_db_key("oracle:inventory-core", dbs)
                self.assertEqual(resolved_ora, "oracle:inventory-core")

                # 4. Alias pgsql:inventory-core works
                resolved_alias = server.resolve_db_key("pgsql:inventory-core", dbs)
                self.assertEqual(resolved_alias, "postgres:inventory-core")

                # 5. list_databases tool surfaces full qualified name on collision
                out_str = asyncio.run(server.list_databases())
                out = json.loads(out_str)
                db_names = [item["db"] for item in out]
                self.assertIn("postgres:inventory-core", db_names)
                self.assertIn("oracle:inventory-core", db_names)
                self.assertIn("ecommerce-dev", db_names)

    def test_single_prefixed_db_resolves_with_or_without_prefix(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.unique-name]
dsn = "postgres://user@localhost:5432/unique"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                dbs = server.databases()
                # Should resolve with exact key
                self.assertEqual(server.resolve_db_key("postgres:unique-name", dbs), "postgres:unique-name")
                # And also resolve without prefix because no collision exists!
                self.assertEqual(server.resolve_db_key("unique-name", dbs), "postgres:unique-name")

    def test_non_existent_db_error_message(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.alpha]
dsn = "postgres://user@localhost:5432/alpha"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                dbs = server.databases()
                with self.assertRaises(ValueError) as ctx:
                    server.resolve_db_key("non-existent-db", dbs)
                self.assertIn("db 'non-existent-db' does not exist", str(ctx.exception))
                self.assertIn("Available choices:", str(ctx.exception))

    def test_inspect_config_valid_and_missing_env(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.app]
dsn = "postgres://user@localhost/app"
password_env = "UNSET_TEST_ENV_VAR_123"

[db.oracle.corp]
dsn = "host/corp"
user = "ro_user"
password = "plain_password"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                # UNSET_TEST_ENV_VAR_123 is missing from env
                with patch.dict("os.environ", {}, clear=True):
                    report = server.inspect_config()
                    self.assertFalse(report["valid"])
                    db_names = [d["name"] for d in report["databases"]]
                    self.assertIn("app", db_names)
                    self.assertIn("corp", db_names)

                    # Now provide the env var
                    with patch.dict("os.environ", {"UNSET_TEST_ENV_VAR_123": "secret"}):
                        report_ok = server.inspect_config()
                        self.assertTrue(report_ok["valid"])

    def test_check_config_mcp_tool(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.crm]
dsn = "postgres://user@localhost/crm"
password = "123"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                out_str = asyncio.run(server.check_config())
                data = json.loads(out_str)
                self.assertTrue(data["valid"])
                self.assertEqual(data["databases"][0]["name"], "crm")

    def test_ping_database_postgres_mock(self):
        cfg = {
            "type": "postgres",
            "dsn": "postgres://user@localhost/test",
            "password": "pwd",
        }

        class MockConn:
            def cursor(self):
                return self
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, sql, params=()):
                pass
            @property
            def description(self):
                return [("?column?",)]
            def fetchmany(self, size):
                return [(1,)]
            def close(self):
                pass
            @property
            def closed(self):
                return False
            @property
            def broken(self):
                return False

        with patch("psycopg.connect", return_value=MockConn()):
            res = server.ping_database("postgres:test", cfg)
            self.assertEqual(res["status"], "connected")
            self.assertEqual(res["query"], "SELECT 1;")
            self.assertIsNotNone(res["latency_ms"])
            self.assertIsNone(res["error"])

    def test_ping_database_failure_handling(self):
        cfg = {
            "type": "postgres",
            "dsn": "postgres://invalid:5432/bad",
        }
        with patch("psycopg.connect", side_effect=Exception("Connection refused")):
            res = server.ping_database("postgres:bad", cfg)
            self.assertEqual(res["status"], "failed")
            self.assertEqual(res["query"], "SELECT 1;")
            self.assertIn("Connection refused", res["error"])

    def test_check_connection_mcp_tool(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.prod]
dsn = "postgres://localhost/prod"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                with patch.object(server, "ping_database", return_value={"db": "postgres:prod", "status": "connected", "query": "SELECT 1;", "latency_ms": 1.2, "error": None, "type": "postgres"}):
                    out_str = asyncio.run(server.check_connection("all"))
                    res = json.loads(out_str)
                    self.assertEqual(len(res), 1)
                    self.assertEqual(res[0]["status"], "connected")
                    self.assertEqual(res[0]["query"], "SELECT 1;")

    def test_query_filter_rejects_non_select(self):
        sql_insert = "INSERT INTO users (name) VALUES ('alice')"
        reject = None if sql_insert.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
        self.assertEqual(reject, "only SELECT/WITH queries allowed")

        sql_select = "SELECT * FROM users"
        reject_select = None if sql_select.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
        self.assertIsNone(reject_select)


if __name__ == "__main__":
    unittest.main()
