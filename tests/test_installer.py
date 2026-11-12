import tempfile
import unittest
from pathlib import Path

import installer


class TestInstaller(unittest.TestCase):
    def test_read_and_write_json_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.json"
            # Test write
            installer.write_json_file(file_path, {"test": 123, "name": "db-grep"})
            self.assertTrue(file_path.exists())

            # Test read
            data = installer.read_json_file(file_path)
            self.assertEqual(data.get("test"), 123)
            self.assertEqual(data.get("name"), "db-grep")

    def test_read_json_with_comments_lenient(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.jsonc"
            file_path.write_text("""{
  // This is a single line comment
  "enabled": true,
  "port": 48260
}""", encoding="utf-8")

            data = installer.read_json_file(file_path)
            self.assertEqual(data.get("enabled"), True)
            self.assertEqual(data.get("port"), 48260)

    def test_harness_registry_order(self):
        expected_order = [
            "claude",
            "claude-desktop",
            "opencode",
            "antigravity",
            "cursor",
            "codex",
            "roo-code",
            "zed",
            "pi",
        ]
        actual_order = [item["id"] for item in installer.HARNESS_REGISTRY]
        self.assertEqual(actual_order, expected_order)

    def test_install_codex_toml_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_file = Path(tmpdir) / "config.toml"
            codex_file.write_text('model = "gpt-4o"\n', encoding="utf-8")

            # Patch path
            original_fn = installer.get_codex_config_path
            installer.get_codex_config_path = lambda: codex_file
            try:
                installer.install_codex(Path("/dummy/instructions.md"))
                content = codex_file.read_text(encoding="utf-8")
                self.assertIn('model = "gpt-4o"', content)
                self.assertIn("[mcp_servers.db]", content)
                self.assertIn('command = "db-grep"', content)
                self.assertIn('args = ["--server"]', content)
            finally:
                installer.get_codex_config_path = original_fn

    def test_install_json_harnesses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            claude_cfg = tmp / "claude.json"
            roo_cfg = tmp / "roo.json"
            zed_cfg = tmp / "zed.json"

            orig_claude = installer.get_claude_desktop_config_path
            orig_roo = installer.get_roo_code_config_path
            orig_zed = installer.get_zed_config_path
            installer.get_claude_desktop_config_path = lambda: claude_cfg
            installer.get_roo_code_config_path = lambda: roo_cfg
            installer.get_zed_config_path = lambda: zed_cfg
            try:
                dummy_instr = Path("/dummy/instructions.md")
                installer.install_claude_desktop(dummy_instr)
                installer.install_roo_code(dummy_instr)
                installer.install_zed(dummy_instr)

                self.assertEqual(
                    installer.read_json_file(claude_cfg)["mcpServers"]["db"],
                    {"command": "db-grep", "args": ["--server"]},
                )
                self.assertEqual(
                    installer.read_json_file(roo_cfg)["mcpServers"]["db"],
                    {"command": "db-grep", "args": ["--server"], "disabled": False, "autoApprove": []},
                )
                self.assertEqual(
                    installer.read_json_file(zed_cfg)["context_servers"]["db"],
                    {"command": {"path": "db-grep", "args": ["--server"]}},
                )
            finally:
                installer.get_claude_desktop_config_path = orig_claude
                installer.get_roo_code_config_path = orig_roo
                installer.get_zed_config_path = orig_zed


if __name__ == "__main__":
    unittest.main()
