import subprocess
import sys
import unittest


class TestCli(unittest.TestCase):
    def run_cli(self, args):
        cmd = [sys.executable, "cli.py"] + args
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

    def test_version_flag(self):
        res = self.run_cli(["--version"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("db-grep", res.stdout)
        self.assertIn("0.1.0", res.stdout)

    def test_help_flag(self):
        res = self.run_cli(["--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("--server", res.stdout)
        self.assertIn("--monitor", res.stdout)
        self.assertIn("--init", res.stdout)
        self.assertIn("--client", res.stdout)
        self.assertIn("claude", res.stdout)
        self.assertIn("antigravity", res.stdout)
        self.assertIn("pi", res.stdout)

    def test_bare_command_displays_help_and_version(self):
        res = self.run_cli([])
        self.assertEqual(res.returncode, 0)
        self.assertIn("db-grep v0.1.0", res.stdout)
        self.assertIn("usage: db-grep", res.stdout)

    def test_invalid_argument_fails(self):
        res = self.run_cli(["--invalid-argument-xyz"])
        self.assertNotEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
