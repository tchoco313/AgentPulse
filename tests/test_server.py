import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from pulse import server


def line(ts="2026-09-22T01:00:00.000Z", session="s1", inp=1):
    return json.dumps({
        "type": "assistant", "timestamp": ts, "sessionId": session,
        "cwd": "/Users/teo",
        "message": {"role": "assistant", "model": "claude-opus-5",
                    "content": [{"type": "tool_use", "id": "t", "name": "Bash",
                                 "input": {"command": "ls"}},
                                {"type": "text", "text": "비밀 대화 내용"}],
                    "usage": {"input_tokens": inp, "cache_read_input_tokens": 100}},
    })


CFG = {"context_limit": 1_000_000, "warn_threshold": 0.85,
       "caution_threshold": 0.70, "active_window_minutes": 15, "port": 0}


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.projects = self.root / "projects" / "-Users-teo"
        self.projects.mkdir(parents=True)
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<h1>AgentPulse</h1>", encoding="utf-8")
        self.claude_json = self.root / ".claude.json"
        self.claude_json.write_text(json.dumps({"mcpServers": {"dbhub": {}}}),
                                    encoding="utf-8")

    def start(self):
        state = server.State(
            cfg=CFG, projects_root=self.root / "projects",
            cache_path=self.root / "cache.json",
            claude_json=self.claude_json, skill_roots=[])
        self.httpd = server.make_server(state, port=0, web_dir=self.web)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.read().decode("utf-8")


class TestApi(ServerTestCase):
    def test_summary_returns_json(self):
        (self.projects / "a.jsonl").write_text(line(inp=42) + "\n", encoding="utf-8")
        self.start()
        status, body = self.get("/api/summary?range=all")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["totals"]["input"], 42)
        self.assertEqual(data["range"], "all")
        self.assertEqual(data["context_limit"], 1_000_000)

    def test_summary_omits_conversation_text(self):
        (self.projects / "a.jsonl").write_text(line() + "\n", encoding="utf-8")
        self.start()
        _, body = self.get("/api/summary?range=all")
        self.assertNotIn("비밀 대화 내용", body)

    def test_summary_omits_sessions(self):
        (self.projects / "a.jsonl").write_text(line() + "\n", encoding="utf-8")
        self.start()
        _, body = self.get("/api/summary?range=all")
        self.assertNotIn("sessions", json.loads(body))

    def test_summary_includes_unused_inventory(self):
        (self.projects / "a.jsonl").write_text(line() + "\n", encoding="utf-8")
        self.start()
        _, body = self.get("/api/summary?range=all")
        self.assertEqual(json.loads(body)["unused"]["mcp"], ["dbhub"])

    def test_live_returns_sessions(self):
        self.start()
        status, body = self.get("/api/live")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("sessions", data)
        self.assertEqual(data["warn_threshold"], 0.85)

    def test_index_served_at_root(self):
        self.start()
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("AgentPulse", body)

    def test_unknown_path_is_404(self):
        self.start()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/nope")
        self.assertEqual(cm.exception.code, 404)

    def test_path_traversal_is_refused(self):
        self.start()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/../../etc/passwd")
        self.assertIn(cm.exception.code, (400, 403, 404))

    def test_binds_loopback_only(self):
        self.start()
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")


class TestPortFallback(ServerTestCase):
    def _state(self):
        return server.State(
            cfg=CFG, projects_root=self.root / "projects",
            cache_path=self.root / "cache.json",
            claude_json=self.claude_json, skill_roots=[])

    def test_occupied_port_falls_through_to_next(self):
        taken = server.make_server(self._state(), port=0, web_dir=self.web)
        self.addCleanup(taken.server_close)
        busy = taken.server_address[1]

        httpd = server.bind(self._state(), port=busy, web_dir=self.web)
        self.addCleanup(httpd.server_close)

        self.assertNotEqual(httpd.server_address[1], busy)
        self.assertGreater(httpd.server_address[1], busy)

    def test_all_ports_occupied_raises(self):
        taken = server.make_server(self._state(), port=0, web_dir=self.web)
        self.addCleanup(taken.server_close)
        busy = taken.server_address[1]

        with self.assertRaises(OSError):
            server.bind(self._state(), port=busy, web_dir=self.web, attempts=1)


class TestEmptyEnvironment(ServerTestCase):
    def test_no_transcripts_still_serves(self):
        # Review Focus 4: 트랜스크립트가 하나도 없어도 정상으로 떠야 한다
        self.start()
        status, body = self.get("/api/summary?range=7d")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["totals"]["total"], 0)
        self.assertEqual(data["totals"]["cache_read_ratio"], 0.0)
        self.assertEqual(data["days"], [])
        self.assertEqual(data["compacts"]["count"], 0)

    def test_no_transcripts_live_is_empty(self):
        self.start()
        _, body = self.get("/api/live")
        self.assertEqual(json.loads(body)["sessions"], [])


if __name__ == "__main__":
    unittest.main()
