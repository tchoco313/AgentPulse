"""localhost 전용 HTTP 서버. 조립해서 JSON으로 낼 뿐 계산하지 않는다."""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pulse import aggregate as agg
from pulse import inventory, live, pipeline

HOST = "127.0.0.1"


class State:
    """요청마다 증분 갱신을 돌리고 결과를 만든다."""

    def __init__(self, cfg, projects_root, cache_path, claude_json=None,
                 skill_roots=None):
        self.cfg = cfg
        self.projects_root = projects_root
        self.cache_path = cache_path
        self.claude_json = claude_json or inventory.DEFAULT_CLAUDE_JSON
        self.skill_roots = skill_roots

    def _partials(self):
        partials, _ = pipeline.refresh(self.projects_root, self.cache_path)
        return partials

    def summary(self, range_key):
        since = pipeline.since_for(range_key)
        data = agg.merge(self._partials(), since=since)
        data.pop("sessions", None)  # 세션 세부는 /api/live 담당
        data["range"] = range_key
        data["since"] = since
        data["context_limit"] = self.cfg["context_limit"]
        data["unused"] = inventory.unused(
            data["tools"],
            inventory.installed_mcp_servers(self.claude_json),
            inventory.installed_skills(self.skill_roots),
        )
        return data

    def live(self):
        merged = agg.merge(self._partials())
        return {
            "sessions": live.active_sessions(merged["sessions"], self.cfg),
            "context_limit": self.cfg["context_limit"],
            "warn_threshold": self.cfg["warn_threshold"],
            "caution_threshold": self.cfg["caution_threshold"],
        }


def _handler_class(state, web_dir):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(web_dir), **kw)

        def log_message(self, *args):
            pass  # 터미널을 조용히

        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            route = parsed.path

            if route == "/api/summary":
                q = parse_qs(parsed.query)
                range_key = (q.get("range") or ["7d"])[0]
                if range_key not in ("7d", "30d", "all"):
                    range_key = "7d"
                return self._json(state.summary(range_key))

            if route == "/api/live":
                return self._json(state.live())

            if route.startswith("/api/"):
                return self._json({"error": "not found"}, status=404)

            return super().do_GET()

    return Handler


def make_server(state, port, web_dir):
    return ThreadingHTTPServer((HOST, port), _handler_class(state, Path(web_dir)))


def bind(state, port, web_dir, attempts=20):
    """포트가 막혀 있으면 다음 포트를 시도한다. 전부 막히면 마지막 OSError."""
    import errno

    last = None
    for candidate in range(port, port + attempts):
        try:
            return make_server(state, candidate, web_dir)
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            last = e
    raise last


def serve(state, port, web_dir):
    try:
        httpd = bind(state, port, web_dir)
    except OSError:
        raise SystemExit(f"{port}부터 20개 포트가 모두 사용 중입니다.")

    print(f"AgentPulse → http://{HOST}:{httpd.server_address[1]}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
