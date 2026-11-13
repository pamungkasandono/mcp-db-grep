import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch

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

    def test_harness_descriptions_and_menu_alignment_consistency(self):
        # 1. Verify all file-based harness descriptions are callable and return expanded absolute paths
        for item in installer.HARNESS_REGISTRY:
            if item["id"] == "claude":
                self.assertEqual(item["desc"], "`claude mcp add`")
            else:
                self.assertTrue(callable(item["desc"]), f"{item['id']} desc must be callable")
                resolved_desc = item["desc"]()
                self.assertFalse(resolved_desc.startswith("~"), f"{item['id']} path not expanded: {resolved_desc}")
                self.assertTrue(Path(resolved_desc).is_absolute(), f"{item['id']} path not absolute: {resolved_desc}")

        # 2. Verify menu spacing alignment across [1]..[9], [a], and [n]
        buf = io.StringIO()
        with patch("builtins.input", return_value="n"), redirect_stdout(buf):
            installer.ask_user_choices({"claude": True, "opencode": True})

        menu_lines = [line for line in buf.getvalue().splitlines() if line.strip().startswith("[")]
        self.assertEqual(len(menu_lines), 11)  # 9 harnesses + [a] + [n]
        dash_positions = {line.index(" - ") for line in menu_lines}
        self.assertEqual(len(dash_positions), 1, f"Inconsistent column alignment in menu: {menu_lines}")

    def test_cross_platform_harness_paths_windows_mac_linux(self):
        file_harnesses = [item for item in installer.HARNESS_REGISTRY if item["id"] != "claude"]

        class FakeWindowsPath(PureWindowsPath):
            def exists(self) -> bool:
                return False

            def expanduser(self):
                s = str(self)
                if s.startswith("~"):
                    home = os.environ.get("USERPROFILE", r"C:\Users\default")
                    return FakeWindowsPath(home + s[1:])
                return self

        class FakePosixPath(PurePosixPath):
            def exists(self) -> bool:
                return False

            def expanduser(self):
                s = str(self)
                if s.startswith("~"):
                    home = os.environ.get("HOME", "/home/default")
                    return FakePosixPath(home + s[1:])
                return self

        # 1. Windows (win32) with USERPROFILE and APPDATA
        win_env = {
            "USERPROFILE": r"C:\Users\901163",
            "APPDATA": r"C:\Users\901163\AppData\Roaming",
        }
        with patch.object(installer.sys, "platform", "win32"), patch.dict("os.environ", win_env, clear=True), patch.object(
            installer, "Path", FakeWindowsPath
        ):
            resolved = {item["id"]: item["desc"]() for item in file_harnesses}
            self.assertEqual(resolved["claude-desktop"], r"C:\Users\901163\AppData\Roaming\Claude\claude_desktop_config.json")
            self.assertEqual(resolved["opencode"], r"C:\Users\901163\.config\opencode\opencode.jsonc")
            self.assertEqual(resolved["antigravity"], r"C:\Users\901163\.gemini\config\mcp_config.json")
            self.assertEqual(resolved["cursor"], r"C:\Users\901163\.cursor\mcp.json")
            self.assertEqual(resolved["codex"], r"C:\Users\901163\.codex\config.toml")
            self.assertEqual(
                resolved["roo-code"],
                r"C:\Users\901163\AppData\Roaming\Code\User\globalStorage\rooveterinaryinc.roo-cline\settings\cline_mcp_settings.json",
            )
            self.assertEqual(resolved["zed"], r"C:\Users\901163\AppData\Roaming\Zed\settings.json")
            self.assertEqual(resolved["pi"], r"C:\Users\901163\.pi\agent\mcp.json")
            for hid, p in resolved.items():
                self.assertNotIn("~", p, f"Unexpanded ~ on Windows for {hid}: {p}")
                self.assertNotIn("/", p, f"Forward slash found on Windows for {hid}: {p}")

        # 2. macOS (darwin)
        with patch.object(installer.sys, "platform", "darwin"), patch.dict(
            "os.environ", {"HOME": "/Users/dev"}, clear=True
        ), patch.object(installer, "Path", FakePosixPath):
            resolved_mac = {item["id"]: item["desc"]() for item in file_harnesses}
            self.assertEqual(resolved_mac["claude-desktop"], "/Users/dev/Library/Application Support/Claude/claude_desktop_config.json")
            self.assertEqual(resolved_mac["opencode"], "/Users/dev/.config/opencode/opencode.jsonc")
            self.assertEqual(resolved_mac["antigravity"], "/Users/dev/.gemini/config/mcp_config.json")
            self.assertEqual(resolved_mac["cursor"], "/Users/dev/.cursor/mcp.json")
            self.assertEqual(resolved_mac["codex"], "/Users/dev/.codex/config.toml")
            self.assertEqual(
                resolved_mac["roo-code"],
                "/Users/dev/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json",
            )
            self.assertEqual(resolved_mac["zed"], "/Users/dev/.config/zed/settings.json")
            self.assertEqual(resolved_mac["pi"], "/Users/dev/.pi/agent/mcp.json")
            for hid, p in resolved_mac.items():
                self.assertNotIn("~", p, f"Unexpanded ~ on macOS for {hid}: {p}")
                self.assertNotIn("\\", p, f"Backslash found on macOS for {hid}: {p}")

        # 3. Linux (linux)
        with patch.object(installer.sys, "platform", "linux"), patch.dict(
            "os.environ", {"HOME": "/home/dev"}, clear=True
        ), patch.object(installer, "Path", FakePosixPath):
            resolved_linux = {item["id"]: item["desc"]() for item in file_harnesses}
            self.assertEqual(resolved_linux["claude-desktop"], "/home/dev/.config/Claude/claude_desktop_config.json")
            self.assertEqual(resolved_linux["opencode"], "/home/dev/.config/opencode/opencode.jsonc")
            self.assertEqual(resolved_linux["antigravity"], "/home/dev/.gemini/config/mcp_config.json")
            self.assertEqual(resolved_linux["cursor"], "/home/dev/.cursor/mcp.json")
            self.assertEqual(resolved_linux["codex"], "/home/dev/.codex/config.toml")
            self.assertEqual(
                resolved_linux["roo-code"],
                "/home/dev/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json",
            )
            self.assertEqual(resolved_linux["zed"], "/home/dev/.config/zed/settings.json")
            self.assertEqual(resolved_linux["pi"], "/home/dev/.pi/agent/mcp.json")
            for hid, p in resolved_linux.items():
                self.assertNotIn("~", p, f"Unexpanded ~ on Linux for {hid}: {p}")
                self.assertNotIn("\\", p, f"Backslash found on Linux for {hid}: {p}")


if __name__ == "__main__":
    unittest.main()
