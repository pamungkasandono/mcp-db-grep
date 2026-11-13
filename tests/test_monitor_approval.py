import asyncio
import json
import unittest
from unittest.mock import patch

import monitor


class FakeStreamReader:
    def __init__(self, messages: list[dict]):
        self._lines = [json.dumps(m).encode() + b"\n" for m in messages]

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeStreamWriter:
    def __init__(self):
        self.written: list[dict] = []
        self.closed = False

    def write(self, data: bytes):
        for line in data.decode().splitlines():
            if line.strip():
                self.written.append(json.loads(line))

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class TestMonitorApproval(unittest.TestCase):
    def setUp(self):
        monitor.sessions.clear()
        monitor.auto_approved_sessions.clear()
        monitor.prompt_active = False

    def tearDown(self):
        monitor.sessions.clear()
        monitor.auto_approved_sessions.clear()
        monitor.prompt_active = False

    def test_approve_once_requires_prompt_on_next_query(self):
        messages = [
            {"type": "request", "tool": "query", "db": "postgres:siakad", "konteks": "siakad · q1", "sql": "SELECT 1;", "params": ()},
            {"type": "result", "cols": ["1"], "rows": [[1]], "ms": 5, "limit": 100},
            {"type": "request", "tool": "query", "db": "postgres:siakad", "konteks": "siakad · q2", "sql": "SELECT 2;", "params": ()},
        ]
        reader = FakeStreamReader(messages)
        writer = FakeStreamWriter()

        # First query pressed 'y', second query pressed 'n'
        keys = ["y", "n"]
        with patch.object(monitor, "getch", side_effect=lambda _t: keys.pop(0)):
            asyncio.run(monitor.handle(reader, writer))

        # written[0] is hello, written[1] is 1st approval, written[2] is 2nd denial
        self.assertEqual(writer.written[0]["type"], "hello")
        self.assertEqual(writer.written[1], {"approved": True, "auto_session": False})
        self.assertEqual(writer.written[2], {"approved": False, "auto_session": False})
        self.assertEqual(len(keys), 0)

    def test_auto_approve_session_skips_subsequent_prompts_and_clears_on_disconnect(self):
        messages = [
            {"type": "request", "tool": "query", "db": "postgres:siakad", "konteks": "siakad · q1", "sql": "SELECT 1;", "params": ()},
            {"type": "result", "cols": ["1"], "rows": [[1]], "ms": 3, "limit": 100},
            {"type": "request", "tool": "query", "db": "postgres:siakad", "konteks": "siakad · q2", "sql": "SELECT 2;", "params": ()},
            {"type": "result", "cols": ["2"], "rows": [[2]], "ms": 4, "limit": 100},
            {"type": "request", "tool": "query", "db": "postgres:siakad", "konteks": "siakad · q3", "sql": "SELECT 3;", "params": ()},
            {"type": "result", "cols": ["3"], "rows": [[3]], "ms": 2, "limit": 100},
        ]
        reader = FakeStreamReader(messages)
        writer = FakeStreamWriter()

        getch_calls = []

        def fake_getch(_t):
            getch_calls.append(1)
            return "a"

        with patch.object(monitor, "getch", side_effect=fake_getch):
            asyncio.run(monitor.handle(reader, writer))

        # getch was only called ONCE for the first query; q2 and q3 were auto-approved
        self.assertEqual(len(getch_calls), 1)
        self.assertEqual(writer.written[1], {"approved": True, "auto_session": True})
        self.assertEqual(writer.written[2], {"approved": True, "auto_session": True})
        self.assertEqual(writer.written[3], {"approved": True, "auto_session": True})
        # Cleaned up automatically when session disconnects
        self.assertEqual(len(monitor.auto_approved_sessions), 0)

    def test_lock_single_session_instant(self):
        monitor.auto_approved_sessions["A"] = "pgsql:siakad"
        with patch.object(monitor, "getch") as mock_getch:
            asyncio.run(monitor.lock_sessions_interactive())
            # 1 active session locks immediately without calling getch
            mock_getch.assert_not_called()
        self.assertNotIn("A", monitor.auto_approved_sessions)

    def test_lock_under_5_sessions_direct_letter_choice(self):
        monitor.auto_approved_sessions = {
            "A": "pgsql:siakad",
            "B": "mysql:orders",
            "C": "ora:payroll",
        }
        # Press 'b' to lock only session B
        with patch.object(monitor, "getch", return_value="b"):
            asyncio.run(monitor.lock_sessions_interactive())

        self.assertIn("A", monitor.auto_approved_sessions)
        self.assertNotIn("B", monitor.auto_approved_sessions)
        self.assertIn("C", monitor.auto_approved_sessions)

        # Press '*' to lock all remaining
        with patch.object(monitor, "getch", return_value="*"):
            asyncio.run(monitor.lock_sessions_interactive())

        self.assertEqual(len(monitor.auto_approved_sessions), 0)

    def test_lock_30_sessions_numbered_list_selection(self):
        # Populate 30 active sessions (A..Z, S26..S29)
        for i in range(30):
            name = chr(65 + i) if i < 26 else f"S{i}"
            monitor.auto_approved_sessions[name] = f"pgsql:db-{i}"

        self.assertEqual(len(monitor.auto_approved_sessions), 30)

        # Lock sessions 1-3 (A, B, C) and 30 (S29)
        with patch.object(monitor, "read_line", return_value="1-3, 30"):
            asyncio.run(monitor.lock_sessions_interactive())

        self.assertNotIn("A", monitor.auto_approved_sessions)
        self.assertNotIn("B", monitor.auto_approved_sessions)
        self.assertNotIn("C", monitor.auto_approved_sessions)
        self.assertNotIn("S29", monitor.auto_approved_sessions)
        self.assertIn("D", monitor.auto_approved_sessions)
        self.assertEqual(len(monitor.auto_approved_sessions), 26)

        # Lock all remaining 26 sessions with default empty enter
        with patch.object(monitor, "read_line", return_value=""):
            asyncio.run(monitor.lock_sessions_interactive())

        self.assertEqual(len(monitor.auto_approved_sessions), 0)

    def test_lock_under_5_sessions_with_multichar_name_numeric_fallback(self):
        monitor.auto_approved_sessions = {
            "A": "pgsql:siakad",
            "S27": "mysql:orders",
            "S28": "ora:payroll",
        }
        # Press '2' to lock the second item ('S27')
        with patch.object(monitor, "getch", return_value="2"):
            asyncio.run(monitor.lock_sessions_interactive())

        self.assertIn("A", monitor.auto_approved_sessions)
        self.assertNotIn("S27", monitor.auto_approved_sessions)
        self.assertIn("S28", monitor.auto_approved_sessions)


if __name__ == "__main__":
    unittest.main()
