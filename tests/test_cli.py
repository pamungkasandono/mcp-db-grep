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
        self.assertIn("-s, --server", res.stdout)
        self.assertIn("-m, --monitor", res.stdout)
        self.assertIn("-i, --init", res.stdout)
        self.assertIn("-c [CMD ...], --config [CMD ...]", res.stdout)
        self.assertIn("-C, --check-config", res.stdout)
        self.assertIn("-t [DB ...], --check-connection [DB ...]", res.stdout)
        self.assertIn("-u, --check-update", res.stdout)
        self.assertIn("-U, --upgrade", res.stdout)
        self.assertIn("-a {claude", res.stdout)
        self.assertIn("--client", res.stdout)
        self.assertIn("claude", res.stdout)
        self.assertIn("antigravity", res.stdout)
        self.assertIn("pi", res.stdout)

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

    def test_invalid_argument_fails(self):
        res = self.run_cli(["--invalid-argument-xyz"])
        self.assertNotEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
