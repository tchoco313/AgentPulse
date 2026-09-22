import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from pulse import server
from tests import tmpdir


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
        self.root = tmpdir()
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
        # LIFO로 돌기 때문에 shutdown이 먼저, 그 다음 server_close다.
        # server_close를 빠뜨리면 듣던 소켓이 남아 ResourceWarning이 뜬다.
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, host=None):
        """host를 주면 Host 헤더를 그걸로 덮어쓴다.

        urllib은 Host를 URL에서 만든다 — 남의 Host를 흉내 내려면 요청 객체에
        직접 박아야 한다.
        """
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if host is not None:
            req.add_unredirected_header("Host", host)
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode("utf-8")

    def head(self, path, host=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method="HEAD")
        if host is not None:
            req.add_unredirected_header("Host", host)
        with urllib.request.urlopen(req) as r:
            return r.status


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
        # HTTPError는 파일 객체다 — 닫지 않으면 ResourceWarning이 남는다
        self.addCleanup(cm.exception.close)
        self.assertEqual(cm.exception.code, 404)

    def test_path_traversal_is_refused(self):
        self.start()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/../../etc/passwd")
        self.addCleanup(cm.exception.close)
        self.assertIn(cm.exception.code, (400, 403, 404))

    def test_falsy_claude_json_is_not_swapped_for_the_real_one(self):
        """주입한 경로가 falsy라도 진짜 ~/.claude.json으로 떨어지면 안 된다."""
        state = server.State(cfg=CFG, projects_root=self.root / "projects",
                             cache_path=self.root / "cache.json",
                             claude_json="", skill_roots=[])
        self.assertEqual(state.claude_json, "")

    def test_binds_loopback_only(self):
        self.start()
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")


class TestHostHeader(ServerTestCase):
    """DNS 리바인딩 차단.

    127.0.0.1에 묶어도 attacker.com이 127.0.0.1로 재해석되면 브라우저에는
    같은 출처가 된다 — 그 페이지가 /api/live를 읽으면 세션 제목(대화 제목)이
    그대로 넘어간다. 실제로 Host: evil.example.com에 200을 주던 경로다.
    """

    def _get_expecting_403(self, path, host):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get(path, host=host)
        self.addCleanup(cm.exception.close)
        self.assertEqual(cm.exception.code, 403)

    def test_foreign_host_on_api_is_403(self):
        (self.projects / "a.jsonl").write_text(line() + "\n", encoding="utf-8")
        self.start()
        self._get_expecting_403("/api/live", "evil.example.com")

    def test_foreign_host_on_static_file_is_403(self):
        # 정적 파일에도 건다 — API만 막으면 페이지를 실어 나를 수 있다
        self.start()
        self._get_expecting_403("/", "evil.example.com")

    def test_foreign_host_with_port_is_403(self):
        self.start()
        self._get_expecting_403("/api/summary?range=7d", "evil.example.com:8787")

    def test_empty_host_is_403(self):
        self.start()
        self._get_expecting_403("/api/live", "")

    def test_loopback_hosts_are_allowed(self):
        self.start()
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}",
                     f"[::1]:{self.port}", "127.0.0.1"):
            with self.subTest(host=host):
                self.assertEqual(self.get("/api/live", host=host)[0], 200)

    def test_normal_request_still_works(self):
        self.start()
        self.assertEqual(self.get("/")[0], 200)

    def test_head_with_foreign_host_is_403(self):
        """do_GET만 막던 시절엔 HEAD가 헤더만으로 존재·크기 오라클이었다."""
        self.start()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.head("/", host="evil.example.com")
        self.addCleanup(cm.exception.close)
        self.assertEqual(cm.exception.code, 403)

    def test_head_with_normal_host_is_200(self):
        self.start()
        self.assertEqual(self.head("/"), 200)


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


class TestConcurrency(ServerTestCase):
    def test_simultaneous_summary_and_live_both_succeed(self):
        """페이지가 뜰 때 두 요청이 동시에 나간다 — 실측으로 한쪽이 끊기던 경로."""
        import threading

        (self.projects / "a.jsonl").write_text(line() + "\n", encoding="utf-8")
        self.start()
        results = []

        def hit(path):
            try:
                results.append(self.get(path)[0])
            except Exception as e:  # noqa: BLE001
                results.append(repr(e))

        for _ in range(5):
            ts = [threading.Thread(target=hit, args=(p,))
                  for p in ("/api/summary?range=7d", "/api/live")]
            for t in ts:
                t.start()
            for t in ts:
                t.join()

        self.assertEqual(results, [200] * 10)


class TestApiErrorHandling(ServerTestCase):
    def test_failure_inside_summary_returns_500_not_a_dropped_connection(self):
        class Boom(server.State):
            def summary(self, range_key):
                raise RuntimeError("집계 실패")

        state = Boom(cfg=CFG, projects_root=self.root / "projects",
                     cache_path=self.root / "cache.json",
                     claude_json=self.claude_json, skill_roots=[])
        self.httpd = server.make_server(state, port=0, web_dir=self.web)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

        # 일부러 터뜨리는 경로다. print_exc는 지우지 않는다 — 동시성 버그를
        # 잡아낸 게 그 출력이다. 대신 이 테스트 동안만 막는다. 핸들러는 서버
        # 스레드에서 도니 테스트 쪽 stderr를 돌려봐야 소용이 없다.
        with mock.patch("pulse.server.traceback.print_exc") as printed:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get("/api/summary?range=7d")
            self.addCleanup(cm.exception.close)
            self.assertEqual(cm.exception.code, 500)
            self.assertEqual(json.loads(cm.exception.read())["error"],
                             "RuntimeError")
            printed.assert_called_once()


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
