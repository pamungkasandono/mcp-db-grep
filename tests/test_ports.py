import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor
import server


class TestPorts(unittest.TestCase):
    def test_write_and_remove_state_port(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_port_file = Path(tmpdir) / "monitor.port"
            with patch.object(monitor, "STATE_PORT_FILE", test_port_file):
                # Write port
                monitor.write_state_port(48261)
                self.assertTrue(test_port_file.exists())
                self.assertEqual(test_port_file.read_text(encoding="utf-8").strip(), "48261")

                # Overwrite stale port
                monitor.write_state_port(48262)
                self.assertEqual(test_port_file.read_text(encoding="utf-8").strip(), "48262")

                # Remove port file
                monitor.remove_state_port()
                self.assertFalse(test_port_file.exists())

    def test_resolve_candidate_ports_with_state_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_port_file = Path(tmpdir) / "monitor.port"
            test_port_file.write_text("48265", encoding="utf-8")

            with patch.object(server, "STATE_PORT_FILE", test_port_file):
                ports = server.resolve_candidate_ports()
                self.assertEqual(ports[0], 48265)
                self.assertIn(48260, ports)

    def test_resolve_candidate_ports_fallback_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_file = Path(tmpdir) / "non_existent.port"
            with patch.object(server, "STATE_PORT_FILE", empty_file):
                with patch.dict("os.environ", {}, clear=True):
                    ports = server.resolve_candidate_ports()
                    # First port should be default
                    self.assertIn(48260, ports)
                    # Range should contain 48260..48269
                    for p in range(48260, 48270):
                        self.assertIn(p, ports)


if __name__ == "__main__":
    unittest.main()
