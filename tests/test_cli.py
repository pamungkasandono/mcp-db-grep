import subprocess
import sys
import unittest

from cli import parse_selection_tokens
from updater import get_current_version


class TestCli(unittest.TestCase):
    def run_cli(self, args):
        cmd = [sys.executable, "cli.py"] + args
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

    def test_version_flag(self):
        ver = get_current_version()
        res = self.run_cli(["--version"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("db-grep", res.stdout)
        self.assertIn(ver, res.stdout)

    def test_help_flag(self):
        res = self.run_cli(["--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("server (s)", res.stdout)
        self.assertIn("monitor (m)", res.stdout)
        self.assertIn("ping (p)", res.stdout)
        self.assertIn("config (c)", res.stdout)
        self.assertIn("install (i)", res.stdout)
        self.assertIn("update (u)", res.stdout)
        self.assertIn("driver (d)", res.stdout)

    def test_subcommand_helps(self):
        res_inst = self.run_cli(["install", "--help"])
        self.assertEqual(res_inst.returncode, 0)
        self.assertIn("install [-h] [CLIENT]", res_inst.stdout)

        res_up = self.run_cli(["update", "--help"])
        self.assertEqual(res_up.returncode, 0)
        self.assertIn("-y, --yes, -f, --force", res_up.stdout)

        res_cfg = self.run_cli(["config", "--help"])
        self.assertEqual(res_cfg.returncode, 0)
        self.assertIn("config [-h] [-y] [ACTION ...]", res_cfg.stdout)

        res_ping = self.run_cli(["ping", "--help"])
        self.assertEqual(res_ping.returncode, 0)
        self.assertIn("ping [-h] [DB ...]", res_ping.stdout)

    def test_shorthand_flags_work(self):
        # -v
        res_v = self.run_cli(["-v"])
        self.assertEqual(res_v.returncode, 0)
        self.assertIn("db-grep", res_v.stdout)

        # -h
        res_h = self.run_cli(["-h"])
        self.assertEqual(res_h.returncode, 0)
        self.assertIn("usage: db-grep", res_h.stdout)

    def test_bare_command_displays_help_and_version(self):
        ver = get_current_version()
        res = self.run_cli([])
        self.assertEqual(res.returncode, 0)
        self.assertIn(f"db-grep v{ver}", res.stdout)
        self.assertIn("usage: db-grep", res.stdout)

    def test_parse_selection_tokens_single(self):
        # 1-indexed input returning 0-indexed indices
        self.assertEqual(parse_selection_tokens("1", 5), [0])
        self.assertEqual(parse_selection_tokens("3", 5), [2])

    def test_parse_selection_tokens_comma_and_space(self):
        self.assertEqual(parse_selection_tokens("1, 3, 5", 5), [0, 2, 4])
        self.assertEqual(parse_selection_tokens("1 2 4", 5), [0, 1, 3])

    def test_parse_selection_tokens_range(self):
        self.assertEqual(parse_selection_tokens("1-3", 5), [0, 1, 2])
        self.assertEqual(parse_selection_tokens("2-4, 5", 5), [1, 2, 3, 4])
        self.assertEqual(parse_selection_tokens("4-2", 5), [1, 2, 3])

    def test_parse_selection_tokens_out_of_bounds(self):
        # 10 is out of bounds for total_items=5
        self.assertEqual(parse_selection_tokens("1, 10", 5), [0])
        self.assertEqual(parse_selection_tokens("0, -1, foo", 5), [])

    def test_normalize_cli_args(self):
        from cli import normalize_cli_args
        self.assertEqual(normalize_cli_args(["-s"]), ["server"])
        self.assertEqual(normalize_cli_args(["-m"]), ["monitor"])
        self.assertEqual(normalize_cli_args(["-p", "all"]), ["ping", "all"])
        self.assertEqual(normalize_cli_args(["-c", "list"]), ["config", "list"])
        self.assertEqual(normalize_cli_args(["-i", "codex"]), ["install", "codex"])
        self.assertEqual(normalize_cli_args(["-u"]), ["update"])
        self.assertEqual(normalize_cli_args(["-d", "oracle"]), ["driver", "oracle"])

    def test_invalid_argument_fails(self):
        res = self.run_cli(["--invalid-argument-xyz"])
        self.assertNotEqual(res.returncode, 0)

    def test_config_migrate_cli(self):
        import os
        import tempfile
        from pathlib import Path

        legacy_sample = """[defaults]\nread_metadata = true\n"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as tmp:
            tmp.write(legacy_sample)
            tmp_path = Path(tmp.name)

        try:
            env = dict(os.environ, DB_GREP_CONFIG=str(tmp_path))
            cmd = [sys.executable, "cli.py", "config", "migrate", "-y"]
            res = subprocess.run(cmd, capture_output=True, text=True, env=env)
            self.assertEqual(res.returncode, 0)
            self.assertIn("Successfully migrated", res.stdout)
            self.assertIn('allow_metadata = ["read"]', tmp_path.read_text(encoding="utf-8"))
        finally:
            tmp_path.unlink(missing_ok=True)
            tmp_path.with_suffix(".toml.bak").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
