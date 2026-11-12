import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config_manager


class TestConfigManager(unittest.TestCase):
    def test_read_active_and_disabled_entries(self):
        sample = b"""[monitor]
port = 48260

[defaults]
auto_metadata = true

[db.postgres.ecommerce-dev]
description = "E-Commerce App"
dsn = "postgres://localhost/ecom"

# [DISABLED: server maintenance] [db.oracle.payroll-prod]
# description = "Payroll App"
# dsn = "localhost:1521/ORCL"
# user = "ro_user"
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample)
            tmp_path = Path(tmp.name)

        try:
            with patch.object(config_manager, "CONFIG", tmp_path):
                raw_lines, entries = config_manager.read_config_entries()
                self.assertEqual(len(entries), 2)

                e1 = entries[0]
                self.assertEqual(e1.key, "postgres:ecommerce-dev")
                self.assertTrue(e1.enabled)
                self.assertEqual(e1.get_field("description"), "E-Commerce App")

                e2 = entries[1]
                self.assertEqual(e2.key, "oracle:payroll-prod")
                self.assertFalse(e2.enabled)
                self.assertEqual(e2.reason, "server maintenance")
                self.assertEqual(e2.get_field("description"), "Payroll App")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_disable_and_enable_entry(self):
        sample = b"""[monitor]
port = 48260

[db.postgres.inventory-core]
description = "Core Inventory"
dsn = "postgres://localhost/inv"
password = "env:INVENTORY_PW"
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample)
            tmp_path = Path(tmp.name)

        try:
            with patch.object(config_manager, "CONFIG", tmp_path):
                # 1. Disable with reason
                success = config_manager.disable_entry("inventory-core", reason="weekly migration")
                self.assertTrue(success)

                content = tmp_path.read_text(encoding="utf-8")
                self.assertIn("# [DISABLED: weekly migration] [db.postgres.inventory-core]", content)
                self.assertIn("# description = \"Core Inventory\"", content)

                # Check read_config_entries reflects disabled state
                _, entries = config_manager.read_config_entries()
                self.assertFalse(entries[0].enabled)
                self.assertEqual(entries[0].reason, "weekly migration")

                # 2. Re-enable
                success_enable = config_manager.enable_entry("postgres:inventory-core")
                self.assertTrue(success_enable)

                content_restored = tmp_path.read_text(encoding="utf-8")
                self.assertIn("[db.postgres.inventory-core]", content_restored)
                self.assertNotIn("# [DISABLED", content_restored)
                self.assertIn("description = \"Core Inventory\"", content_restored)

                _, entries_restored = config_manager.read_config_entries()
                self.assertTrue(entries_restored[0].enabled)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_add_and_delete_entry(self):
        sample = b"""[monitor]
port = 48260
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample)
            tmp_path = Path(tmp.name)

        try:
            with patch.object(config_manager, "CONFIG", tmp_path):
                # 1. Add entry
                added = config_manager.add_entry(
                    driver="postgres",
                    name="billing-service",
                    dsn="postgres://localhost/billing",
                    description="Billing Service",
                    password="env:BILLING_PW",
                    auto_tables=["mst_*", "ref_*"],
                )
                self.assertTrue(added)

                content = tmp_path.read_text(encoding="utf-8")
                self.assertIn("[db.postgres.billing-service]", content)
                self.assertIn('auto_tables = ["mst_*", "ref_*"]', content)

                # 2. Delete entry
                deleted = config_manager.delete_entry("billing-service", confirm=False)
                self.assertTrue(deleted)

                content_after = tmp_path.read_text(encoding="utf-8")
                self.assertNotIn("billing-service", content_after)

                # 3. Add MySQL entry
                added_mysql = config_manager.add_entry(
                    driver="mysql",
                    name="orders-db",
                    dsn="mysql://localhost:3306/orders",
                    description="MySQL Orders",
                )
                self.assertTrue(added_mysql)
                self.assertIn("[db.mysql.orders-db]", tmp_path.read_text(encoding="utf-8"))
        finally:
            tmp_path.unlink(missing_ok=True)


    def test_resolve_entry_ambiguous(self):
        sample = b"""[monitor]
port = 48260

[db.postgres.inventory-core]
dsn = "postgres://localhost/inv"

[db.oracle.inventory-core]
dsn = "localhost:1521/ORCL"
user = "ro_user"
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample)
            tmp_path = Path(tmp.name)

        try:
            with patch.object(config_manager, "CONFIG", tmp_path):
                _, entries = config_manager.read_config_entries()

                # Short name -> ambiguous, must return None
                result = config_manager.resolve_entry("inventory-core", entries)
                self.assertIsNone(result)

                # Full key -> resolves correctly
                result_pg = config_manager.resolve_entry("postgres:inventory-core", entries)
                self.assertIsNotNone(result_pg)
                self.assertEqual(result_pg.key, "postgres:inventory-core")

                result_ora = config_manager.resolve_entry("oracle:inventory-core", entries)
                self.assertIsNotNone(result_ora)
                self.assertEqual(result_ora.key, "oracle:inventory-core")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_resolve_entry_unique_short_name(self):
        sample = b"""[monitor]
port = 48260

[db.postgres.ecommerce-dev]
dsn = "postgres://localhost/ecom"
"""
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as tmp:
            tmp.write(sample)
            tmp_path = Path(tmp.name)

        try:
            with patch.object(config_manager, "CONFIG", tmp_path):
                _, entries = config_manager.read_config_entries()
                result = config_manager.resolve_entry("ecommerce-dev", entries)
                self.assertIsNotNone(result)
                self.assertEqual(result.key, "postgres:ecommerce-dev")
        finally:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
