"""localhost 전용 HTTP 서버. 조립해서 JSON으로 낼 뿐 계산하지 않는다."""
import json
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

from pulse import aggregate as agg
from pulse import inventory, live, pipeline

HOST = "127.0.0.1"

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
"""Host 헤더로 허용하는 이름.

127.0.0.1에만 묶는 것으로는 DNS 리바인딩을 막지 못한다. attacker.com이
127.0.0.1로 재해석되면 그 페이지는 브라우저에게 같은 출처가 되고, /api/live가
주는 세션 제목(Claude가 붙인 대화 제목)을 그대로 읽어간다. 실측으로
`Host: evil.example.com`에 200을 돌려주던 경로다.
"""


def host_allowed(host_header):
    """Host 헤더가 로컬을 가리키는가. 포트와 IPv6 대괄호를 벗겨 본다."""
    if not host_header:
        return False  # Host 없는 요청은 브라우저가 아니다 — 받지 않는다
    try:
        name = urlsplit("//" + host_header).hostname
    except ValueError:
        return False
    return (name or "").lower() in ALLOWED_HOSTS


class State:
    """요청마다 증분 갱신을 돌리고 결과를 만든다."""

    def __init__(self, cfg, projects_root, cache_path, claude_json=None,
                 skill_roots=None):
        self.cfg = cfg
        self.projects_root = projects_root
        self.cache_path = cache_path
        # `or`를 쓰면 빈 문자열 같은 falsy 주입값이 조용히 진짜 ~/.claude.json으로
        # 떨어진다 — 테스트가 실제 홈을 건드리지 않게 하는 규칙이 막으려는 바로
        # 그 일이다. skill_roots와 같은 `is None` 판정을 쓴다.
        self.claude_json = (inventory.DEFAULT_CLAUDE_JSON
                            if claude_json is None else claude_json)
        self.skill_roots = skill_roots
        self._lock = threading.Lock()

    def _partials(self):
        """갱신은 한 번에 하나만 돈다.

        페이지가 뜰 때 /api/summary와 /api/live가 동시에 나가는데, 둘 다 같은
        캐시 파일을 갱신한다. 직렬화하지 않으면 두 스레드가 같은 캐시를 두고
        경쟁한다 — 실측으로 로드마다 한쪽 요청이 끊겼다.
        """
        with self._lock:
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
            # 라우팅보다 먼저. 정적 파일에도 건다 — API만 막으면 리바인딩된
            # 페이지가 대시보드 자체를 실어 나를 수 있다.
            if not host_allowed(self.headers.get("Host")):
                return self._json({"error": "forbidden host"}, status=403)

            parsed = urlparse(self.path)
            route = parsed.path

            if route.startswith("/api/"):
                # 집계 중 예외가 나도 연결을 끊지 않는다 — 끊기면 브라우저에는
                # 원인 없는 실패로만 보이고, 5초마다 조용히 반복된다.
                try:
                    if route == "/api/summary":
                        q = parse_qs(parsed.query)
                        range_key = (q.get("range") or ["7d"])[0]
                        if range_key not in ("7d", "30d", "all"):
                            range_key = "7d"
                        return self._json(state.summary(range_key))
                    if route == "/api/live":
                        return self._json(state.live())
                    return self._json({"error": "not found"}, status=404)
                except Exception as e:  # noqa: BLE001 - 대시보드를 죽이지 않는다
                    traceback.print_exc()
                    return self._json({"error": type(e).__name__}, status=500)

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
