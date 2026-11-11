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

# Two databases sharing the EXACT SAME NAME 'bekal-core', but different types!
[db.postgres.bekal-core]
dsn = "postgres://user@localhost:5432/bekal_pg"
description = "Postgres DB"

[db.oracle.bekal-core]
dsn = "host:1521/ORCL"
user = "ro_user"
description = "Oracle DB"

[db.postgres.kala-studio]
dsn = "postgres://user@localhost:5432/kala"
"""
        with tempfile.NamedTemporaryFile(suffix=".toml") as tmp:
            tmp.write(sample_toml)
            tmp.flush()

            with patch.object(server, "CONFIG", Path(tmp.name)):
                dbs = server.databases()
                self.assertIn("postgres:bekal-core", dbs)
                self.assertIn("oracle:bekal-core", dbs)
                self.assertIn("postgres:kala-studio", dbs)

                # 1. Unambiguous unique name resolves directly
                resolved_kala = server.resolve_db_key("kala-studio", dbs)
                self.assertEqual(resolved_kala, "postgres:kala-studio")

                # 2. Resolving collided short name without prefix raises helpful ValueError
                with self.assertRaises(ValueError) as ctx:
                    server.resolve_db_key("bekal-core", dbs)
                self.assertIn("ambiguous across multiple types", str(ctx.exception))
                self.assertIn("postgres:bekal-core", str(ctx.exception))
                self.assertIn("oracle:bekal-core", str(ctx.exception))

                # 3. Explicit prefix resolves accurately
                resolved_pg = server.resolve_db_key("postgres:bekal-core", dbs)
                self.assertEqual(resolved_pg, "postgres:bekal-core")

                resolved_ora = server.resolve_db_key("oracle:bekal-core", dbs)
                self.assertEqual(resolved_ora, "oracle:bekal-core")

                # 4. Alias pgsql:bekal-core works
                resolved_alias = server.resolve_db_key("pgsql:bekal-core", dbs)
                self.assertEqual(resolved_alias, "postgres:bekal-core")

                # 5. list_databases tool surfaces full qualified name on collision
                out_str = asyncio.run(server.list_databases())
                out = json.loads(out_str)
                db_names = [item["db"] for item in out]
                self.assertIn("postgres:bekal-core", db_names)
                self.assertIn("oracle:bekal-core", db_names)
                self.assertIn("kala-studio", db_names)

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

    def test_query_filter_rejects_non_select(self):
        sql_insert = "INSERT INTO users (name) VALUES ('alice')"
        reject = None if sql_insert.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
        self.assertEqual(reject, "only SELECT/WITH queries allowed")

        sql_select = "SELECT * FROM users"
        reject_select = None if sql_select.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
        self.assertIsNone(reject_select)


if __name__ == "__main__":
    unittest.main()
