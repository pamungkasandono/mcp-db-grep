import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
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
            {"type": "request", "tool": "query", "db": "postgres:academic_portal", "context": "academic_portal · q1", "sql": "SELECT 1;", "params": ()},
            {"type": "result", "cols": ["1"], "rows": [[1]], "ms": 5, "limit": 100},
            {"type": "request", "tool": "query", "db": "postgres:academic_portal", "context": "academic_portal · q2", "sql": "SELECT 2;", "params": ()},
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
            {"type": "request", "tool": "query", "db": "postgres:academic_portal", "context": "academic_portal · q1", "sql": "SELECT 1;", "params": ()},
            {"type": "result", "cols": ["1"], "rows": [[1]], "ms": 3, "limit": 100},
            {"type": "request", "tool": "query", "db": "postgres:academic_portal", "context": "academic_portal · q2", "sql": "SELECT 2;", "params": ()},
            {"type": "result", "cols": ["2"], "rows": [[2]], "ms": 4, "limit": 100},
            {"type": "request", "tool": "query", "db": "postgres:academic_portal", "context": "academic_portal · q3", "sql": "SELECT 3;", "params": ()},
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
        monitor.auto_approved_sessions["A"] = "pgsql:academic_portal"
        with patch.object(monitor, "getch") as mock_getch:
            asyncio.run(monitor.lock_sessions_interactive())
            # 1 active session locks immediately without calling getch
            mock_getch.assert_not_called()
        self.assertNotIn("A", monitor.auto_approved_sessions)

    def test_lock_under_5_sessions_direct_letter_choice(self):
        monitor.auto_approved_sessions = {
            "A": "pgsql:academic_portal",
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
            "A": "pgsql:academic_portal",
            "S27": "mysql:orders",
            "S28": "ora:payroll",
        }
        # Press '2' to lock the second item ('S27')
        with patch.object(monitor, "getch", return_value="2"):
            asyncio.run(monitor.lock_sessions_interactive())

        self.assertIn("A", monitor.auto_approved_sessions)
        self.assertNotIn("S27", monitor.auto_approved_sessions)
        self.assertIn("S28", monitor.auto_approved_sessions)

    def test_read_recent_audit_history_empty_and_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_file = Path(tmpdir) / "audit.jsonl"
            with patch.object(monitor, "AUDIT", missing_file):
                self.assertEqual(monitor.read_recent_audit_history(limit=20), [])
                self.assertEqual(monitor.read_recent_audit_history(limit=0), [])

    def test_read_recent_audit_history_consolidates_and_parses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            records = [
                {"ts": "2026-11-18T10:00:00Z", "event": "list_databases", "count": 2, "databases": ["postgres:academic_portal", "mysql:orders"]},
                {"ts": "2026-11-18T10:00:05Z", "event": "check_connection", "targets": ["all"], "count": 2},
                {"ts": "2026-11-18T10:01:00Z", "event": "auto_allow", "tool": "list_tables", "db": "postgres:academic_portal", "reason": "auto_metadata = true"},
                {"ts": "2026-11-18T10:02:00Z", "event": "denied", "reason": "monitor offline", "tool": "query", "db": "mysql:orders", "context": "orders · count", "sql": "SELECT count(*) FROM orders"},
                {"ts": "2026-11-18T10:03:00Z", "event": "request", "tool": "query", "db": "postgres:academic_portal", "context": "academic_portal · find students", "sql": "SELECT * FROM students"},
                {"ts": "2026-11-18T10:03:02Z", "event": "executed", "rows": 15, "ms": 4, "tool": "query", "db": "postgres:academic_portal", "context": "academic_portal · find students", "sql": "SELECT * FROM students"},
            ]
            audit_file.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

            with patch.object(monitor, "AUDIT", audit_file):
                res = monitor.read_recent_audit_history(limit=10)
                # Should have 5 records because request + executed are consolidated into executed
                self.assertEqual(len(res), 5)
                events = [r["event"] for r in res]
                self.assertEqual(events, ["list_databases", "check_connection", "auto_allow", "denied", "executed"])
                # The executed record preserved context and sql
                exec_rec = res[-1]
                self.assertEqual(exec_rec["context"], "academic_portal · find students")
                self.assertEqual(exec_rec["rows"], 15)

    def test_format_history_entry_badges(self):
        denied_offline = {
            "ts": "2026-11-18T10:02:00Z",
            "event": "denied",
            "reason": "monitor offline",
            "tool": "query",
            "db": "oracle:finance",
            "context": "finance · quarterly",
            "sql": "SELECT * FROM quarterly_revenue",
        }
        lines = monitor.format_history_entry(denied_offline)
        joined = " ".join(lines)
        self.assertIn("PAUSED-OFFLINE", joined)
        self.assertIn("query @ oracle:finance", joined)
        self.assertIn("SELECT * FROM quarterly_revenue", joined)
        self.assertIn("paused because monitor was offline", joined)

        exec_rec = {
            "ts": "2026-11-18T10:03:00Z",
            "event": "executed",
            "tool": "query",
            "db": "postgres:academic_portal",
            "context": "academic_portal · find",
            "sql": "SELECT * FROM students",
            "rows": 12,
            "ms": 5,
        }
        lines_exec = monitor.format_history_entry(exec_rec)
        joined_exec = " ".join(lines_exec)
        self.assertIn("EXECUTED", joined_exec)
        self.assertIn("12 rows, 5 ms", joined_exec)

    def test_render_history_and_live_banners(self):
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            monitor.render_history_banner([])
            monitor.render_live_banner(48260, fallback=False)
        output = stdout_buf.getvalue()
        self.assertIn("RECENT ACTIVITY (BEFORE MONITOR OPENED)", output)
        self.assertIn("No previous database activity", output)
        self.assertIn("LIVE STREAM & APPROVAL BRIDGE ACTIVE", output)

    def test_handle_event_streaming_live(self):
        messages = [
            {
                "type": "event",
                "tool": "check_connection",
                "targets": ["all"],
                "count": 1,
                "results": [{"db": "postgres:academic_portal", "type": "postgres", "status": "connected", "latency_ms": 12}],
            },
            {
                "type": "event",
                "tool": "list_databases",
                "count": 1,
                "databases": ["postgres:academic_portal"],
            },
            {
                "type": "event",
                "tool": "check_config",
                "valid": True,
            },
        ]
        reader = FakeStreamReader(messages)
        writer = FakeStreamWriter()
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            asyncio.run(monitor.handle(reader, writer))
        out = stdout_buf.getvalue()
        self.assertIn("check_connection", out)
        self.assertIn("academic_portal", out)
        self.assertIn("list_databases", out)
        self.assertIn("check_config", out)
        self.assertIn("VALID", out)


if __name__ == "__main__":
    unittest.main()
