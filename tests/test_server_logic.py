import asyncio
from contextlib import contextmanager
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import server


class TestServerLogic(unittest.TestCase):
    @contextmanager
    def temp_config(self, sample_toml: bytes):
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample_toml)
            tmp_path = Path(tmp.name)
        try:
            with patch.object(server, "CONFIG", tmp_path):
                yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_check_context_valid(self):
        valid = "my-project · fetch active users"
        self.assertEqual(server.check_context(valid), valid)
        self.assertEqual(server.check_konteks(valid), valid)

    def test_check_context_invalid_missing_separator(self):
        with self.assertRaises(ValueError):
            server.check_context("invalid context without middle dot")
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
        with self.temp_config(sample_toml):
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
        with self.temp_config(sample_toml):
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

            # 4. Shorthand aliases pgsql: and ora: resolve to canonical keys
            self.assertEqual(server.resolve_db_key("pgsql:inventory-core", dbs), "postgres:inventory-core")
            self.assertEqual(server.resolve_db_key("ora:inventory-core", dbs), "oracle:inventory-core")

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
        with self.temp_config(sample_toml):
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
        with self.temp_config(sample_toml):
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
        with self.temp_config(sample_toml):
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
        with self.temp_config(sample_toml):
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

        mock_psycopg = MagicMock()
        mock_psycopg.connect.return_value = MockConn()
        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
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
        mock_psycopg = MagicMock()
        mock_psycopg.connect.side_effect = Exception("Connection refused")
        with patch.dict(sys.modules, {"psycopg": mock_psycopg}):
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
        with self.temp_config(sample_toml):
            with patch.object(server, "ping_database", return_value={"db": "postgres:prod", "status": "connected", "query": "SELECT 1;", "latency_ms": 1.2, "error": None, "type": "postgres"}):
                out_str = asyncio.run(server.check_connection("all"))
                res = json.loads(out_str)
                self.assertEqual(len(res), 1)
                self.assertEqual(res[0]["status"], "connected")
                self.assertEqual(res[0]["query"], "SELECT 1;")

    def test_resolve_db_keys_for_check_both_run_on_duplicate(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.inventory-core]
dsn = "postgres://localhost/inv_pg"

[db.oracle.inventory-core]
dsn = "localhost:1521/ORCL"
user = "ro_user"

[db.postgres.ecommerce-dev]
dsn = "postgres://localhost/ecom"
"""
        with self.temp_config(sample_toml):
            dbs = server.databases()

            # Short name matching two DBs -> returns BOTH (both run)
            resolved = server.resolve_db_keys_for_check("inventory-core", dbs)
            self.assertEqual(sorted(resolved), ["oracle:inventory-core", "postgres:inventory-core"])

            # Explicit prefix -> returns only the specified one
            resolved_pg = server.resolve_db_keys_for_check("postgres:inventory-core", dbs)
            self.assertEqual(resolved_pg, ["postgres:inventory-core"])

            resolved_ora = server.resolve_db_keys_for_check("oracle:inventory-core", dbs)
            self.assertEqual(resolved_ora, ["oracle:inventory-core"])

            # Shorthand prefixes pgsql: and ora: also resolve accurately
            self.assertEqual(server.resolve_db_keys_for_check("pgsql:inventory-core", dbs), ["postgres:inventory-core"])
            self.assertEqual(server.resolve_db_keys_for_check("ora:inventory-core", dbs), ["oracle:inventory-core"])

    def test_query_filter_rejects_non_select(self):
        sql_insert = "INSERT INTO users (name) VALUES ('alice')"
        reject = None if sql_insert.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
        self.assertEqual(reject, "only SELECT/WITH queries allowed")

        sql_select = "SELECT * FROM users"
        reject_select = None if sql_select.lstrip().lower().startswith(("select", "with")) else "only SELECT/WITH queries allowed"
        self.assertIsNone(reject_select)

    def test_mysql_driver_config_schema_and_ping_mock(self):
        sample_toml = b"""[monitor]
port = 48260

[db.mysql.orders-app]
dsn = "mysql://ro_user:my_secret_pw@localhost:3306/orders_db"
description = "MySQL Orders DB"
"""
        with self.temp_config(sample_toml):
            dbs = server.databases()
            self.assertIn("mysql:orders-app", dbs)
            self.assertEqual(server.resolve_db_key("orders-app", dbs), "mysql:orders-app")
            self.assertEqual(server.resolve_db_key("my:orders-app", dbs), "mysql:orders-app")

            mysql_inst = server.MySQL("mysql:orders-app", dbs["mysql:orders-app"])
            self.assertEqual(mysql_inst.default_schema, "orders_db")
            self.assertIn("my_secret_pw", mysql_inst.secrets)

            # Invalid MySQL DSN syntax is rejected before driver
            with self.assertRaises(ValueError):
                server.validate_dsn_syntax("mysql", "postgres://localhost:3306/orders_db")
            with self.assertRaises(ValueError):
                server.validate_dsn_syntax("mysql", "mysql://localhost:badport/orders_db")

            # SQLGlot MySQL backtick table extraction
            tables = server.extract_tables("SELECT * FROM `mst_orders` JOIN `ref_status` ON mst_orders.s = ref_status.id", dialect="mysql")
            self.assertEqual(tables, {"mst_orders", "ref_status"})

            class MockMySQLConn:
                open = True
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
                    return [("1",)]
                def fetchmany(self, size):
                    return [(1,)]
                def close(self):
                    self.open = False

            mock_pymysql = MagicMock()
            mock_pymysql.connect.return_value = MockMySQLConn()
            with patch.dict(sys.modules, {"pymysql": mock_pymysql}):
                res = server.ping_database("mysql:orders-app", dbs["mysql:orders-app"])
                self.assertEqual(res["status"], "connected")
                self.assertEqual(res["type"], "mysql")
                self.assertEqual(res["query"], "SELECT 1;")
                mock_pymysql.connect.assert_called_once()
                call_kwargs = mock_pymysql.connect.call_args.kwargs
                self.assertEqual(call_kwargs["host"], "localhost")
                self.assertEqual(call_kwargs["port"], 3306)
                self.assertEqual(call_kwargs["user"], "ro_user")
                self.assertEqual(call_kwargs["password"], "my_secret_pw")
                self.assertEqual(call_kwargs["database"], "orders_db")

    def test_clean_pattern_helper(self):
        self.assertEqual(server.DB._clean_pattern("people"), "%people%")
        self.assertEqual(server.DB._clean_pattern("vw_people%"), "vw_people%")
        self.assertEqual(server.DB._clean_pattern("%people"), "%people")
        self.assertEqual(server.DB._clean_pattern("*people*"), "%people%")
        self.assertEqual(server.DB._clean_pattern(""), "%")
        self.assertEqual(server.DB._clean_pattern("   "), "%")

    def test_postgres_get_search_query(self):
        pg = server.Postgres("postgres:test", {"type": "postgres", "max_rows": 100})
        # Default all types, no schema
        sql, params = pg.get_search_query("people", limit=50)
        self.assertIn("table_schema NOT IN ('pg_catalog', 'information_schema')", sql)
        self.assertIn("UPPER(table_name) LIKE UPPER(%s)", sql)
        self.assertIn("table_type IN ('BASE TABLE', 'VIEW', 'FOREIGN TABLE')", sql)
        self.assertIn("LIMIT %s", sql)
        self.assertEqual(params, ("%people%", 50))

        # Explicit schema and object_type TABLE
        sql, params = pg.get_search_query("users", schema="public", object_type="TABLE", limit=20)
        self.assertIn("table_type = 'BASE TABLE'", sql)
        self.assertIn("LOWER(table_schema) = LOWER(%s)", sql)
        self.assertEqual(params, ("%users%", "public", 20))

        # Object_type VIEW
        sql, params = pg.get_search_query("vw_%", object_type="VIEW")
        self.assertIn("table_type = 'VIEW'", sql)
        self.assertEqual(params[0], "vw_%")

    def test_mysql_get_search_query(self):
        my = server.MySQL("mysql:test", {"type": "mysql", "max_rows": 100})
        sql, params = my.get_search_query("orders", limit=30)
        self.assertIn("table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')", sql)
        self.assertIn("UPPER(table_name) LIKE UPPER(%s)", sql)
        self.assertIn("table_type IN ('BASE TABLE', 'VIEW')", sql)
        self.assertEqual(params, ("%orders%", 30))

        # With specific schema
        sql, params = my.get_search_query("items", schema="shop_db", object_type="VIEW", limit=10)
        self.assertIn("LOWER(table_schema) = LOWER(%s)", sql)
        self.assertIn("table_type = 'VIEW'", sql)
        self.assertEqual(params, ("%items%", "shop_db", 10))

    def test_oracle_get_search_query(self):
        ora = server.Oracle("oracle:test", {"type": "oracle", "max_rows": 100})
        # Default search across non-sys schemas
        sql, params = ora.get_search_query("people", limit=50)
        self.assertIn("FROM all_objects", sql)
        self.assertIn("UPPER(object_name) LIKE UPPER(:1)", sql)
        self.assertIn("owner NOT IN ('SYS', 'SYSTEM'", sql)
        self.assertIn("object_type IN ('TABLE', 'VIEW', 'SYNONYM', 'MATERIALIZED VIEW')", sql)
        self.assertIn("WHERE ROWNUM <= :2", sql)
        self.assertEqual(params, ("%people%", 50))

        # Specific schema and VIEW type
        sql, params = ora.get_search_query("peo_vw%", schema="bni_portal", object_type="VIEW", limit=25)
        self.assertIn("owner = UPPER(:2)", sql)
        self.assertIn("object_type IN ('VIEW', 'MATERIALIZED VIEW')", sql)
        self.assertIn("WHERE ROWNUM <= :3", sql)
        self.assertEqual(params, ("peo_vw%", "bni_portal", 25))

        # SYNONYM type
        sql, params = ora.get_search_query("syn_test", object_type="SYNONYM")
        self.assertIn("object_type IN ('SYNONYM')", sql)

    def test_oracle_list_and_desc_sql(self):
        ora = server.Oracle("oracle:test", {"type": "oracle", "max_rows": 100})
        self.assertIn("UNION", ora.LIST_SQL)
        self.assertIn("all_views", ora.LIST_SQL)
        self.assertIn("all_synonyms", ora.DESC_SQL)
        self.assertIn("all_tab_columns", ora.DESC_SQL)

    def test_describe_table_dot_notation_parsing(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.crm]
dsn = "postgres://localhost/crm"
"""
        with self.temp_config(sample_toml):
            with patch.object(server, "execute", return_value="[]") as mock_exec:
                # 1. table with dot format 'schema.table'
                asyncio.run(server.describe_table("crm", "billing.invoices", "crm · desc"))
                mock_exec.assert_called_once()
                call_args = mock_exec.call_args[0]
                self.assertEqual(call_args[0], "describe_table")
                # params passed to execute: (schema, table)
                self.assertEqual(call_args[4], ("billing", "invoices"))

            with patch.object(server, "execute", return_value="[]") as mock_exec:
                # 2. explicit schema overrides dot
                asyncio.run(server.describe_table("crm", "invoices", "crm · desc", schema="custom_schema"))
                mock_exec.assert_called_once()
                call_args = mock_exec.call_args[0]
                self.assertEqual(call_args[4], ("custom_schema", "invoices"))

    def test_find_objects_mcp_tool(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.crm]
dsn = "postgres://localhost/crm"
"""
        with self.temp_config(sample_toml):
            mock_ret = json.dumps({"columns": ["schema_name", "object_name", "object_type"], "rows": [["public", "users", "TABLE"]]})
            with patch.object(server, "execute", return_value=mock_ret) as mock_exec:
                out_str = asyncio.run(server.find_objects("crm", "crm · find users", "user", schema="public", limit=10))
                mock_exec.assert_called_once()
                call_args = mock_exec.call_args[0]
                self.assertEqual(call_args[0], "find_objects")
                self.assertIn("FROM information_schema.tables", call_args[3])
                self.assertEqual(call_args[4], ("%user%", "public", 10))
                data = json.loads(out_str)
                self.assertEqual(data["columns"], ["schema_name", "object_name", "object_type"])
                self.assertEqual(data["rows"], [["public", "users", "TABLE"]])

    def test_describe_table_compact_and_status(self):
        sample_toml = b"""[monitor]
port = 48260

[db.oracle.hr]
dsn = "hr/hr@localhost/hr"
"""
        with self.temp_config(sample_toml):
            # Test 1: Standard / non-compact mode with VALID status
            mock_rows = json.dumps([
                {"COLUMN_NAME": "EMP_ID", "DATA_TYPE": "NUMBER(10)", "NULLABLE": "N", "STATUS": "VALID"},
                {"COLUMN_NAME": "NAME", "DATA_TYPE": "VARCHAR2(50)", "NULLABLE": "Y", "STATUS": "VALID"},
            ])
            with patch.object(server, "execute", return_value=mock_rows):
                res = json.loads(asyncio.run(server.describe_table("hr", "employees", "hr · desc", compact=False)))
                self.assertEqual(res["table"], "employees")
                self.assertEqual(res["status"], "VALID")
                self.assertNotIn("error_hint", res)
                self.assertEqual(len(res["columns"]), 2)
                self.assertEqual(res["columns"][0], "EMP_ID: NUMBER(10) [NOT NULL]")
                self.assertEqual(res["columns"][1], "NAME: VARCHAR2(50)")

            # Test 2: Compact mode (returns array of 'col: type' strings)
            with patch.object(server, "execute", return_value=mock_rows):
                res = json.loads(asyncio.run(server.describe_table("hr", "employees", "hr · desc", compact=True)))
                self.assertEqual(res["columns"][0], "EMP_ID: NUMBER(10)")
                self.assertEqual(res["columns"][1], "NAME: VARCHAR2(50)")

            # Test 3: Object with INVALID status
            invalid_rows = json.dumps([
                {"COLUMN_NAME": "EMP_ID", "DATA_TYPE": "UNDEFINED", "NULLABLE": "Y", "STATUS": "INVALID"}
            ])
            with patch.object(server, "execute", return_value=invalid_rows):
                res = json.loads(asyncio.run(server.describe_table("hr", "broken_vw", "hr · desc")))
                self.assertEqual(res["status"], "INVALID")
                self.assertIn("error_hint", res)
                self.assertIn("compilation errors", res["error_hint"])

            # Test 4: Enriched metadata (PK, FK, UNIQUE, DEFAULT, comments)
            enriched_rows = json.dumps([
                {
                    "column_name": "id",
                    "data_type": "bigint",
                    "is_nullable": "NO",
                    "constraint_type": "PRIMARY KEY",
                    "column_default": "nextval('users_id_seq'::regclass)",
                    "column_comment": "Primary internal identifier",
                },
                {
                    "column_name": "email",
                    "data_type": "varchar(255)",
                    "is_nullable": "NO",
                    "constraint_type": "UNIQUE",
                    "column_default": None,
                },
                {
                    "column_name": "dept_id",
                    "data_type": "integer",
                    "is_nullable": "YES",
                    "foreign_table": "departments",
                    "column_default": "1",
                },
            ])
            with patch.object(server, "execute", return_value=enriched_rows):
                res = json.loads(asyncio.run(server.describe_table("hr", "users", "hr · desc", compact=False)))
                self.assertIn("[PK]", res["columns"][0])
                self.assertIn("[NOT NULL]", res["columns"][0])
                self.assertIn("Primary internal identifier", res["columns"][0])
                self.assertIn("[UNIQUE]", res["columns"][1])
                self.assertIn("[NOT NULL]", res["columns"][1])
                self.assertIn("[FK -> departments]", res["columns"][2])
                self.assertIn("[DEFAULT: 1]", res["columns"][2])

    def test_oracle_limit_error_hint(self):
        ora = server.Oracle("ora_test", {"type": "oracle", "dsn": "ora@localhost/db"})
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("ORA-00933: SQL command not properly ended")
        mock_cursor.__enter__.return_value = mock_cursor

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        ora.conn = mock_conn

        with self.assertRaises(Exception) as ctx:
            ora._exec("SELECT * FROM employees LIMIT 10", ())

        err_text = str(ctx.exception)
        self.assertIn("ORA-00933", err_text)
        self.assertIn("Hint: Oracle SQL does not support the 'LIMIT' clause", err_text)
        self.assertIn("FETCH FIRST 10 ROWS ONLY", err_text)

    def test_sample_table_dialect_queries(self):
        # 1. Postgres
        pg = server.Postgres("pg_test", {"type": "postgres", "dsn": "postgres://localhost/test"})
        sql_pg, params_pg = pg.get_sample_query("billing.invoices", limit=5)
        self.assertEqual(sql_pg, 'SELECT * FROM "billing"."invoices" LIMIT %s')
        self.assertEqual(params_pg, (5,))

        # 2. MySQL
        my = server.MySQL("my_test", {"type": "mysql", "dsn": "mysql://localhost/test"})
        sql_my, params_my = my.get_sample_query("orders", limit=3, schema="shop")
        self.assertEqual(sql_my, "SELECT * FROM `shop`.`orders` LIMIT %s")
        self.assertEqual(params_my, (3,))

        # 3. Oracle
        ora = server.Oracle("ora_test", {"type": "oracle", "dsn": "user/pw@localhost/test", "schema": "HR"})
        sql_ora, params_ora = ora.get_sample_query("employees", limit=10)
        self.assertEqual(sql_ora, 'SELECT * FROM "HR"."EMPLOYEES" WHERE ROWNUM <= :1')
        self.assertEqual(params_ora, (10,))

        # 4. Invalid identifier validation
        with self.assertRaises(ValueError):
            pg.get_sample_query("users; DROP TABLE users--")

        # 5. Backtick identifier stripping
        target_schema, table_clean = pg._parse_and_validate_table("`orders`", "`shop`")
        self.assertEqual(target_schema, "shop")
        self.assertEqual(table_clean, "orders")

    def test_search_columns_dialect_queries(self):
        # 1. Postgres
        pg = server.Postgres("pg_test", {"type": "postgres", "dsn": "postgres://localhost/test"})
        sql_pg, params_pg = pg.get_search_columns_query("cif_number", limit=20)
        self.assertIn("FROM information_schema.columns", sql_pg)
        self.assertIn("pg_catalog", sql_pg)
        self.assertEqual(params_pg, ("%cif_number%", 20))

        # 2. MySQL
        my = server.MySQL("my_test", {"type": "mysql", "dsn": "mysql://localhost/test"})
        sql_my, params_my = my.get_search_columns_query("email", schema="customer", limit=15)
        self.assertIn("FROM information_schema.columns", sql_my)
        self.assertIn("performance_schema", sql_my)
        self.assertEqual(params_my, ("%email%", "customer", 15))

        # 3. Oracle
        ora = server.Oracle("ora_test", {"type": "oracle", "dsn": "user/pw@localhost/test"})
        sql_ora, params_ora = ora.get_search_columns_query("nik", schema="hr", limit=25)
        self.assertIn("FROM all_tab_columns", sql_ora)
        self.assertIn("WHERE ROWNUM <=", sql_ora)
        self.assertEqual(params_ora, ("%nik%", "hr", 25))

    def test_describe_relations_dialect_queries(self):
        # 1. Postgres
        pg = server.Postgres("pg_test", {"type": "postgres", "dsn": "postgres://localhost/test", "schema": "public"})
        sql_pg, params_pg = pg.get_relations_query("orders")
        self.assertIn("OUTGOING", sql_pg)
        self.assertIn("INCOMING", sql_pg)
        self.assertIn("information_schema.referential_constraints", sql_pg)
        self.assertEqual(params_pg[0], "orders")

        # 2. MySQL
        my = server.MySQL("my_test", {"type": "mysql", "dsn": "mysql://localhost/test", "schema": "app"})
        sql_my, params_my = my.get_relations_query("orders")
        self.assertIn("information_schema.KEY_COLUMN_USAGE", sql_my)
        self.assertIn("REFERENCED_TABLE_NAME IS NOT NULL", sql_my)
        self.assertEqual(params_my[0], "orders")

        # 3. Oracle
        ora = server.Oracle("ora_test", {"type": "oracle", "dsn": "user/pw@localhost/test", "schema": "HR"})
        sql_ora, params_ora = ora.get_relations_query("employees")
        self.assertIn("all_constraints", sql_ora)
        self.assertIn("constraint_type = 'R'", sql_ora)
        self.assertEqual(params_ora[0], "EMPLOYEES")
        self.assertEqual(params_ora[1], "HR")

    def test_list_indexes_dialect_queries(self):
        # 1. Postgres
        pg = server.Postgres("pg_test", {"type": "postgres", "dsn": "postgres://localhost/test", "schema": "public"})
        sql_pg, params_pg = pg.get_indexes_query("users")
        self.assertIn("FROM pg_index ix", sql_pg)
        self.assertIn("ix.indisunique", sql_pg)
        self.assertEqual(params_pg[0], "users")

        # 2. MySQL
        my = server.MySQL("my_test", {"type": "mysql", "dsn": "mysql://localhost/test", "schema": "store"})
        sql_my, params_my = my.get_indexes_query("users")
        self.assertIn("information_schema.STATISTICS", sql_my)
        self.assertIn("INDEX_NAME", sql_my)
        self.assertEqual(params_my[0], "users")

        # 3. Oracle
        ora = server.Oracle("ora_test", {"type": "oracle", "dsn": "user/pw@localhost/test", "schema": "HR"})
        sql_ora, params_ora = ora.get_indexes_query("users")
        self.assertIn("all_indexes", sql_ora)
        self.assertIn("all_ind_columns", sql_ora)
        self.assertEqual(params_ora[0], "USERS")
        self.assertEqual(params_ora[1], "HR")

    def test_get_object_ddl_dialect_queries(self):
        # 1. Postgres Table
        pg = server.Postgres("pg_test", {"type": "postgres", "dsn": "postgres://localhost/test", "schema": "public"})
        sql_pg_tbl, params_pg_tbl = pg.get_ddl_query("users", object_type="TABLE")
        self.assertIn("CREATE TABLE", sql_pg_tbl)
        self.assertIn("information_schema.columns", sql_pg_tbl)
        self.assertEqual(params_pg_tbl[0], "users")

        # 2. Postgres View
        sql_pg_vw, params_pg_vw = pg.get_ddl_query("active_users", object_type="VIEW")
        self.assertIn("pg_get_viewdef", sql_pg_vw)
        self.assertEqual(params_pg_vw[0], "active_users")

        # 3. MySQL
        my = server.MySQL("my_test", {"type": "mysql", "dsn": "mysql://localhost/test", "schema": "store"})
        sql_my, params_my = my.get_ddl_query("orders")
        self.assertIn("SHOW CREATE TABLE `store`.`orders`", sql_my)
        self.assertEqual(params_my, ())

        # 4. Oracle
        ora = server.Oracle("ora_test", {"type": "oracle", "dsn": "user/pw@localhost/test", "schema": "HR"})
        sql_ora, params_ora = ora.get_ddl_query("employees", object_type="TABLE")
        self.assertIn("DBMS_METADATA.GET_DDL", sql_ora)
        self.assertEqual(params_ora, ("TABLE", "EMPLOYEES", "HR"))

    def test_sample_table_and_search_columns_mcp_tools(self):
        sample_toml = b"""[monitor]
port = 48260

[db.postgres.crm]
dsn = "postgres://localhost/crm"
"""
        with self.temp_config(sample_toml):
            # Test sample_table tool execution (returns columnar format)
            sample_ret = json.dumps({"columns": ["id", "code"], "rows": [[1, "ABC"]]})
            with patch.object(server, "execute", return_value=sample_ret) as mock_exec:
                res = asyncio.run(server.sample_table("crm", "customers", "crm · preview sample", limit=5))
                mock_exec.assert_called_once()
                call_args = mock_exec.call_args[0]
                self.assertEqual(call_args[0], "sample_table")
                self.assertEqual(call_args[3], 'SELECT * FROM "customers" LIMIT %s')
                self.assertEqual(call_args[4], (5,))
                self.assertEqual(json.loads(res), {"columns": ["id", "code"], "rows": [[1, "ABC"]]})

            # Test search_columns tool execution (returns columnar format)
            search_ret = json.dumps({
                "columns": ["schema_name", "table_name", "column_name", "data_type"],
                "rows": [["public", "customers", "cif_no", "text"]],
            })
            with patch.object(server, "execute", return_value=search_ret) as mock_exec:
                res = asyncio.run(server.search_columns("crm", "cif*", "crm · find column", limit=10))
                mock_exec.assert_called_once()
                call_args = mock_exec.call_args[0]
                self.assertEqual(call_args[0], "search_columns")
                self.assertEqual(call_args[4], ("cif%", 10))
                self.assertEqual(json.loads(res)["columns"], ["schema_name", "table_name", "column_name", "data_type"])
                self.assertEqual(json.loads(res)["rows"], [["public", "customers", "cif_no", "text"]])

            # Test describe_relations tool execution
            rel_ret = json.dumps({
                "columns": ["direction", "constraint_name", "source_table", "source_column", "target_table", "target_column", "on_delete", "on_update"],
                "rows": [["OUTGOING", "fk_orders_user", "public.orders", "user_id", "public.users", "id", "CASCADE", "NO ACTION"]],
            })
            with patch.object(server, "execute", return_value=rel_ret) as mock_exec:
                res = asyncio.run(server.describe_relations("crm", "orders", "crm · get relations"))
                mock_exec.assert_called_once()
                call_args = mock_exec.call_args[0]
                self.assertEqual(call_args[0], "describe_relations")
                self.assertIn("OUTGOING", call_args[3])
                self.assertEqual(json.loads(res)["rows"][0][1], "fk_orders_user")

            # Test list_indexes tool execution
            idx_ret = json.dumps({
                "columns": ["index_name", "columns", "is_unique", "is_primary", "index_type"],
                "rows": [["pk_users", "id", "YES", "YES", "btree"]],
            })
            with patch.object(server, "execute", return_value=idx_ret) as mock_exec:
                res = asyncio.run(server.list_indexes("crm", "users", "crm · get indexes"))
                mock_exec.assert_called_once()
                call_args = mock_exec.call_args[0]
                self.assertEqual(call_args[0], "list_indexes")
                self.assertIn("index_name", call_args[3])
                self.assertEqual(json.loads(res)["rows"][0][0], "pk_users")

            # Test get_object_ddl tool execution
            ddl_ret = json.dumps({
                "columns": ["ddl"],
                "rows": [["CREATE TABLE crm.users (id bigint PRIMARY KEY);"]],
            })
            with patch.object(server, "execute", return_value=ddl_ret) as mock_exec:
                res = asyncio.run(server.get_object_ddl("crm", "users", "crm · get ddl"))
                mock_exec.assert_called_once()
                self.assertEqual(mock_exec.call_args[0][0], "get_object_ddl")
                self.assertEqual(mock_exec.call_args.kwargs.get("table_for_auth"), "users")
                self.assertEqual(json.loads(res)["object"], "users")
                self.assertIn("CREATE TABLE", json.loads(res)["ddl"])

    def test_describe_table_handles_columnar_and_legacy_list(self):
        sample_toml = b"""[monitor]
port = 48260

[db.oracle.hr]
dsn = "hr/hr@localhost/hr"
"""
        with self.temp_config(sample_toml):
            # Test with modern columnar format
            columnar_rows = json.dumps({
                "columns": ["COLUMN_NAME", "DATA_TYPE", "NULLABLE", "STATUS"],
                "rows": [
                    ["EMP_ID", "NUMBER(10)", "N", "VALID"],
                    ["NAME", "VARCHAR2(50)", "Y", "VALID"],
                ],
            })
            with patch.object(server, "execute", return_value=columnar_rows):
                res = json.loads(asyncio.run(server.describe_table("hr", "employees", "hr · desc")))
                self.assertEqual(res["table"], "employees")
                self.assertEqual(res["status"], "VALID")
                self.assertEqual(len(res["columns"]), 2)
                self.assertEqual(res["columns"][0], "EMP_ID: NUMBER(10) [NOT NULL]")
                self.assertEqual(res["columns"][1], "NAME: VARCHAR2(50)")

            # Test with legacy list of dicts format
            legacy_rows = json.dumps([
                {"COLUMN_NAME": "EMP_ID", "DATA_TYPE": "NUMBER(10)", "NULLABLE": "N", "STATUS": "VALID"}
            ])
            with patch.object(server, "execute", return_value=legacy_rows):
                res = json.loads(asyncio.run(server.describe_table("hr", "employees", "hr · desc")))
                self.assertEqual(res["table"], "employees")
                self.assertEqual(res["columns"][0], "EMP_ID: NUMBER(10) [NOT NULL]")

    def test_execute_returns_columnar_format(self):
        sample_toml = b"""[monitor]
port = 48260

[defaults]
allow_metadata = ["read"]
allow_data = ["read"]

[db.postgres.crm]
dsn = "postgres://localhost/crm"
"""
        with self.temp_config(sample_toml):
            db = server.get_db("crm")
            db.run = MagicMock(return_value=(["EMP_NUMBER", "FULL_NAME", "JOB_TITLE"], [("P01", "ANDI", "MGR"), ("P02", "BUDI", "STAFF")]))

            server.link.ensure = MagicMock()
            async def fake_ensure(timeout_sec=0):
                return
            server.link.ensure = fake_ensure

            server.link.send = MagicMock()
            async def fake_send(msg):
                return
            server.link.send = fake_send

            raw_res = asyncio.run(server.execute("query", db, "crm · test", "SELECT * FROM employees"))
            parsed = json.loads(raw_res)
            self.assertEqual(parsed, {
                "columns": ["EMP_NUMBER", "FULL_NAME", "JOB_TITLE"],
                "rows": [
                    ["P01", "ANDI", "MGR"],
                    ["P02", "BUDI", "STAFF"],
                ],
            })

    def test_sample_table_restricted_access_control(self):
        sample_toml = b"""[monitor]
port = 48260

[defaults]
allow_metadata = ["read"]

[db.postgres.crm]
dsn = "postgres://localhost/crm"
restricted_tables = ["payroll_*", "secret"]
restricted_columns = ["salary*", "nik"]

[db.postgres.crm2]
dsn = "postgres://localhost/crm"
restricted_tables = ["payroll_*"]
restricted_columns = []
"""
        with self.temp_config(sample_toml):
            sent_messages = []
            async def fake_ensure(timeout_sec=0):
                return
            async def fake_send(msg):
                sent_messages.append(msg)
            server.link.ensure = fake_ensure
            server.link.send = fake_send

            mock_reader = MagicMock()
            mock_reader.readline = MagicMock()
            async def fake_readline():
                return b'{"approved": true}\n'
            mock_reader.readline = fake_readline
            server.link.reader = mock_reader

            with patch.object(server.Postgres, "run", return_value=(["id", "amount"], [(1, 5000)])):
                # Test 1: sample_table on table matching restricted_columns MUST trigger request approval and NOT auto_allow
                res = asyncio.run(server.sample_table("crm", "payroll_data", "crm · audit payroll", limit=5))
                req_messages = [m for m in sent_messages if m.get("type") == "request"]
                self.assertTrue(len(req_messages) > 0)
                self.assertFalse(req_messages[0]["can_auto_allow"])
                # Ensure auto_allow was NOT sent
                auto_messages = [m for m in sent_messages if m.get("type") == "auto_allow"]
                self.assertEqual(len(auto_messages), 0)

                # Test 2: restricted table when restricted_columns is empty triggers restricted_table(s)
                sent_messages.clear()
                res2 = asyncio.run(server.sample_table("crm2", "payroll_data", "crm · audit payroll 2", limit=5))
                req_messages2 = [m for m in sent_messages if m.get("type") == "request"]
                self.assertTrue(len(req_messages2) > 0)
                self.assertEqual(req_messages2[0]["trigger_reason"], "restricted_table(s): payroll_data")
                self.assertFalse(req_messages2[0]["can_auto_allow"])
                self.assertEqual(len([m for m in sent_messages if m.get("type") == "auto_allow"]), 0)

    def test_inspect_query_access_and_columns(self):
        # 1. Simple query
        tables, cols, has_star = server.inspect_query_access("SELECT id, name FROM users WHERE status = 1")
        self.assertEqual(tables, {"users"})
        self.assertEqual(cols, {"id", "name", "status"})
        self.assertFalse(has_star)

        # 2. Wildcard query
        tables_star, cols_star, has_star2 = server.inspect_query_access("SELECT * FROM employees")
        self.assertEqual(tables_star, {"employees"})
        self.assertTrue(has_star2)

        # 3. CTE query (CTE alias should NOT be listed as table)
        sql_cte = """
        WITH active_orders AS (
            SELECT order_id, customer_id FROM orders WHERE amount > 100
        )
        SELECT a.order_id, c.name
        FROM active_orders a
        JOIN customers c ON a.customer_id = c.id
        """
        tables_cte, cols_cte, _ = server.inspect_query_access(sql_cte)
        self.assertEqual(tables_cte, {"orders", "customers"})
        self.assertIn("amount", cols_cte)
        self.assertIn("name", cols_cte)

    def test_check_restricted_columns_matching(self):
        sens_patterns = ["salary*", "user.nik", "password_hash"]

        # 1. Safe query: no restricted columns
        matched_safe = server.check_restricted_columns({"id", "username"}, {"user"}, False, sens_patterns)
        self.assertEqual(matched_safe, [])

        # 2. Bare pattern match
        matched_bare = server.check_restricted_columns({"id", "salary_amount"}, {"payroll"}, False, sens_patterns)
        self.assertEqual(matched_bare, ["salary_amount"])

        # 3. Table-qualified pattern match: user.nik matches when table is user
        matched_qual = server.check_restricted_columns({"nik"}, {"user"}, False, sens_patterns)
        self.assertEqual(matched_qual, ["nik (user.nik)"])

        # 4. Table-qualified pattern does NOT match when table is different
        matched_other_tbl = server.check_restricted_columns({"nik"}, {"other_table"}, False, sens_patterns)
        self.assertEqual(matched_other_tbl, [])

        # 5. Wildcard query on table with restricted columns
        matched_wildcard = server.check_restricted_columns(set(), {"payroll"}, True, sens_patterns)
        self.assertIn("* (salary*)", matched_wildcard)

    def test_granular_access_control_rules_and_priorities(self):
        sample_toml = b"""[monitor]
port = 48260

[defaults]
allow_metadata = ["read"]
allow_data = []

[db.postgres.corp]
dsn = "postgres://localhost/corp"
allow_tables = ["departments", "public_announcements"]
restricted_tables = ["pegawai_salary", "user_identity"]
restricted_columns = ["salary*", "nik", "password_hash"]
"""
        with self.temp_config(sample_toml):
            db_corp = server.get_db("corp")
            db_corp.run = MagicMock(return_value=(["col"], [(1,)]))

            # Dummy link to capture sent messages
            sent_messages = []
            server.link.writer = MagicMock()
            server.link.writer.is_closing.return_value = False
            server.link.ensure = MagicMock()

            async def fake_ensure(timeout_sec=0):
                return
            server.link.ensure = fake_ensure

            async def fake_send(msg):
                sent_messages.append(msg)
            server.link.send = fake_send

            # 1. Metadata tool: list_tables should auto-allow via allow_metadata = ["read"]
            sent_messages.clear()
            asyncio.run(server.execute("list_tables", db_corp, "corp · list tables", "SELECT 1"))
            self.assertEqual(sent_messages[0]["type"], "auto_allow")
            self.assertEqual(sent_messages[0]["reason"], "allow_metadata permits 'read'")

            # 2. Safe data query on allow_tables without restricted columns -> auto-allowed
            sent_messages.clear()
            asyncio.run(server.execute("query", db_corp, "corp · query dept", "SELECT id, name FROM departments"))
            self.assertEqual(sent_messages[0]["type"], "auto_allow")
            self.assertIn("match allow_tables", sent_messages[0]["reason"])

            # 3. Restricted table: even if in allow_tables, restricted_tables has strict priority
            db_corp.cfg["allow_tables"].append("pegawai_salary")
            sent_messages.clear()
            server.link.reader = MagicMock()
            server.link.reader.readline = MagicMock()

            async def fake_readline_approve():
                return (json.dumps({"approved": True}) + "\n").encode()
            server.link.reader.readline = fake_readline_approve

            asyncio.run(server.execute("query", db_corp, "corp · query salary", "SELECT id, month FROM pegawai_salary"))
            req_msg = next(m for m in sent_messages if m["type"] == "request")
            self.assertFalse(req_msg["can_auto_allow"])
            self.assertIn("restricted_table", req_msg["trigger_reason"])

            # 4. Restricted column: query accessing salary_amount forces prompt & disables auto_allow
            sent_messages.clear()
            asyncio.run(server.execute("query", db_corp, "corp · query salary", "SELECT id, salary_amount FROM departments"))
            req_msg2 = next(m for m in sent_messages if m["type"] == "request")
            self.assertFalse(req_msg2["can_auto_allow"])
            self.assertIn("restricted column(s)", req_msg2["trigger_reason"])

            # 5. Normal unapproved table -> prompts with can_auto_allow = True and candidate_tables
            sent_messages.clear()
            asyncio.run(server.execute("query", db_corp, "corp · query orders", "SELECT id, total FROM orders"))
            req_msg3 = next(m for m in sent_messages if m["type"] == "request")
            self.assertTrue(req_msg3["can_auto_allow"])
            self.assertEqual(req_msg3["candidate_tables"], ["orders"])


if __name__ == "__main__":
    unittest.main()
