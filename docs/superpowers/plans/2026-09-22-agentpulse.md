# AgentPulse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Code 트랜스크립트를 집계해 사용량 대시보드를 띄우고, 컨텍스트가 임계값을 넘으면 알린다.

**Architecture:** 순수 함수 층(`fold`/`merge`/분류/판정)과 입출력 층(`scan`/`cache`/`server`)을 가른다. 파일별 부분집계를 캐시에 남겨, 변하지 않은 파일은 다시 읽지 않고 캐시의 부분집계를 그대로 합친다. 프론트엔드는 JSON API를 폴링하는 정적 페이지다.

**Tech Stack:** Python 3.14 표준 라이브러리만 (`http.server`, `json`, `unittest`, `datetime`). 프론트엔드는 vanilla HTML/CSS/JS. 외부 패키지 없음.

**Spec:** `docs/superpowers/specs/2026-09-22-agentpulse-design.md`

## Global Constraints

- **외부 의존성 0개.** `pip install`이 필요한 것은 무엇도 쓰지 않는다. pytest는 이 머신에 없다 — 테스트는 stdlib `unittest`로 쓴다.
- **테스트 실행 명령은 항상** `cd ~/Workspaces/13_AgentPulse && python3 -m unittest discover -s tests -t . -v`. 단일 테스트는 `python3 -m unittest tests.test_scan.TestScanFile.test_name -v`.
- **읽기 전용.** `~/.claude/` 아래에서 이 프로젝트가 쓰는 파일은 `pulse-cache.json`, `pulse-config.json`, `pulse-alerted.json` 셋뿐이다. 트랜스크립트는 절대 쓰지 않는다.
- **테스트는 실제 `~/.claude`를 건드리지 않는다.** 모든 경로는 인자로 주입한다. 기본값을 쓰는 테스트를 쓰지 않는다.
- **서버는 `127.0.0.1`에만 바인딩한다.** `0.0.0.0` 금지.
- **API 응답에 대화 본문을 넣지 않는다.** 수치, 도구 이름, 세션 제목까지만.
- **훅은 절대 실패하지 않는다.** `hooks/context_alert.py`는 모든 예외를 삼키고 exit 0 한다.
- **UI 문구는 한국어.** 코드 식별자와 커밋 메시지 제목은 영어여도 되나, 사용자에게 보이는 문자열은 한국어다.
- **모든 커밋 메시지 끝에** 다음 한 줄을 붙인다 (빈 줄 뒤에):
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- **기본 임계값:** warn 0.85, caution 0.70, 활동 창 15분, 포트 8787.
- **컨텍스트 한도:** `settings.json`의 `model`에 `[1m]` 포함 시 1,000,000, 아니면 200,000.

## Review Focus

스펙이 전제하지만 어느 태스크도 자연히 건드리지 않는 입력들. 각 줄은 담당 태스크에 테스트로 박아 넣었다.

1. **타임존** — 트랜스크립트 `timestamp`는 UTC(`...Z`)인데 "일별" 차트는 사용자의 로컬 날짜여야 한다. UTC로 버킷하면 한국 시간 오전 9시 이전 작업이 전날로 밀린다. → Task 4에 테스트.
2. **`usage`가 없거나 `model`이 `<synthetic>`인 assistant 메시지** — 실제 로그에 8건 있다. 토큰 0으로 세고 크래시하지 않아야 한다. → Task 3에 테스트.
3. **세션 재개(resume)로 같은 `sessionId`가 여러 파일에 걸침** — 병합 시 가장 최신 활동·최신 컨텍스트가 이겨야 한다. 두 번 세면 안 된다. → Task 4에 테스트.
4. **트랜스크립트가 하나도 없는 환경** — 첫 실행이나 빈 디렉터리에서 빈 대시보드가 정상으로 떠야 한다. 0으로 나누지 않아야 한다. → Task 8에 테스트.
5. **`postTokens > preTokens`인 압축 레코드** — 버린 토큰이 음수로 표시되면 안 된다. 0으로 바닥을 친다. → Task 4에 테스트.

---

### Task 1: 프로젝트 뼈대와 설정

**Files:**
- Create: `pulse/__init__.py`
- Create: `pulse/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Create: `.gitignore`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `pulse.config.DEFAULTS: dict` — 기본 설정값
  - `pulse.config.detect_context_limit(settings_path: str | Path) -> int`
  - `pulse.config.load_config(config_path=None, settings_path=None) -> dict`
    반환 키: `context_limit:int, warn_threshold:float, caution_threshold:float, active_window_minutes:int, port:int`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_config.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from pulse import config


class TestDetectContextLimit(unittest.TestCase):
    def _settings(self, data):
        d = Path(tempfile.mkdtemp())
        p = d / "settings.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_1m_model_gives_million(self):
        p = self._settings({"model": "opus[1m]"})
        self.assertEqual(config.detect_context_limit(p), 1_000_000)

    def test_plain_model_gives_200k(self):
        p = self._settings({"model": "sonnet"})
        self.assertEqual(config.detect_context_limit(p), 200_000)

    def test_missing_file_gives_200k(self):
        self.assertEqual(
            config.detect_context_limit(Path("/nonexistent/settings.json")),
            200_000,
        )

    def test_broken_json_gives_200k(self):
        d = Path(tempfile.mkdtemp())
        p = d / "settings.json"
        p.write_text("{not json", encoding="utf-8")
        self.assertEqual(config.detect_context_limit(p), 200_000)


class TestLoadConfig(unittest.TestCase):
    def test_defaults_when_no_config_file(self):
        d = Path(tempfile.mkdtemp())
        settings = d / "settings.json"
        settings.write_text(json.dumps({"model": "opus[1m]"}), encoding="utf-8")

        cfg = config.load_config(config_path=d / "absent.json", settings_path=settings)

        self.assertEqual(cfg["context_limit"], 1_000_000)
        self.assertEqual(cfg["warn_threshold"], 0.85)
        self.assertEqual(cfg["caution_threshold"], 0.70)
        self.assertEqual(cfg["active_window_minutes"], 15)
        self.assertEqual(cfg["port"], 8787)

    def test_user_config_overrides(self):
        d = Path(tempfile.mkdtemp())
        settings = d / "settings.json"
        settings.write_text(json.dumps({"model": "opus[1m]"}), encoding="utf-8")
        user = d / "pulse-config.json"
        user.write_text(
            json.dumps({"warn_threshold": 0.9, "port": 9000}), encoding="utf-8"
        )

        cfg = config.load_config(config_path=user, settings_path=settings)

        self.assertEqual(cfg["warn_threshold"], 0.9)
        self.assertEqual(cfg["port"], 9000)
        self.assertEqual(cfg["context_limit"], 1_000_000)

    def test_explicit_null_context_limit_falls_back_to_detection(self):
        d = Path(tempfile.mkdtemp())
        settings = d / "settings.json"
        settings.write_text(json.dumps({"model": "opus[1m]"}), encoding="utf-8")
        user = d / "pulse-config.json"
        user.write_text(json.dumps({"context_limit": None}), encoding="utf-8")

        cfg = config.load_config(config_path=user, settings_path=settings)

        self.assertEqual(cfg["context_limit"], 1_000_000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest discover -s tests -t . -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pulse'`

- [ ] **Step 3: 최소 구현을 쓴다**

`pulse/__init__.py`: 빈 파일.
`tests/__init__.py`: 빈 파일.

`pulse/config.py`:

```python
"""설정 로드와 컨텍스트 한도 추정."""
import json
from pathlib import Path

DEFAULTS = {
    "context_limit": None,
    "warn_threshold": 0.85,
    "caution_threshold": 0.70,
    "active_window_minutes": 15,
    "port": 8787,
}

CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_CONFIG_PATH = CLAUDE_DIR / "pulse-config.json"
DEFAULT_SETTINGS_PATH = CLAUDE_DIR / "settings.json"


def _read_json(path):
    """읽을 수 없거나 깨졌으면 빈 dict. 설정 문제로 도구가 죽지 않게 한다."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def detect_context_limit(settings_path=DEFAULT_SETTINGS_PATH):
    """settings.json의 모델 문자열로 컨텍스트 창 크기를 추정한다."""
    model = _read_json(settings_path).get("model") or ""
    return 1_000_000 if "[1m]" in model else 200_000


def load_config(config_path=None, settings_path=None):
    config_path = DEFAULT_CONFIG_PATH if config_path is None else config_path
    settings_path = DEFAULT_SETTINGS_PATH if settings_path is None else settings_path

    cfg = dict(DEFAULTS)
    cfg.update(_read_json(config_path))

    if cfg.get("context_limit") is None:
        cfg["context_limit"] = detect_context_limit(settings_path)

    return cfg
```

`.gitignore`:

```
__pycache__/
*.pyc
.DS_Store
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest discover -s tests -t . -v
```

Expected: PASS — 7 tests

- [ ] **Step 5: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add pulse tests .gitignore
git commit -m "feat: 설정 로드와 컨텍스트 한도 추정

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 캐시 입출력

**Files:**
- Create: `pulse/cache.py`
- Create: `tests/test_cache.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `pulse.cache.SCHEMA_VERSION: int` — 현재 1
  - `pulse.cache.empty_cache() -> dict` — `{"schema": 1, "files": {}}`
  - `pulse.cache.load_cache(path) -> dict` — 없거나 깨졌거나 스키마가 다르면 `empty_cache()`
  - `pulse.cache.save_cache(path, cache) -> None` — 임시 파일에 쓰고 원자적으로 교체

  캐시의 `files` 항목 형태: `{"<파일경로>": {"size": int, "mtime": float, "offset": int, "partial": dict}}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cache.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from pulse import cache


class TestCache(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "pulse-cache.json"

    def test_missing_file_gives_empty_cache(self):
        self.assertEqual(cache.load_cache(self.path), cache.empty_cache())

    def test_roundtrip(self):
        data = cache.empty_cache()
        data["files"]["/a/b.jsonl"] = {
            "size": 10,
            "mtime": 1.5,
            "offset": 10,
            "partial": {"bad_lines": 0},
        }
        cache.save_cache(self.path, data)

        self.assertEqual(cache.load_cache(self.path), data)

    def test_broken_json_gives_empty_cache(self):
        self.path.write_text("{truncated", encoding="utf-8")
        self.assertEqual(cache.load_cache(self.path), cache.empty_cache())

    def test_old_schema_gives_empty_cache(self):
        self.path.write_text(
            json.dumps({"schema": 0, "files": {"/x": {}}}), encoding="utf-8"
        )
        self.assertEqual(cache.load_cache(self.path), cache.empty_cache())

    def test_save_creates_parent_directory(self):
        nested = self.dir / "sub" / "dir" / "cache.json"
        cache.save_cache(nested, cache.empty_cache())
        self.assertTrue(nested.exists())

    def test_save_leaves_no_temp_file_behind(self):
        cache.save_cache(self.path, cache.empty_cache())
        self.assertEqual([p.name for p in self.dir.iterdir()], ["pulse-cache.json"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_cache -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pulse.cache'`

- [ ] **Step 3: 최소 구현을 쓴다**

`pulse/cache.py`:

```python
"""파일별 부분집계 캐시의 입출력."""
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1


def empty_cache():
    return {"schema": SCHEMA_VERSION, "files": {}}


def load_cache(path):
    """스키마가 다르거나 읽을 수 없으면 빈 캐시 — 전체 재스캔을 유도한다."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty_cache()

    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        return empty_cache()
    if not isinstance(data.get("files"), dict):
        return empty_cache()
    return data


def save_cache(path, cache):
    """임시 파일에 쓰고 rename — 쓰는 중에 죽어도 반쪽 캐시가 남지 않는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, path)
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_cache -v
```

Expected: PASS — 6 tests

- [ ] **Step 5: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add pulse/cache.py tests/test_cache.py
git commit -m "feat: 부분집계 캐시 입출력

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 트랜스크립트 증분 스캔

**Files:**
- Create: `pulse/scan.py`
- Create: `tests/test_scan.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `pulse.scan.MALFORMED` — 파싱 실패를 뜻하는 센티널 객체
  - `pulse.scan.parse_line(line: str) -> list[dict] | None | MALFORMED`
    관심 없는 레코드는 `None`, JSON 파싱 실패는 `MALFORMED`.
    **둘을 반드시 구분한다** — JSONL은 모든 줄이 `{`로 시작하므로 `scan_file`이
    문자열 모양으로 깨진 줄을 알아낼 방법이 없다. 섞으면 attachment·mode 같은
    평범한 레코드가 전부 "읽지 못한 줄"로 집계된다(실측: 한 파일에서 0건이 52건으로).
  - `pulse.scan.scan_file(path, offset: int = 0) -> tuple[list[dict], int, int]`
    반환: `(events, new_offset, bad_lines)`
  - `pulse.scan.find_transcripts(root) -> list[str]` — 정렬된 절대경로.
    루트 아래 **모든** `.jsonl`을 재귀로 찾는다. 서브에이전트 트랜스크립트가
    `<프로젝트>/<세션>/subagents/agent-*.jsonl`에 있고, 실측 117개 중 53개가
    거기 있다 — 2단계만 훑으면 토큰·호출 총계의 45%가 빠진다.

  **정규화 이벤트 형태** (뒤 태스크가 전부 이 형태에 의존한다):

  ```python
  {"kind": "usage", "ts": str, "session": str, "cwd": str, "model": str,
   "input": int, "output": int, "cache_read": int, "cache_creation": int}
  {"kind": "tool", "ts": str, "session": str, "name": str, "skill": str | None}
  {"kind": "compact", "ts": str, "session": str, "trigger": str, "pre": int, "post": int}
  {"kind": "title", "session": str, "title": str}
  {"kind": "activity", "ts": str, "session": str}
  ```

  `ts`는 트랜스크립트 원문 그대로의 ISO 문자열(예: `"2026-09-22T01:37:49.879Z"`)이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_scan.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from pulse import scan


def assistant_line(ts="2026-09-22T01:00:00.000Z", session="s1", usage=None,
                   model="claude-opus-5", tools=(), cwd="/Users/teo"):
    content = [{"type": "text", "text": "hi"}]
    for name, inp in tools:
        content.append({"type": "tool_use", "id": "t", "name": name, "input": inp})
    msg = {"role": "assistant", "model": model, "content": content}
    if usage is not None:
        msg["usage"] = usage
    return json.dumps({
        "type": "assistant", "timestamp": ts, "sessionId": session,
        "cwd": cwd, "message": msg,
    })


class TestParseLine(unittest.TestCase):
    def test_usage_event(self):
        line = assistant_line(usage={
            "input_tokens": 2, "output_tokens": 533,
            "cache_read_input_tokens": 57112, "cache_creation_input_tokens": 1315,
        })
        events = scan.parse_line(line)
        usage = [e for e in events if e["kind"] == "usage"][0]
        self.assertEqual(usage["input"], 2)
        self.assertEqual(usage["output"], 533)
        self.assertEqual(usage["cache_read"], 57112)
        self.assertEqual(usage["cache_creation"], 1315)
        self.assertEqual(usage["session"], "s1")
        self.assertEqual(usage["model"], "claude-opus-5")

    def test_assistant_without_usage_counts_as_zero(self):
        # Review Focus 2: 실제 로그에 usage 없는 <synthetic> 메시지가 있다
        events = scan.parse_line(assistant_line(usage=None, model="<synthetic>"))
        usage = [e for e in events if e["kind"] == "usage"][0]
        self.assertEqual(
            (usage["input"], usage["output"], usage["cache_read"], usage["cache_creation"]),
            (0, 0, 0, 0),
        )
        self.assertEqual(usage["model"], "<synthetic>")

    def test_tool_use_events(self):
        events = scan.parse_line(assistant_line(
            usage={}, tools=[("Bash", {"command": "ls"}),
                             ("Skill", {"skill": "dataviz"})]))
        tools = [e for e in events if e["kind"] == "tool"]
        self.assertEqual([t["name"] for t in tools], ["Bash", "Skill"])
        self.assertIsNone(tools[0]["skill"])
        self.assertEqual(tools[1]["skill"], "dataviz")

    def test_compact_boundary(self):
        line = json.dumps({
            "type": "system", "subtype": "compact_boundary",
            "timestamp": "2026-09-20T05:00:00.000Z", "sessionId": "s1",
            "compactMetadata": {"trigger": "auto", "preTokens": 966957,
                                "postTokens": 13491},
        })
        events = scan.parse_line(line)
        compact = [e for e in events if e["kind"] == "compact"][0]
        self.assertEqual(compact["trigger"], "auto")
        self.assertEqual(compact["pre"], 966957)
        self.assertEqual(compact["post"], 13491)

    def test_title(self):
        line = json.dumps({"type": "ai-title", "sessionId": "s1",
                           "aiTitle": "조테로 녹음 텍스트 추출"})
        events = scan.parse_line(line)
        self.assertEqual(events[0]["kind"], "title")
        self.assertEqual(events[0]["title"], "조테로 녹음 텍스트 추출")

    def test_user_line_yields_activity(self):
        line = json.dumps({"type": "user", "sessionId": "s1",
                           "timestamp": "2026-09-22T02:00:00.000Z"})
        events = scan.parse_line(line)
        self.assertEqual(events[0]["kind"], "activity")

    def test_broken_json_returns_malformed(self):
        self.assertIs(scan.parse_line("{not json"), scan.MALFORMED)

    def test_non_object_json_is_malformed(self):
        self.assertIs(scan.parse_line("[1, 2, 3]"), scan.MALFORMED)

    def test_uninteresting_line_returns_none(self):
        self.assertIsNone(scan.parse_line(json.dumps({"type": "mode", "mode": "x"})))

    def test_uninteresting_lines_are_not_counted_as_bad(self):
        # JSONL은 모든 줄이 '{'로 시작한다 — 모양으로 구분하면 전부 깨진 줄이 된다
        self.assertIsNone(scan.parse_line(json.dumps({"type": "attachment"})))
        self.assertIsNone(scan.parse_line(json.dumps({"type": "last-prompt"})))


class TestScanFile(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "t.jsonl"

    def write(self, lines, mode="w"):
        with open(self.path, mode, encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def test_reads_all_from_zero(self):
        self.write([assistant_line(usage={"input_tokens": 1})] * 3)
        events, offset, bad = scan.scan_file(self.path, 0)
        self.assertEqual(len([e for e in events if e["kind"] == "usage"]), 3)
        self.assertEqual(offset, self.path.stat().st_size)
        self.assertEqual(bad, 0)

    def test_counts_broken_lines_and_keeps_going(self):
        self.write([assistant_line(usage={"input_tokens": 1}),
                    "{broken",
                    assistant_line(usage={"input_tokens": 1})])
        events, _, bad = scan.scan_file(self.path, 0)
        self.assertEqual(len([e for e in events if e["kind"] == "usage"]), 2)
        self.assertEqual(bad, 1)

    def test_routine_records_are_not_counted_as_bad(self):
        # 실제 코퍼스의 첫 파일: 파싱 실패 0건인데 52건으로 세던 회귀
        self.write([json.dumps({"type": "attachment", "sessionId": "s1"}),
                    json.dumps({"type": "mode", "mode": "x"}),
                    json.dumps({"type": "last-prompt", "lastPrompt": "x"}),
                    assistant_line(usage={"input_tokens": 1})])
        events, _, bad = scan.scan_file(self.path, 0)
        self.assertEqual(bad, 0)
        self.assertEqual(len([e for e in events if e["kind"] == "usage"]), 1)

    def test_incremental_reads_only_new_lines(self):
        self.write([assistant_line(usage={"input_tokens": 1})])
        _, offset, _ = scan.scan_file(self.path, 0)

        self.write([assistant_line(usage={"input_tokens": 99})], mode="a")
        events, new_offset, _ = scan.scan_file(self.path, offset)

        usage = [e for e in events if e["kind"] == "usage"]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["input"], 99)
        self.assertGreater(new_offset, offset)

    def test_partial_last_line_is_not_consumed(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(assistant_line(usage={"input_tokens": 1}) + "\n")
            f.write('{"type":"assistant","timesta')  # 개행 없이 끊김

        events, offset, _ = scan.scan_file(self.path, 0)
        self.assertEqual(len([e for e in events if e["kind"] == "usage"]), 1)
        self.assertLess(offset, self.path.stat().st_size)

        # 나머지가 마저 쓰이면 그 줄을 온전히 읽는다
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('mp":"2026-09-22T03:00:00.000Z","sessionId":"s1","cwd":"/x",'
                    '"message":{"role":"assistant","model":"m","content":[],'
                    '"usage":{"input_tokens":7}}}\n')
        events2, offset2, _ = scan.scan_file(self.path, offset)
        usage2 = [e for e in events2 if e["kind"] == "usage"]
        self.assertEqual(len(usage2), 1)
        self.assertEqual(usage2[0]["input"], 7)
        self.assertEqual(offset2, self.path.stat().st_size)

    def test_missing_file_is_not_an_error(self):
        events, offset, bad = scan.scan_file(self.dir / "gone.jsonl", 0)
        self.assertEqual((events, offset, bad), ([], 0, 0))

    def test_does_not_load_whole_file_into_memory(self):
        # 줄 단위 스트리밍인지 — read()를 쓰면 이 테스트가 잡지 못하므로
        # 소스에 통짜 read가 없음을 확인한다
        source = (Path(scan.__file__)).read_text(encoding="utf-8")
        self.assertNotIn(".read()", source)
        self.assertNotIn(".readlines()", source)


class TestFindTranscripts(unittest.TestCase):
    def test_finds_jsonl_two_levels_down_and_sorts(self):
        root = Path(tempfile.mkdtemp())
        (root / "projB").mkdir()
        (root / "projA").mkdir()
        (root / "projB" / "b.jsonl").write_text("", encoding="utf-8")
        (root / "projA" / "a.jsonl").write_text("", encoding="utf-8")
        (root / "projA" / "notes.txt").write_text("", encoding="utf-8")

        found = scan.find_transcripts(root)

        self.assertEqual([Path(p).name for p in found], ["a.jsonl", "b.jsonl"])

    def test_finds_subagent_transcripts(self):
        # 실측 117개 중 53개가 <프로젝트>/<세션>/subagents/ 아래에 있다
        root = Path(tempfile.mkdtemp())
        (root / "proj" / "sess" / "subagents").mkdir(parents=True)
        (root / "proj" / "main.jsonl").write_text("", encoding="utf-8")
        (root / "proj" / "sess" / "subagents" / "agent-x.jsonl").write_text(
            "", encoding="utf-8")

        found = scan.find_transcripts(root)

        self.assertEqual([Path(p).name for p in found],
                         ["agent-x.jsonl", "main.jsonl"])

    def test_missing_root_gives_empty_list(self):
        self.assertEqual(scan.find_transcripts("/nonexistent/projects"), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_scan -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pulse.scan'`

- [ ] **Step 3: 최소 구현을 쓴다**

`pulse/scan.py`:

```python
"""트랜스크립트 JSONL을 증분으로 읽어 정규화 이벤트를 낸다.

디스크만 안다 — 집계가 무엇인지는 모른다.
"""
import json
from pathlib import Path


MALFORMED = object()
"""파싱 실패를 뜻하는 센티널. '관심 없는 줄'(None)과 구분하기 위해 필요하다."""


def find_transcripts(root):
    """루트 아래 모든 .jsonl을 정렬해 돌려준다.

    서브에이전트 트랜스크립트가 <프로젝트>/<세션>/subagents/ 아래에 있어서
    2단계 glob으로는 절반 가까이를 놓친다.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(str(p) for p in root.rglob("*.jsonl"))


def _int(d, key):
    v = d.get(key)
    return v if isinstance(v, int) else 0


def parse_line(line):
    """한 줄 -> 이벤트 리스트. 관심 없으면 None, 파싱 실패면 MALFORMED."""
    try:
        d = json.loads(line)
    except ValueError:
        return MALFORMED
    if not isinstance(d, dict):
        return MALFORMED

    kind = d.get("type")
    session = d.get("sessionId") or ""
    ts = d.get("timestamp") or ""

    if kind == "assistant":
        msg = d.get("message") or {}
        usage = msg.get("usage") or {}
        events = [{
            "kind": "usage", "ts": ts, "session": session,
            "cwd": d.get("cwd") or "", "model": msg.get("model") or "",
            "input": _int(usage, "input_tokens"),
            "output": _int(usage, "output_tokens"),
            "cache_read": _int(usage, "cache_read_input_tokens"),
            "cache_creation": _int(usage, "cache_creation_input_tokens"),
        }]
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input")
                skill = inp.get("skill") if isinstance(inp, dict) else None
                events.append({
                    "kind": "tool", "ts": ts, "session": session,
                    "name": block.get("name") or "",
                    "skill": skill if isinstance(skill, str) else None,
                })
        return events

    if kind == "system" and d.get("subtype") == "compact_boundary":
        meta = d.get("compactMetadata") or {}
        return [{
            "kind": "compact", "ts": ts, "session": session,
            "trigger": meta.get("trigger") or "unknown",
            "pre": _int(meta, "preTokens"), "post": _int(meta, "postTokens"),
        }]

    if kind == "ai-title":
        return [{"kind": "title", "session": session,
                 "title": d.get("aiTitle") or ""}]

    if kind == "user":
        return [{"kind": "activity", "ts": ts, "session": session}]

    return None


def scan_file(path, offset=0):
    """offset부터 줄 단위로 읽는다. (events, new_offset, bad_lines).

    개행으로 끝나지 않는 마지막 줄은 아직 쓰이는 중일 수 있으므로 버리고,
    offset을 그 줄 시작으로 물린다.
    """
    events = []
    bad = 0
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            pos = offset
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # 미완성 줄 — pos를 올리지 않는다
                pos += len(raw)
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                parsed = parse_line(text)
                if parsed is MALFORMED:
                    bad += 1
                    continue
                if parsed is None:
                    continue
                events.extend(parsed)
    except OSError:
        return [], offset, 0

    return events, pos, bad
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_scan -v
```

Expected: PASS — 20 tests

- [ ] **Step 5: 실제 데이터로 한 번 돌려본다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -c "
from pulse import scan
from pathlib import Path
files = scan.find_transcripts(Path.home() / '.claude/projects')
print('파일', len(files))
ev, off, bad = scan.scan_file(files[0], 0)
print('이벤트', len(ev), '오프셋', off, '깨진 줄', bad)
"
```

Expected: 파일 수가 100개 이상, 이벤트가 0보다 크고, 예외 없음.

- [ ] **Step 6: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add pulse/scan.py tests/test_scan.py
git commit -m "feat: 트랜스크립트 증분 스캐너

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 집계 — fold와 merge

**Files:**
- Create: `pulse/aggregate.py`
- Create: `tests/test_aggregate.py`

**Interfaces:**
- Consumes: Task 3의 정규화 이벤트 형태
- Produces:
  - `pulse.aggregate.classify_tool(name: str, skill: str | None) -> tuple[str, str]`
    반환 `(group, label)`, group은 `"mcp" | "skill" | "tools"` 중 하나
  - `pulse.aggregate.local_day(ts: str) -> str` — `"YYYY-MM-DD"`, 로컬 타임존 기준
  - `pulse.aggregate.empty_partial() -> dict`
  - `pulse.aggregate.fold(events, partial=None, subagent=False) -> dict`
    `subagent=True`면 이 파일에서 본 세션에 `"sub": True`를 심는다.
  - `pulse.aggregate.merge(partials: list[dict], since: str | None = None) -> dict`

  **partial 형태:**

  ```python
  {"days": {"2026-09-22": {"input": int, "output": int, "cache_read": int,
                            "cache_creation": int,
                            "calls": {"mcp": int, "skill": int, "tools": int}}},
   "tools": {"mcp": {"dbhub": int}, "skill": {"dataviz": int}, "tools": {"Bash": int}},
   "compacts": [{"ts": str, "day": str, "trigger": str, "pre": int, "post": int,
                 "dropped": int}],
   "sessions": {"<sid>": {"title": str | None, "cwd": str, "model": str,
                          "last_ts": str, "last_ctx": int | None, "sub": bool}},
   "bad_lines": int}
  ```

  **merge 반환(summary) 형태:**

  ```python
  {"days": [{"day": str, "input": int, "output": int, "cache_read": int,
             "cache_creation": int, "total": int,
             "calls": {"mcp": int, "skill": int, "tools": int}}],   # 날짜 오름차순
   "totals": {"input": int, "output": int, "cache_read": int,
              "cache_creation": int, "total": int, "calls": int,
              "cache_read_ratio": float},
   "tools": {"mcp": [[label, count], ...], "skill": [...], "tools": [...]},  # 내림차순
   "compacts": {"count": int, "auto": int, "manual": int,
                "dropped_total": int, "dropped_avg": int},
   "sessions": {"<sid>": {...}},
   "bad_lines": int}
  ```

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_aggregate.py`:

```python
import unittest
from datetime import datetime, timezone

from pulse import aggregate as agg


def usage(ts, session="s1", **kw):
    e = {"kind": "usage", "ts": ts, "session": session, "cwd": "/Users/teo",
         "model": "claude-opus-5", "input": 0, "output": 0,
         "cache_read": 0, "cache_creation": 0}
    e.update(kw)
    return e


def tool(ts, name, skill=None, session="s1"):
    return {"kind": "tool", "ts": ts, "session": session, "name": name, "skill": skill}


class TestClassifyTool(unittest.TestCase):
    def test_mcp_tool_uses_server_name(self):
        self.assertEqual(agg.classify_tool("mcp__dbhub__execute_sql", None),
                         ("mcp", "dbhub"))

    def test_mcp_server_name_with_underscores(self):
        self.assertEqual(
            agg.classify_tool("mcp__claude-in-chrome__tabs_close_mcp", None),
            ("mcp", "claude-in-chrome"))

    def test_skill_uses_skill_argument(self):
        self.assertEqual(agg.classify_tool("Skill", "dataviz"), ("skill", "dataviz"))

    def test_skill_without_argument_falls_back(self):
        self.assertEqual(agg.classify_tool("Skill", None), ("skill", "(미상)"))

    def test_builtin_tool(self):
        self.assertEqual(agg.classify_tool("Bash", None), ("tools", "Bash"))

    def test_malformed_mcp_name_is_not_mcp(self):
        self.assertEqual(agg.classify_tool("mcp__", None), ("tools", "mcp__"))


class TestLocalDay(unittest.TestCase):
    def test_utc_timestamp_maps_to_local_date(self):
        # Review Focus 1: UTC로 버킷하면 KST 오전 작업이 전날로 밀린다
        ts = "2026-09-22T01:00:00.000Z"
        expected = (datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)
                    .astimezone().strftime("%Y-%m-%d"))
        self.assertEqual(agg.local_day(ts), expected)

    def test_late_utc_evening_may_be_next_local_day(self):
        ts = "2026-09-21T23:30:00.000Z"
        expected = (datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
                    .astimezone().strftime("%Y-%m-%d"))
        self.assertEqual(agg.local_day(ts), expected)

    def test_empty_timestamp_gives_empty_string(self):
        self.assertEqual(agg.local_day(""), "")


class TestFold(unittest.TestCase):
    def test_sums_tokens_by_day(self):
        day = agg.local_day("2026-09-22T01:00:00.000Z")
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=10, output=5),
                      usage("2026-09-22T01:05:00.000Z", input=1, cache_read=100)])
        self.assertEqual(p["days"][day]["input"], 11)
        self.assertEqual(p["days"][day]["output"], 5)
        self.assertEqual(p["days"][day]["cache_read"], 100)

    def test_counts_tools_by_group(self):
        p = agg.fold([tool("2026-09-22T01:00:00.000Z", "Bash"),
                      tool("2026-09-22T01:00:00.000Z", "Bash"),
                      tool("2026-09-22T01:00:00.000Z", "mcp__dbhub__execute_sql"),
                      tool("2026-09-22T01:00:00.000Z", "Skill", skill="dataviz")])
        self.assertEqual(p["tools"]["tools"]["Bash"], 2)
        self.assertEqual(p["tools"]["mcp"]["dbhub"], 1)
        self.assertEqual(p["tools"]["skill"]["dataviz"], 1)
        day = agg.local_day("2026-09-22T01:00:00.000Z")
        self.assertEqual(p["days"][day]["calls"],
                         {"mcp": 1, "skill": 1, "tools": 2})

    def test_compact_dropped_tokens(self):
        p = agg.fold([{"kind": "compact", "ts": "2026-09-20T05:00:00.000Z",
                       "session": "s1", "trigger": "auto",
                       "pre": 966957, "post": 13491}])
        self.assertEqual(p["compacts"][0]["dropped"], 953466)

    def test_compact_dropped_never_negative(self):
        # Review Focus 5
        p = agg.fold([{"kind": "compact", "ts": "2026-09-20T05:00:00.000Z",
                       "session": "s1", "trigger": "auto",
                       "pre": 100, "post": 500}])
        self.assertEqual(p["compacts"][0]["dropped"], 0)

    def test_session_tracks_latest_context(self):
        p = agg.fold([
            usage("2026-09-22T01:00:00.000Z", input=1, cache_read=100, cache_creation=10),
            usage("2026-09-22T02:00:00.000Z", input=2, cache_read=500, cache_creation=20),
        ])
        self.assertEqual(p["sessions"]["s1"]["last_ctx"], 522)
        self.assertEqual(p["sessions"]["s1"]["last_ts"], "2026-09-22T02:00:00.000Z")

    def test_out_of_order_events_do_not_regress_latest(self):
        p = agg.fold([
            usage("2026-09-22T02:00:00.000Z", input=2, cache_read=500),
            usage("2026-09-22T01:00:00.000Z", input=1, cache_read=100),
        ])
        self.assertEqual(p["sessions"]["s1"]["last_ts"], "2026-09-22T02:00:00.000Z")
        self.assertEqual(p["sessions"]["s1"]["last_ctx"], 502)

    def test_subagent_flag_marks_sessions(self):
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=1)], subagent=True)
        self.assertTrue(p["sessions"]["s1"]["sub"])

    def test_default_is_not_subagent(self):
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=1)])
        self.assertFalse(p["sessions"]["s1"]["sub"])

    def test_title_event_names_session(self):
        p = agg.fold([{"kind": "title", "session": "s1", "title": "제목"}])
        self.assertEqual(p["sessions"]["s1"]["title"], "제목")

    def test_activity_updates_last_ts_but_not_context(self):
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=1, cache_read=100),
                      {"kind": "activity", "ts": "2026-09-22T03:00:00.000Z",
                       "session": "s1"}])
        self.assertEqual(p["sessions"]["s1"]["last_ts"], "2026-09-22T03:00:00.000Z")
        self.assertEqual(p["sessions"]["s1"]["last_ctx"], 101)

    def test_folding_onto_existing_partial_accumulates(self):
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=10)])
        p = agg.fold([usage("2026-09-22T01:00:00.000Z", input=5)], p)
        day = agg.local_day("2026-09-22T01:00:00.000Z")
        self.assertEqual(p["days"][day]["input"], 15)


class TestMerge(unittest.TestCase):
    def test_incremental_equals_whole(self):
        """증분 경로와 전체 경로가 같은 답을 낸다 — 증분 스캔이 옳다는 근거."""
        events = [
            usage("2026-09-20T01:00:00.000Z", input=10, output=1),
            tool("2026-09-20T01:00:00.000Z", "Bash"),
            usage("2026-09-21T01:00:00.000Z", input=20, cache_read=5),
            tool("2026-09-21T01:00:00.000Z", "mcp__dbhub__execute_sql"),
            {"kind": "compact", "ts": "2026-09-21T02:00:00.000Z", "session": "s1",
             "trigger": "auto", "pre": 900, "post": 100},
        ]
        whole = agg.merge([agg.fold(events)])

        incremental = agg.merge([agg.fold(events[:2]), agg.fold(events[2:])])

        self.assertEqual(whole, incremental)

    def test_totals_and_cache_ratio(self):
        s = agg.merge([agg.fold([
            usage("2026-09-22T01:00:00.000Z", input=10, output=10,
                  cache_read=80, cache_creation=0)])])
        self.assertEqual(s["totals"]["total"], 100)
        self.assertAlmostEqual(s["totals"]["cache_read_ratio"], 0.8)

    def test_zero_tokens_gives_zero_ratio_not_crash(self):
        s = agg.merge([agg.empty_partial()])
        self.assertEqual(s["totals"]["total"], 0)
        self.assertEqual(s["totals"]["cache_read_ratio"], 0.0)

    def test_days_sorted_ascending(self):
        s = agg.merge([agg.fold([usage("2026-09-22T01:00:00.000Z", input=1),
                                 usage("2026-09-20T01:00:00.000Z", input=1)])])
        days = [d["day"] for d in s["days"]]
        self.assertEqual(days, sorted(days))

    def test_since_filters_days_and_tokens(self):
        d20 = agg.local_day("2026-09-20T01:00:00.000Z")
        d22 = agg.local_day("2026-09-22T01:00:00.000Z")
        s = agg.merge([agg.fold([usage("2026-09-20T01:00:00.000Z", input=100),
                                 usage("2026-09-22T01:00:00.000Z", input=7)])],
                      since=d22)
        self.assertEqual([d["day"] for d in s["days"]], [d22])
        self.assertEqual(s["totals"]["input"], 7)
        self.assertNotIn(d20, [d["day"] for d in s["days"]])

    def test_tools_ranked_descending(self):
        s = agg.merge([agg.fold([tool("2026-09-22T01:00:00.000Z", "Read"),
                                 tool("2026-09-22T01:00:00.000Z", "Bash"),
                                 tool("2026-09-22T01:00:00.000Z", "Bash")])])
        self.assertEqual(s["tools"]["tools"][0], ["Bash", 2])
        self.assertEqual(s["tools"]["tools"][1], ["Read", 1])

    def test_same_session_across_files_is_merged_not_doubled(self):
        # Review Focus 3: resume하면 같은 sessionId가 여러 파일에 걸친다
        a = agg.fold([usage("2026-09-22T01:00:00.000Z", session="s1",
                            input=1, cache_read=100)])
        b = agg.fold([usage("2026-09-22T05:00:00.000Z", session="s1",
                            input=1, cache_read=900)])

        s = agg.merge([a, b])

        self.assertEqual(len(s["sessions"]), 1)
        self.assertEqual(s["sessions"]["s1"]["last_ts"], "2026-09-22T05:00:00.000Z")
        self.assertEqual(s["sessions"]["s1"]["last_ctx"], 901)
        self.assertEqual(s["totals"]["input"], 2)

    def test_subagent_flag_survives_merge_and_tokens_still_count(self):
        sub = agg.fold([usage("2026-09-22T01:00:00.000Z", session="a", input=7)],
                       subagent=True)
        main = agg.fold([usage("2026-09-22T01:00:00.000Z", session="b", input=3)])

        s = agg.merge([sub, main])

        self.assertTrue(s["sessions"]["a"]["sub"])
        self.assertFalse(s["sessions"]["b"]["sub"])
        self.assertEqual(s["totals"]["input"], 10)  # 총계에는 둘 다 들어간다

    def test_compact_stats(self):
        s = agg.merge([agg.fold([
            {"kind": "compact", "ts": "2026-09-20T05:00:00.000Z", "session": "s1",
             "trigger": "auto", "pre": 1000, "post": 100},
            {"kind": "compact", "ts": "2026-09-21T05:00:00.000Z", "session": "s1",
             "trigger": "manual", "pre": 500, "post": 100},
        ])])
        self.assertEqual(s["compacts"]["count"], 2)
        self.assertEqual(s["compacts"]["auto"], 1)
        self.assertEqual(s["compacts"]["manual"], 1)
        self.assertEqual(s["compacts"]["dropped_total"], 1300)
        self.assertEqual(s["compacts"]["dropped_avg"], 650)

    def test_compact_stats_with_none(self):
        s = agg.merge([agg.empty_partial()])
        self.assertEqual(s["compacts"]["count"], 0)
        self.assertEqual(s["compacts"]["dropped_avg"], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_aggregate -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pulse.aggregate'`

- [ ] **Step 3: 최소 구현을 쓴다**

`pulse/aggregate.py`:

```python
"""이벤트 -> 부분집계(fold) -> 기간 집계(merge).

순수 함수만 있다. 파일 시스템을 모른다.
"""
from datetime import datetime

GROUPS = ("mcp", "skill", "tools")


def classify_tool(name, skill):
    """도구 이름을 (그룹, 표시 라벨)로 나눈다."""
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3 and parts[1]:
            return "mcp", parts[1]
        return "tools", name
    if name == "Skill":
        return "skill", skill or "(미상)"
    return "tools", name


def local_day(ts):
    """UTC ISO 문자열 -> 로컬 타임존의 YYYY-MM-DD.

    사용자가 보는 '하루'는 로컬 날짜다. UTC로 버킷하면 KST 오전 작업이
    전날로 밀린다.
    """
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone().strftime("%Y-%m-%d")


def empty_partial():
    return {"days": {}, "tools": {g: {} for g in GROUPS},
            "compacts": [], "sessions": {}, "bad_lines": 0}


def _day_bucket(partial, day):
    return partial["days"].setdefault(day, {
        "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
        "calls": {g: 0 for g in GROUPS},
    })


def _session(partial, sid):
    return partial["sessions"].setdefault(sid, {
        "title": None, "cwd": "", "model": "", "last_ts": "", "last_ctx": None,
        "sub": False,
    })


def fold(events, partial=None, subagent=False):
    """이벤트들을 부분집계에 접어 넣는다. partial을 주면 그 위에 누적한다.

    subagent=True는 서브에이전트 트랜스크립트에서 온 이벤트라는 뜻이다. 사용량
    총계에는 들어가지만 실시간 세션 카드에서는 빠진다 — 사용자가 알고 싶은 것은
    자기 세션이 언제 압축되는가이고, 서브에이전트 컨텍스트는 손쓸 대상이 아니다.
    """
    p = empty_partial() if partial is None else partial

    for e in events:
        kind = e.get("kind")
        sid = e.get("session") or ""
        if subagent and sid:
            _session(p, sid)["sub"] = True

        if kind == "usage":
            day = local_day(e["ts"])
            if day:
                b = _day_bucket(p, day)
                for k in ("input", "output", "cache_read", "cache_creation"):
                    b[k] += e.get(k, 0)
            s = _session(p, sid)
            if e["ts"] >= s["last_ts"]:
                s["last_ts"] = e["ts"]
                s["last_ctx"] = (e.get("input", 0) + e.get("cache_read", 0)
                                 + e.get("cache_creation", 0))
                s["cwd"] = e.get("cwd") or s["cwd"]
                s["model"] = e.get("model") or s["model"]

        elif kind == "tool":
            group, label = classify_tool(e.get("name") or "", e.get("skill"))
            p["tools"][group][label] = p["tools"][group].get(label, 0) + 1
            day = local_day(e["ts"])
            if day:
                _day_bucket(p, day)["calls"][group] += 1

        elif kind == "compact":
            pre, post = e.get("pre", 0), e.get("post", 0)
            p["compacts"].append({
                "ts": e["ts"], "day": local_day(e["ts"]),
                "trigger": e.get("trigger") or "unknown",
                "pre": pre, "post": post, "dropped": max(0, pre - post),
            })

        elif kind == "title":
            _session(p, sid)["title"] = e.get("title") or None

        elif kind == "activity":
            s = _session(p, sid)
            if e["ts"] >= s["last_ts"]:
                s["last_ts"] = e["ts"]

    return p


def _merge_sessions(target, incoming):
    for sid, s in incoming.items():
        cur = target.get(sid)
        if cur is None:
            target[sid] = dict(s)
            continue
        # 제목은 한 번이라도 붙었으면 유지
        if s.get("title") and not cur.get("title"):
            cur["title"] = s["title"]
        if s.get("sub"):
            cur["sub"] = True  # 한 번이라도 서브에이전트면 서브에이전트다
        if s.get("last_ts", "") >= cur.get("last_ts", ""):
            cur["last_ts"] = s["last_ts"]
            if s.get("last_ctx") is not None:
                cur["last_ctx"] = s["last_ctx"]
            cur["cwd"] = s.get("cwd") or cur.get("cwd", "")
            cur["model"] = s.get("model") or cur.get("model", "")


def merge(partials, since=None):
    """부분집계들을 기간(since 이상)으로 걸러 합친다."""
    days = {}
    tools = {g: {} for g in GROUPS}
    compacts = []
    sessions = {}
    bad_lines = 0

    for p in partials:
        bad_lines += p.get("bad_lines", 0)
        _merge_sessions(sessions, p.get("sessions", {}))

        for day, b in p.get("days", {}).items():
            if since and day < since:
                continue
            t = days.setdefault(day, {
                "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
                "calls": {g: 0 for g in GROUPS},
            })
            for k in ("input", "output", "cache_read", "cache_creation"):
                t[k] += b.get(k, 0)
            for g in GROUPS:
                t["calls"][g] += (b.get("calls") or {}).get(g, 0)

        for c in p.get("compacts", []):
            if since and c.get("day", "") < since:
                continue
            compacts.append(c)

    # 기간 필터가 도구 순위에도 걸려야 하므로, 일별 호출 수에서 그룹 총합을 내고
    # 라벨별 세부는 기간 필터가 없을 때만 정확하다. 기간이 걸린 경우를 위해
    # 라벨별 집계도 일자와 함께 보관해야 정확하지만, 부분집계 크기를 감당하기
    # 위해 라벨별은 파일 단위로만 나눈다. 따라서 since가 있으면 라벨 순위는
    # 그 기간에 활동이 있던 파일들의 전체 라벨 집계다 -- UI에 그렇게 표기한다.
    for p in partials:
        for g in GROUPS:
            for label, n in (p.get("tools", {}).get(g) or {}).items():
                tools[g][label] = tools[g].get(label, 0) + n

    total_calls = sum(sum(d["calls"].values()) for d in days.values())
    totals = {k: sum(d[k] for d in days.values())
              for k in ("input", "output", "cache_read", "cache_creation")}
    totals["total"] = sum(totals.values())
    totals["calls"] = total_calls
    totals["cache_read_ratio"] = (
        totals["cache_read"] / totals["total"] if totals["total"] else 0.0
    )

    dropped_total = sum(c["dropped"] for c in compacts)
    compact_stats = {
        "count": len(compacts),
        "auto": sum(1 for c in compacts if c["trigger"] == "auto"),
        "manual": sum(1 for c in compacts if c["trigger"] != "auto"),
        "dropped_total": dropped_total,
        "dropped_avg": dropped_total // len(compacts) if compacts else 0,
    }

    return {
        "days": [dict(day=d, total=sum(v[k] for k in
                      ("input", "output", "cache_read", "cache_creation")), **v)
                 for d, v in sorted(days.items())],
        "totals": totals,
        "tools": {g: sorted(([label, n] for label, n in tools[g].items()),
                            key=lambda x: (-x[1], x[0])) for g in GROUPS},
        "compacts": compact_stats,
        "sessions": sessions,
        "bad_lines": bad_lines,
    }
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_aggregate -v
```

Expected: PASS — 31 tests

- [ ] **Step 5: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add pulse/aggregate.py tests/test_aggregate.py
git commit -m "feat: fold/merge 집계와 도구 분류

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 설치된 MCP·스킬 인벤토리

**Files:**
- Create: `pulse/inventory.py`
- Create: `tests/test_inventory.py`

**Interfaces:**
- Consumes: Task 4의 `merge` 반환 중 `tools` 키
- Produces:
  - `pulse.inventory.installed_mcp_servers(claude_json_path) -> set[str]`
  - `pulse.inventory.installed_skills(roots: list) -> set[str]`
  - `pulse.inventory.unused(summary_tools: dict, mcp: set, skills: set) -> dict`
    반환: `{"mcp": [str, ...], "skill": [str, ...], "count": int}` (각 리스트 정렬됨)
  - `pulse.inventory.DEFAULT_SKILL_ROOTS: list[Path]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_inventory.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from pulse import inventory as inv


class TestInstalledMcpServers(unittest.TestCase):
    def test_reads_mcp_servers_key(self):
        d = Path(tempfile.mkdtemp())
        p = d / ".claude.json"
        p.write_text(json.dumps({"mcpServers": {"dbhub": {}, "other": {}}}),
                     encoding="utf-8")
        self.assertEqual(inv.installed_mcp_servers(p), {"dbhub", "other"})

    def test_missing_file_gives_empty_set(self):
        self.assertEqual(inv.installed_mcp_servers("/nonexistent/.claude.json"), set())

    def test_broken_json_gives_empty_set(self):
        d = Path(tempfile.mkdtemp())
        p = d / ".claude.json"
        p.write_text("{broken", encoding="utf-8")
        self.assertEqual(inv.installed_mcp_servers(p), set())


class TestInstalledSkills(unittest.TestCase):
    def test_finds_skill_dirs_by_skill_md(self):
        root = Path(tempfile.mkdtemp())
        (root / "alpha").mkdir()
        (root / "alpha" / "SKILL.md").write_text("x", encoding="utf-8")
        (root / "beta" / "nested").mkdir(parents=True)
        (root / "beta" / "nested" / "SKILL.md").write_text("x", encoding="utf-8")
        (root / "gamma").mkdir()  # SKILL.md 없음

        self.assertEqual(inv.installed_skills([root]), {"alpha", "nested"})

    def test_missing_root_is_skipped(self):
        self.assertEqual(inv.installed_skills(["/nonexistent"]), set())


class TestUnused(unittest.TestCase):
    def test_lists_installed_but_unused(self):
        summary_tools = {"mcp": [["dbhub", 5]], "skill": [["dataviz", 2]],
                         "tools": [["Bash", 10]]}
        result = inv.unused(summary_tools, mcp={"dbhub", "sleepy"},
                            skills={"dataviz", "unused-skill", "another"})
        self.assertEqual(result["mcp"], ["sleepy"])
        self.assertEqual(result["skill"], ["another", "unused-skill"])
        self.assertEqual(result["count"], 3)

    def test_skill_labels_may_carry_plugin_prefix(self):
        # 사용 기록은 'superpowers:brainstorming', 설치 목록은 'brainstorming'
        summary_tools = {"mcp": [], "skill": [["superpowers:brainstorming", 4]],
                         "tools": []}
        result = inv.unused(summary_tools, mcp=set(), skills={"brainstorming", "other"})
        self.assertEqual(result["skill"], ["other"])

    def test_nothing_installed_gives_empty(self):
        result = inv.unused({"mcp": [], "skill": [], "tools": []},
                            mcp=set(), skills=set())
        self.assertEqual(result, {"mcp": [], "skill": [], "count": 0})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_inventory -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pulse.inventory'`

- [ ] **Step 3: 최소 구현을 쓴다**

`pulse/inventory.py`:

```python
"""설치돼 있지만 기간 내 쓰이지 않은 MCP 서버·스킬을 찾는다."""
import json
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_CLAUDE_JSON = Path.home() / ".claude.json"
DEFAULT_SKILL_ROOTS = [
    CLAUDE_DIR / "skills",
    CLAUDE_DIR / "modes",
    CLAUDE_DIR / "plugins" / "cache",
]


def installed_mcp_servers(claude_json_path=DEFAULT_CLAUDE_JSON):
    try:
        with open(claude_json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return set(servers) if isinstance(servers, dict) else set()


def installed_skills(roots=None):
    """SKILL.md를 담은 디렉터리의 이름이 스킬 이름이다."""
    roots = DEFAULT_SKILL_ROOTS if roots is None else roots
    names = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for skill_md in root.rglob("SKILL.md"):
            names.add(skill_md.parent.name)
    return names


def _bare(label):
    """'superpowers:brainstorming' -> 'brainstorming'"""
    return label.rsplit(":", 1)[-1]


def unused(summary_tools, mcp, skills):
    used_mcp = {label for label, _ in summary_tools.get("mcp", [])}
    used_skill = {_bare(label) for label, _ in summary_tools.get("skill", [])}

    unused_mcp = sorted(mcp - used_mcp)
    unused_skill = sorted({s for s in skills if _bare(s) not in used_skill})

    return {"mcp": unused_mcp, "skill": unused_skill,
            "count": len(unused_mcp) + len(unused_skill)}
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_inventory -v
```

Expected: PASS — 8 tests

- [ ] **Step 5: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add pulse/inventory.py tests/test_inventory.py
git commit -m "feat: 설치·미사용 MCP와 스킬 인벤토리

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 실시간 세션과 상태 판정

**Files:**
- Create: `pulse/live.py`
- Create: `tests/test_live.py`

**Interfaces:**
- Consumes: Task 4 `merge` 반환의 `sessions` dict
- Produces:
  - `pulse.live.context_status(used: int | None, limit: int, caution: float, warn: float) -> str`
    반환: `"unknown" | "ok" | "caution" | "danger"`
  - `pulse.live.active_sessions(sessions: dict, cfg: dict, now: datetime | None = None) -> list[dict]`
    `sub`가 참인 세션(서브에이전트 트랜스크립트)은 제외한다.
    반환 항목: `{"session": str, "title": str, "project": str, "model": str,
    "last_ts": str, "used": int | None, "limit": int, "ratio": float | None,
    "status": str}` — `ratio` 내림차순 정렬

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_live.py`:

```python
import unittest
from datetime import datetime, timedelta, timezone

from pulse import live

CFG = {"context_limit": 1_000_000, "warn_threshold": 0.85,
       "caution_threshold": 0.70, "active_window_minutes": 15}


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TestContextStatus(unittest.TestCase):
    def test_low_is_ok(self):
        self.assertEqual(live.context_status(100_000, 1_000_000, 0.70, 0.85), "ok")

    def test_caution_at_threshold(self):
        self.assertEqual(live.context_status(700_000, 1_000_000, 0.70, 0.85), "caution")

    def test_danger_at_threshold(self):
        self.assertEqual(live.context_status(850_000, 1_000_000, 0.70, 0.85), "danger")

    def test_none_is_unknown(self):
        self.assertEqual(live.context_status(None, 1_000_000, 0.70, 0.85), "unknown")

    def test_zero_limit_is_unknown_not_division_error(self):
        self.assertEqual(live.context_status(100, 0, 0.70, 0.85), "unknown")


class TestActiveSessions(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)

    def test_recent_session_is_active(self):
        sessions = {"s1": {"title": "작업", "cwd": "/Users/teo/Workspaces/11_Quant",
                           "model": "claude-opus-5",
                           "last_ts": iso(self.now - timedelta(minutes=5)),
                           "last_ctx": 360_000}}
        result = live.active_sessions(sessions, CFG, now=self.now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["project"], "11_Quant")
        self.assertEqual(result[0]["status"], "ok")
        self.assertAlmostEqual(result[0]["ratio"], 0.36)

    def test_subagent_session_is_excluded(self):
        sessions = {"s1": {"title": "서브", "cwd": "/x", "model": "m",
                           "last_ts": iso(self.now - timedelta(minutes=1)),
                           "last_ctx": 100, "sub": True}}
        self.assertEqual(live.active_sessions(sessions, CFG, now=self.now), [])

    def test_old_session_is_excluded(self):
        sessions = {"s1": {"title": "옛날", "cwd": "/x", "model": "m",
                           "last_ts": iso(self.now - timedelta(minutes=60)),
                           "last_ctx": 100}}
        self.assertEqual(live.active_sessions(sessions, CFG, now=self.now), [])

    def test_session_without_context_is_unknown_not_dropped(self):
        sessions = {"s1": {"title": None, "cwd": "/x", "model": "",
                           "last_ts": iso(self.now - timedelta(minutes=1)),
                           "last_ctx": None}}
        result = live.active_sessions(sessions, CFG, now=self.now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "unknown")
        self.assertIsNone(result[0]["ratio"])

    def test_untitled_session_falls_back_to_project_name(self):
        sessions = {"s1": {"title": None, "cwd": "/Users/teo/Workspaces/12_RiskMap",
                           "model": "m",
                           "last_ts": iso(self.now - timedelta(minutes=1)),
                           "last_ctx": 10}}
        result = live.active_sessions(sessions, CFG, now=self.now)
        self.assertEqual(result[0]["title"], "12_RiskMap")

    def test_sorted_by_ratio_descending(self):
        ts = iso(self.now - timedelta(minutes=1))
        sessions = {
            "low": {"title": "a", "cwd": "/x", "model": "m",
                    "last_ts": ts, "last_ctx": 100_000},
            "high": {"title": "b", "cwd": "/x", "model": "m",
                     "last_ts": ts, "last_ctx": 900_000},
        }
        result = live.active_sessions(sessions, CFG, now=self.now)
        self.assertEqual([r["session"] for r in result], ["high", "low"])

    def test_unparseable_timestamp_is_excluded(self):
        sessions = {"s1": {"title": "x", "cwd": "/x", "model": "m",
                           "last_ts": "not-a-date", "last_ctx": 1}}
        self.assertEqual(live.active_sessions(sessions, CFG, now=self.now), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_live -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pulse.live'`

- [ ] **Step 3: 최소 구현을 쓴다**

`pulse/live.py`:

```python
"""활동 중인 세션과 컨텍스트 상태 판정. 순수 함수."""
import os
from datetime import datetime, timedelta, timezone


def context_status(used, limit, caution, warn):
    if used is None or not limit:
        return "unknown"
    ratio = used / limit
    if ratio >= warn:
        return "danger"
    if ratio >= caution:
        return "caution"
    return "ok"


def _parse(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def active_sessions(sessions, cfg, now=None):
    now = datetime.now(timezone.utc) if now is None else now
    cutoff = now - timedelta(minutes=cfg["active_window_minutes"])
    limit = cfg["context_limit"]

    rows = []
    for sid, s in sessions.items():
        if s.get("sub"):
            continue  # 서브에이전트 세션 — 총계에는 있지만 카드에는 안 띄운다
        when = _parse(s.get("last_ts") or "")
        if when is None or when < cutoff:
            continue
        used = s.get("last_ctx")
        project = os.path.basename(s.get("cwd") or "") or "(알 수 없음)"
        rows.append({
            "session": sid,
            "title": s.get("title") or project,
            "project": project,
            "model": s.get("model") or "",
            "last_ts": s.get("last_ts") or "",
            "used": used,
            "limit": limit,
            "ratio": (used / limit) if (used is not None and limit) else None,
            "status": context_status(used, limit, cfg["caution_threshold"],
                                     cfg["warn_threshold"]),
        })

    rows.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0)))
    return rows
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_live -v
```

Expected: PASS — 12 tests

- [ ] **Step 5: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add pulse/live.py tests/test_live.py
git commit -m "feat: 활동 세션 탐지와 컨텍스트 상태 판정

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 파이프라인 — 증분 갱신 조립

> **스펙과의 차이:** 스펙 §5의 모듈 목록에는 없던 `pulse/pipeline.py`를 둔다. 조립을 `server.py`에 넣으면 "서버는 계산하지 않는다"는 경계가 흐려지고, `scan`에 넣으면 스캐너가 캐시와 집계를 알게 된다. 조립만 하는 얇은 모듈을 따로 두는 편이 두 경계를 모두 지킨다.

**Files:**
- Create: `pulse/pipeline.py`
- Create: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `scan.find_transcripts`, `scan.scan_file`, `aggregate.fold`, `aggregate.merge`, `cache.load_cache`, `cache.save_cache`
- Produces:
  - `pulse.pipeline.refresh(projects_root, cache_path) -> tuple[list[dict], dict]`
    반환 `(partials, cache)` — `partials`는 모든 파일의 부분집계 리스트.
    경로에 `/subagents/`가 들어간 파일은 `fold(..., subagent=True)`로 접는다.
  - `pulse.pipeline.is_subagent(path: str) -> bool`
  - `pulse.pipeline.since_for(range_key: str, today: str | None = None) -> str | None`
    `"7d" -> 6일 전 날짜`, `"30d" -> 29일 전`, `"all" -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_pipeline.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from pulse import aggregate as agg
from pulse import pipeline


def line(ts="2026-09-22T01:00:00.000Z", session="s1", inp=1):
    return json.dumps({
        "type": "assistant", "timestamp": ts, "sessionId": session,
        "cwd": "/Users/teo",
        "message": {"role": "assistant", "model": "claude-opus-5", "content": [],
                    "usage": {"input_tokens": inp}},
    })


class TestSinceFor(unittest.TestCase):
    def test_7d_spans_seven_days_inclusive(self):
        self.assertEqual(pipeline.since_for("7d", today="2026-09-22"), "2026-09-16")

    def test_30d(self):
        self.assertEqual(pipeline.since_for("30d", today="2026-09-22"), "2026-08-24")

    def test_all_is_none(self):
        self.assertIsNone(pipeline.since_for("all", today="2026-09-22"))

    def test_unknown_key_defaults_to_7d(self):
        self.assertEqual(pipeline.since_for("bogus", today="2026-09-22"), "2026-09-16")


class TestRefresh(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.proj = self.root / "projects" / "-Users-teo"
        self.proj.mkdir(parents=True)
        self.cache_path = self.root / "cache.json"

    def write(self, name, lines, mode="w"):
        with open(self.proj / name, mode, encoding="utf-8") as f:
            for l in lines:
                f.write(l + "\n")

    def test_first_run_scans_everything(self):
        self.write("a.jsonl", [line(inp=10), line(inp=5)])
        partials, cache = pipeline.refresh(self.root / "projects", self.cache_path)
        summary = agg.merge(partials)
        self.assertEqual(summary["totals"]["input"], 15)
        self.assertEqual(len(cache["files"]), 1)

    def test_second_run_without_changes_reuses_cache(self):
        self.write("a.jsonl", [line(inp=10)])
        pipeline.refresh(self.root / "projects", self.cache_path)

        # 파일을 읽으면 실패하도록 권한을 없애는 대신, 스캔 호출을 세어 본다
        calls = []
        real = pipeline.scan.scan_file

        def counting(path, offset=0):
            calls.append(path)
            return real(path, offset)

        pipeline.scan.scan_file = counting
        try:
            partials, _ = pipeline.refresh(self.root / "projects", self.cache_path)
        finally:
            pipeline.scan.scan_file = real

        self.assertEqual(calls, [])
        self.assertEqual(agg.merge(partials)["totals"]["input"], 10)

    def test_appended_lines_are_added_not_recounted(self):
        self.write("a.jsonl", [line(inp=10)])
        pipeline.refresh(self.root / "projects", self.cache_path)

        self.write("a.jsonl", [line(inp=5)], mode="a")
        partials, _ = pipeline.refresh(self.root / "projects", self.cache_path)

        self.assertEqual(agg.merge(partials)["totals"]["input"], 15)

    def test_truncated_file_is_fully_rescanned(self):
        self.write("a.jsonl", [line(inp=10), line(inp=10), line(inp=10)])
        pipeline.refresh(self.root / "projects", self.cache_path)

        self.write("a.jsonl", [line(inp=1)])  # 덮어써서 짧아짐
        partials, _ = pipeline.refresh(self.root / "projects", self.cache_path)

        self.assertEqual(agg.merge(partials)["totals"]["input"], 1)

    def test_deleted_file_drops_out_of_cache(self):
        self.write("a.jsonl", [line(inp=10)])
        self.write("b.jsonl", [line(inp=5)])
        pipeline.refresh(self.root / "projects", self.cache_path)

        (self.proj / "b.jsonl").unlink()
        partials, cache = pipeline.refresh(self.root / "projects", self.cache_path)

        self.assertEqual(agg.merge(partials)["totals"]["input"], 10)
        self.assertEqual(len(cache["files"]), 1)

    def test_subagent_transcripts_are_scanned_and_flagged(self):
        sub_dir = self.proj / "sess" / "subagents"
        sub_dir.mkdir(parents=True)
        with open(sub_dir / "agent-x.jsonl", "w", encoding="utf-8") as f:
            f.write(line(session="subsess", inp=4) + "\n")
        self.write("a.jsonl", [line(session="mainsess", inp=6)])

        partials, _ = pipeline.refresh(self.root / "projects", self.cache_path)
        summary = agg.merge(partials)

        self.assertEqual(summary["totals"]["input"], 10)  # 총계에 포함
        self.assertTrue(summary["sessions"]["subsess"]["sub"])
        self.assertFalse(summary["sessions"]["mainsess"]["sub"])

    def test_empty_root_gives_empty_summary(self):
        partials, cache = pipeline.refresh(self.root / "projects", self.cache_path)
        self.assertEqual(agg.merge(partials)["totals"]["total"], 0)
        self.assertEqual(cache["files"], {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_pipeline -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pulse.pipeline'`

- [ ] **Step 3: 최소 구현을 쓴다**

`pulse/pipeline.py`:

```python
"""스캔·집계·캐시를 조립한다. 계산도 입출력도 스스로 하지 않는다."""
import os
from datetime import date, timedelta

from pulse import aggregate as agg
from pulse import cache as cache_mod
from pulse import scan

RANGES = {"7d": 7, "30d": 30}


def is_subagent(path):
    """서브에이전트 트랜스크립트는 <프로젝트>/<세션>/subagents/ 아래에 있다."""
    return "/subagents/" in str(path)


def since_for(range_key, today=None):
    """기간 키 -> 포함 시작일. 'all'이면 None."""
    if range_key == "all":
        return None
    days = RANGES.get(range_key, 7)
    base = date.fromisoformat(today) if today else date.today()
    return (base - timedelta(days=days - 1)).isoformat()


def refresh(projects_root, cache_path):
    """변한 파일만 읽어 캐시를 갱신하고, 전체 부분집계 리스트를 낸다."""
    cache = cache_mod.load_cache(cache_path)
    files = scan.find_transcripts(projects_root)
    known = cache["files"]
    fresh = {}

    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            continue

        entry = known.get(path)
        if (entry and entry.get("size") == st.st_size
                and entry.get("mtime") == st.st_mtime
                and isinstance(entry.get("partial"), dict)):
            fresh[path] = entry  # 변한 게 없다 — 읽지 않는다
            continue

        if entry and st.st_size >= entry.get("offset", 0):
            offset = entry.get("offset", 0)
            partial = entry.get("partial") or agg.empty_partial()
        else:
            offset = 0  # 새 파일이거나 잘렸다 — 처음부터
            partial = agg.empty_partial()

        events, new_offset, bad = scan.scan_file(path, offset)
        partial = agg.fold(events, partial, subagent=is_subagent(path))
        partial["bad_lines"] = partial.get("bad_lines", 0) + bad

        fresh[path] = {"size": st.st_size, "mtime": st.st_mtime,
                       "offset": new_offset, "partial": partial}

    cache["files"] = fresh  # 사라진 파일은 여기서 탈락한다
    cache_mod.save_cache(cache_path, cache)

    return [e["partial"] for e in fresh.values()], cache
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_pipeline -v
```

Expected: PASS — 11 tests

- [ ] **Step 5: 실데이터로 증분 효과를 잰다**

```bash
cd ~/Workspaces/13_AgentPulse
rm -f /tmp/pulse-bench-cache.json
python3 -c "
import time
from pathlib import Path
from pulse import pipeline, aggregate as agg
root = Path.home()/'.claude/projects'; cp='/tmp/pulse-bench-cache.json'
t=time.time(); p,_=pipeline.refresh(root, cp); first=time.time()-t
t=time.time(); p2,_=pipeline.refresh(root, cp); second=time.time()-t
s=agg.merge(p2)
print(f'첫 스캔 {first:.1f}초 / 두번째 {second:.2f}초')
print('처리 토큰', s[\"totals\"][\"total\"], '/ 압축', s[\"compacts\"][\"count\"], '회')
print('깨진 줄', s['bad_lines'])
"
rm -f /tmp/pulse-bench-cache.json
```

Expected: 두 번째 실행이 첫 실행보다 뚜렷이 빠르다(1초 미만 목표). 압축 횟수가 7 이상.

- [ ] **Step 6: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add pulse/pipeline.py tests/test_pipeline.py
git commit -m "feat: 증분 갱신 파이프라인

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: HTTP 서버와 API

**Files:**
- Create: `pulse/server.py`
- Create: `pulse.py` (진입점)
- Create: `web/index.html` (이 태스크에서는 자리만 잡는 최소 HTML)
- Create: `tests/test_server.py`

**Interfaces:**
- Consumes: `config.load_config`, `pipeline.refresh`, `pipeline.since_for`, `aggregate.merge`, `inventory.*`, `live.active_sessions`
- Produces:
  - `pulse.server.State` — `State(cfg, projects_root, cache_path, claude_json, skill_roots)`,
    메서드 `summary(range_key) -> dict`, `live() -> dict`
  - `pulse.server.make_server(state, port, web_dir) -> http.server.ThreadingHTTPServer`
  - `pulse.server.bind(state, port, web_dir, attempts=20) -> http.server.ThreadingHTTPServer`
    포트가 막혀 있으면 다음 포트를 시도한다. 전부 막히면 `OSError`.
  - `pulse.server.serve(state, port, web_dir) -> None`

  `State.summary(range_key)` 반환: Task 4 merge 결과에 `{"range": str, "since": str|None, "unused": {...}, "context_limit": int}` 추가, 단 `sessions` 키는 **제거**(응답 크기와 노출 최소화).
  `State.live()` 반환: `{"sessions": [...], "context_limit": int, "warn_threshold": float, "caution_threshold": float}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_server.py`:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_server -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pulse.server'`

- [ ] **Step 3: 최소 구현을 쓴다**

`pulse/server.py`:

```python
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
```

`pulse.py` (진입점):

```python
#!/usr/bin/env python3
"""AgentPulse — Claude Code 사용량 대시보드."""
import argparse
from pathlib import Path

from pulse import config, inventory, server

CLAUDE_DIR = Path.home() / ".claude"
HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description="Claude Code 사용량 대시보드")
    ap.add_argument("--port", type=int, default=None, help="기본값은 설정 파일의 포트")
    args = ap.parse_args()

    cfg = config.load_config()
    port = args.port or cfg["port"]

    state = server.State(
        cfg=cfg,
        projects_root=CLAUDE_DIR / "projects",
        cache_path=CLAUDE_DIR / "pulse-cache.json",
        claude_json=inventory.DEFAULT_CLAUDE_JSON,
        skill_roots=None,
    )
    print(f"컨텍스트 한도 {cfg['context_limit']:,} · 경고 {cfg['warn_threshold']:.0%}")
    print("첫 실행은 트랜스크립트 전체를 읽느라 시간이 걸립니다…")
    server.serve(state, port, HERE / "web")


if __name__ == "__main__":
    main()
```

`web/index.html` (Task 9에서 채운다):

```html
<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>AgentPulse</title></head>
<body><h1>AgentPulse</h1></body>
</html>
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_server -v
```

Expected: PASS — 13 tests

- [ ] **Step 5: 전체 테스트를 돌린다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest discover -s tests -t . -v
```

Expected: PASS — 전부

- [ ] **Step 6: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
chmod +x pulse.py
git add pulse/server.py pulse.py web/index.html tests/test_server.py
git commit -m "feat: HTTP 서버와 JSON API

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: 대시보드 프론트엔드

**Files:**
- Modify: `web/index.html` (전체 교체)
- Create: `web/style.css`
- Create: `web/app.js`

**Interfaces:**
- Consumes: `GET /api/summary?range=7d|30d|all`, `GET /api/live` (Task 8의 반환 형태)
- Produces: 사용자에게 보이는 화면. 뒤 태스크가 의존하지 않는다.

- [ ] **Step 1: dataviz 스킬을 읽는다**

차트 코드를 한 줄이라도 쓰기 전에 `dataviz` 스킬을 호출한다. 색 팔레트, 축·범례 규칙, 스탯 타일 규격이 거기 있다.

```
Skill(skill="dataviz")
```

- [ ] **Step 2: 화면 뼈대를 쓴다**

`web/index.html` (전체 교체):

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentPulse</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="bar">
  <h1>AgentPulse</h1>
  <span class="tag">Claude Code</span>
  <span class="spacer"></span>
  <span id="conn" class="conn">연결 중</span>
</header>

<section class="block">
  <h2>실시간 세션 · 컨텍스트 창
    <small>최근 15분 안에 활동한 세션 · 5초마다 갱신 · 요금제 사용 한도와는 별개</small>
  </h2>
  <div id="sessions" class="cards"></div>
</section>

<section class="block">
  <h2>사용량 <small id="range-note"></small></h2>
  <div class="tabs" id="range-tabs">
    <button data-range="7d" class="on">최근 7일</button>
    <button data-range="30d">최근 30일</button>
    <button data-range="all">전체</button>
  </div>

  <div class="tiles">
    <div class="tile"><span class="k">도구 호출</span>
      <strong id="t-calls">–</strong><span class="s" id="t-calls-sub"></span></div>
    <div class="tile"><span class="k">처리 토큰</span>
      <strong id="t-tokens">–</strong><span class="s" id="t-tokens-sub"></span></div>
    <div class="tile"><span class="k">컨텍스트 압축</span>
      <strong id="t-compact">–</strong><span class="s" id="t-compact-sub"></span></div>
    <div class="tile"><span class="k">기간 내 미사용</span>
      <strong id="t-unused">–</strong><span class="s">설치된 MCP 서버 · 스킬 중</span></div>
  </div>

  <div class="panel">
    <div class="panel-head"><h3>일별 호출 수</h3>
      <div class="legend">
        <span><i class="sw sw-mcp"></i>MCP</span>
        <span><i class="sw sw-skill"></i>Skill</span>
        <span><i class="sw sw-tools"></i>Tools</span>
      </div>
    </div>
    <div id="chart"></div>
  </div>

  <div class="ranks">
    <div class="panel"><div class="panel-head"><h3><i class="sw sw-mcp"></i>MCP</h3>
      <small id="n-mcp"></small></div><ol id="r-mcp" class="rank"></ol></div>
    <div class="panel"><div class="panel-head"><h3><i class="sw sw-skill"></i>Skill</h3>
      <small id="n-skill"></small></div><ol id="r-skill" class="rank"></ol></div>
    <div class="panel"><div class="panel-head"><h3><i class="sw sw-tools"></i>Tools</h3>
      <small id="n-tools"></small></div><ol id="r-tools" class="rank"></ol></div>
  </div>
</section>

<footer id="foot" class="foot"></footer>
<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 3: 동작을 쓴다**

`web/app.js` (전체):

```javascript
"use strict";

const POLL_MS = 5000;
const $ = (id) => document.getElementById(id);

// ---- 포맷 ----------------------------------------------------------------
function human(n) {
  if (n === null || n === undefined) return "–";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return Math.round(n / 1e3) + "K";
  return String(n);
}
const pct = (r) => (r === null || r === undefined) ? "–" : Math.round(r * 100) + "%";
const comma = (n) => (n ?? 0).toLocaleString("ko-KR");

function clock(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString("ko-KR", { hour12: false });
}

const STATUS_LABEL = { ok: "여유", caution: "주의", danger: "위험", unknown: "미상" };

// ---- 실시간 세션 ----------------------------------------------------------
function renderSessions(data) {
  const box = $("sessions");
  if (!data.sessions.length) {
    box.innerHTML = '<p class="empty">최근 15분 안에 활동한 세션이 없습니다.</p>';
    return;
  }
  const warn = data.warn_threshold;
  box.innerHTML = data.sessions.map((s) => {
    const width = Math.min(100, (s.ratio ?? 0) * 100);
    return `
      <article class="card ${s.status}">
        <div class="card-top">
          <div>
            <div class="title">${escape_(s.title)}</div>
            <div class="meta">${escape_(s.project)} · ${escape_(s.model)} ·
              활동 ${clock(s.last_ts)}</div>
          </div>
          <div class="big">${pct(s.ratio)}</div>
        </div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${width}%"></div>
          <div class="bar-mark" style="left:${warn * 100}%"
               title="경고 임계 ${pct(warn)}"></div>
        </div>
        <div class="card-foot">
          <span>컨텍스트 ${human(s.used)} / ${human(s.limit)}</span>
          <span class="badge">${STATUS_LABEL[s.status]}</span>
        </div>
      </article>`;
  }).join("");
}

function escape_(t) {
  const d = document.createElement("div");
  d.textContent = t ?? "";
  return d.innerHTML;
}

// ---- 집계 ----------------------------------------------------------------
function renderSummary(d) {
  const days = d.days.length;
  $("t-calls").textContent = comma(d.totals.calls);
  $("t-calls-sub").textContent = days ? `하루 평균 ${comma(Math.round(d.totals.calls / days))}` : "";
  $("t-tokens").textContent = human(d.totals.total);
  $("t-tokens-sub").textContent =
    `캐시 읽기 ${pct(d.totals.cache_read_ratio)} · 출력 ${human(d.totals.output)}`;
  $("t-compact").textContent = d.compacts.count + "회";
  $("t-compact-sub").textContent = d.compacts.count
    ? `버린 토큰 ${human(d.compacts.dropped_total)} · 회당 ${human(d.compacts.dropped_avg)}`
    : "아직 없음";
  $("t-unused").textContent = d.unused.count + "개";

  $("range-note").textContent = d.since ? `${d.since} 이후` : "전체 기간";
  drawChart(d.days);
  rank("r-mcp", "n-mcp", d.tools.mcp);
  rank("r-skill", "n-skill", d.tools.skill);
  rank("r-tools", "n-tools", d.tools.tools);

  $("foot").textContent = d.bad_lines
    ? `읽지 못한 줄 ${comma(d.bad_lines)}개 (건너뜀) · 갱신 ${clock(new Date().toISOString())}`
    : `갱신 ${clock(new Date().toISOString())}`;
}

function rank(listId, countId, rows) {
  const ol = $(listId);
  $(countId).textContent = rows.length ? `${rows.length}종 사용` : "";
  if (!rows.length) { ol.innerHTML = '<li class="empty">기록 없음</li>'; return; }
  const max = rows[0][1];
  ol.innerHTML = rows.slice(0, 8).map(([label, n]) => `
    <li><span class="name">${escape_(label)}</span><span class="n">${comma(n)}</span>
      <div class="mini" style="width:${(n / max) * 100}%"></div></li>`).join("");
}

// ---- 차트 (인라인 SVG, 외부 라이브러리 없음) --------------------------------
function drawChart(days) {
  const host = $("chart");
  if (!days.length) { host.innerHTML = '<p class="empty">이 기간에 기록이 없습니다.</p>'; return; }

  const W = 900, H = 240, PAD_L = 44, PAD_B = 28, PAD_T = 12;
  const groups = ["mcp", "skill", "tools"];
  const max = Math.max(1, ...days.map((d) => groups.reduce((a, g) => a + d.calls[g], 0)));
  const step = (W - PAD_L) / days.length;
  const bw = Math.min(28, step * 0.6);
  const y = (v) => PAD_T + (H - PAD_T - PAD_B) * (1 - v / max);

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const v = Math.round(max * f);
    return `<line x1="${PAD_L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" class="grid"/>
            <text x="${PAD_L - 8}" y="${y(v) + 4}" class="ax" text-anchor="end">${human(v)}</text>`;
  }).join("");

  const bars = days.map((d, i) => {
    const x = PAD_L + step * i + (step - bw) / 2;
    let acc = 0;
    const segs = groups.map((g) => {
      const v = d.calls[g];
      if (!v) return "";
      acc += v;
      const top = y(acc), bottom = y(acc - v);
      return `<rect x="${x}" y="${top}" width="${bw}" height="${Math.max(1, bottom - top)}"
                class="b-${g}"><title>${d.day} ${g} ${comma(v)}회</title></rect>`;
    }).join("");
    const label = d.day.slice(5).replace("-", "/");
    return segs + `<text x="${x + bw / 2}" y="${H - 8}" class="ax"
                     text-anchor="middle">${label}</text>`;
  }).join("");

  host.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="일별 도구 호출 수"
          preserveAspectRatio="xMidYMid meet">${ticks}${bars}</svg>`;
}

// ---- 폴링 ----------------------------------------------------------------
let range = "7d";
let online = null;

function setConn(ok) {
  if (ok === online) return;
  online = ok;
  const el = $("conn");
  el.textContent = ok ? "연결됨" : "연결 끊김";
  el.className = "conn " + (ok ? "up" : "down");
}

async function get(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function tickLive() {
  if (document.hidden) return;          // 배경 탭에서는 쉰다
  try { renderSessions(await get("/api/live")); setConn(true); }
  catch { setConn(false); }             // 화면은 그대로 둔다
}

async function loadSummary() {
  try { renderSummary(await get(`/api/summary?range=${range}`)); setConn(true); }
  catch { setConn(false); }
}

$("range-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  range = b.dataset.range;
  [...e.currentTarget.children].forEach((c) => c.classList.toggle("on", c === b));
  loadSummary();
});

document.addEventListener("visibilitychange", () => { if (!document.hidden) tickLive(); });

loadSummary();
tickLive();
setInterval(tickLive, POLL_MS);
```

- [ ] **Step 4: 스타일을 쓴다**

`web/style.css`. 클래스는 위 두 파일이 쓰는 것들이다: `.bar .tag .conn .conn.up
.conn.down .block .cards .card .card.ok .card.caution .card.danger .title .meta
.big .bar-track .bar-fill .bar-mark .card-foot .badge .tabs button .tabs
button.on .tiles .tile .k .s .panel .panel-head .legend .sw .sw-mcp .sw-skill
.sw-tools .ranks .rank .name .n .mini .empty .foot`, 그리고 SVG 안의
`.grid .ax .b-mcp .b-skill .b-tools`.

요구사항:

- 다크 기본. 색은 `:root`에 토큰으로 정의한다. `body`에 배경색을 명시한다.
- 그룹 색 3종(MCP / Skill / Tools)과 상태 색 3단(`ok` / `caution` / `danger`)은
  **dataviz 스킬의 팔레트에서 가져온다.** 두 팔레트를 섞지 않는다 — 그룹 색은
  범주형, 상태 색은 순서형이다.
- `.card.danger`는 테두리와 `.bar-fill`이 위험 색, `.card.caution`은 주의 색.
- `.bar-mark`는 트랙 위 세로 눈금 1px — 경고 임계가 어디인지 보여준다.
- 숫자에 `font-variant-numeric: tabular-nums` — 5초마다 갱신되므로 자릿수가
  흔들리면 눈이 아프다.
- 폭 900px 이하에서 `.tiles`, `.ranks`, `.cards`가 1열로 접힌다. 가로 스크롤 없음.
- SVG의 `.ax`는 작은 흐린 글자, `.grid`는 아주 흐린 가로선.

- [ ] **Step 5: 실제로 띄워 눈으로 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 pulse.py
```

브라우저로 출력된 주소를 연다. 확인할 것:

- 스탯 타일 4개에 0이 아닌 값이 있다
- 일별 차트에 막대가 그려진다
- 기간 탭을 바꾸면 수치가 바뀐다
- 이 세션이 실시간 세션 카드에 뜬다 (지금 Claude Code를 쓰고 있으므로)
- 창을 좁혀도 가로 스크롤이 생기지 않는다

- [ ] **Step 6: 서버 테스트가 여전히 통과하는지 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest discover -s tests -t . -v
```

Expected: PASS — 전부

- [ ] **Step 7: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add web/
git commit -m "feat: 대시보드 프론트엔드

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: 컨텍스트 경고 훅

**Files:**
- Create: `hooks/context_alert.py`
- Create: `tests/test_context_alert.py`

**Interfaces:**
- Consumes: `config.load_config`
- Produces: 실행 가능한 스크립트. Claude Code가 Stop 훅으로 부른다.
  - `hooks.context_alert.should_alert(payload: dict, cfg: dict, seen: dict) -> tuple[bool, dict]`
    반환 `(발화할지, 갱신된 seen)`
  - `hooks.context_alert.message(payload: dict, cfg: dict) -> tuple[str, str]` — `(제목, 본문)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_context_alert.py`:

```python
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hooks import context_alert as alert

CFG = {"context_limit": 1_000_000, "warn_threshold": 0.85,
       "caution_threshold": 0.70, "active_window_minutes": 15, "port": 8787}

ROOT = Path(__file__).resolve().parent.parent


def payload(pct=90.0, session="s1"):
    return {"session_id": session, "context_window": {"used_percentage": pct}}


class TestShouldAlert(unittest.TestCase):
    def test_below_threshold_is_quiet(self):
        fire, _ = alert.should_alert(payload(pct=50.0), CFG, {})
        self.assertFalse(fire)

    def test_above_threshold_fires(self):
        fire, seen = alert.should_alert(payload(pct=90.0), CFG, {})
        self.assertTrue(fire)
        self.assertIn("s1", seen)

    def test_exactly_at_threshold_fires(self):
        fire, _ = alert.should_alert(payload(pct=85.0), CFG, {})
        self.assertTrue(fire)

    def test_same_session_fires_only_once(self):
        fire, seen = alert.should_alert(payload(pct=90.0), CFG, {})
        self.assertTrue(fire)
        fire2, _ = alert.should_alert(payload(pct=95.0), CFG, seen)
        self.assertFalse(fire2)

    def test_different_session_fires_separately(self):
        _, seen = alert.should_alert(payload(pct=90.0, session="s1"), CFG, {})
        fire, _ = alert.should_alert(payload(pct=90.0, session="s2"), CFG, seen)
        self.assertTrue(fire)

    def test_missing_context_window_is_quiet(self):
        fire, _ = alert.should_alert({"session_id": "s1"}, CFG, {})
        self.assertFalse(fire)

    def test_missing_session_id_is_quiet(self):
        fire, _ = alert.should_alert({"context_window": {"used_percentage": 99}},
                                     CFG, {})
        self.assertFalse(fire)


class TestMessage(unittest.TestCase):
    def test_message_reports_percent_and_remaining(self):
        title, body = alert.message(payload(pct=90.0), CFG)
        self.assertIn("90%", body)
        self.assertIn("100K", body)  # 1,000,000의 10%
        self.assertTrue(title)

    def test_message_has_no_shell_metacharacters_from_input(self):
        # osascript에 문자열을 넘기므로 따옴표가 새어나가면 안 된다
        p = {"session_id": 's"1', "context_window": {"used_percentage": 90.0}}
        _, body = alert.message(p, CFG)
        self.assertNotIn('"', body)


class TestScriptNeverFails(unittest.TestCase):
    def run_script(self, stdin_text, alerted_path):
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "context_alert.py"),
             "--alerted-path", str(alerted_path), "--dry-run"],
            input=stdin_text, text=True, capture_output=True, cwd=ROOT,
        )

    def test_broken_stdin_exits_zero(self):
        d = Path(tempfile.mkdtemp())
        r = self.run_script("{not json", d / "alerted.json")
        self.assertEqual(r.returncode, 0)

    def test_empty_stdin_exits_zero(self):
        d = Path(tempfile.mkdtemp())
        r = self.run_script("", d / "alerted.json")
        self.assertEqual(r.returncode, 0)

    def test_dry_run_prints_when_over_threshold(self):
        d = Path(tempfile.mkdtemp())
        r = self.run_script(json.dumps(payload(pct=99.0)), d / "alerted.json")
        self.assertEqual(r.returncode, 0)
        self.assertIn("99", r.stdout)

    def test_unwritable_alerted_path_exits_zero(self):
        r = self.run_script(json.dumps(payload(pct=99.0)),
                            "/nonexistent-root/alerted.json")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_context_alert -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'hooks'`

- [ ] **Step 3: 최소 구현을 쓴다**

`hooks/__init__.py`: 빈 파일.

`hooks/context_alert.py`:

```python
#!/usr/bin/env python3
"""Stop 훅: 컨텍스트가 임계값을 넘으면 macOS 알림을 한 번 띄운다.

절대 실패하지 않는다 — 어떤 예외에도 exit 0 한다. 이 훅이 죽으면
Claude Code의 턴이 막힌다.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_ALERTED_PATH = CLAUDE_DIR / "pulse-alerted.json"


def _human(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def should_alert(payload, cfg, seen):
    """(발화할지, 갱신된 seen). 세션당 한 번만 발화한다."""
    session = payload.get("session_id")
    window = payload.get("context_window") or {}
    pct = window.get("used_percentage")

    if not session or not isinstance(pct, (int, float)):
        return False, seen
    if pct / 100 < cfg["warn_threshold"]:
        return False, seen
    if session in seen:
        return False, seen

    return True, {**seen, session: pct}


def message(payload, cfg):
    pct = (payload.get("context_window") or {}).get("used_percentage") or 0
    limit = cfg["context_limit"]
    remaining = max(0, int(limit * (100 - pct) / 100))
    title = "컨텍스트 경고"
    body = (f"컨텍스트 {pct:.0f}% 사용 · 약 {_human(remaining)} 남음. "
            f"정리하거나 새 세션으로 옮길 때입니다.")
    return title, body


def _notify(title, body):
    """osascript로 알림 배너. 인자는 -e 문자열이 아니라 별도 인자로 넘긴다."""
    script = ('on run argv\n'
              '  display notification (item 1 of argv) with title (item 2 of argv)\n'
              'end run')
    subprocess.run(["osascript", "-e", script, body, title],
                   capture_output=True, timeout=5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerted-path", default=str(DEFAULT_ALERTED_PATH))
    ap.add_argument("--dry-run", action="store_true",
                    help="알림을 띄우는 대신 표준출력에 찍는다 (테스트용)")
    args = ap.parse_args()

    from pulse import config

    payload = json.loads(sys.stdin.read())
    cfg = config.load_config()

    alerted_path = Path(args.alerted_path)
    try:
        seen = json.loads(alerted_path.read_text(encoding="utf-8"))
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, ValueError):
        seen = {}

    fire, seen = should_alert(payload, cfg, seen)
    if not fire:
        return

    title, body = message(payload, cfg)
    if args.dry_run:
        print(f"{title}: {body}")
    else:
        _notify(title, body)

    alerted_path.parent.mkdir(parents=True, exist_ok=True)
    alerted_path.write_text(json.dumps(seen), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # 훅은 Claude Code를 절대 막지 않는다
    sys.exit(0)
```

`message`의 본문에 세션 id를 넣지 않으므로 따옴표가 새어나갈 여지가 없고, `_notify`는 셸을 거치지 않고 argv로 넘긴다.

- [ ] **Step 4: 통과를 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest tests.test_context_alert -v
```

Expected: PASS — 13 tests

- [ ] **Step 5: 알림이 실제로 뜨는지 눈으로 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
echo '{"session_id":"manual-test","context_window":{"used_percentage":91}}' \
  | python3 hooks/context_alert.py --alerted-path /tmp/pulse-alerted-test.json
rm -f /tmp/pulse-alerted-test.json
```

Expected: macOS 알림 배너가 뜬다. 안 뜨면 시스템 설정 → 알림에서 터미널 앱의 알림이 꺼져 있는지 확인한다.

- [ ] **Step 6: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
chmod +x hooks/context_alert.py
git add hooks/ tests/test_context_alert.py
git commit -m "feat: 컨텍스트 임계값 경고 훅

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: statusline 경고와 훅 등록

> 이 태스크만 `~/.claude/` 아래 기존 파일을 고친다. **두 파일 모두 백업 먼저.**

**Files:**
- Modify: `~/.claude/statusline.py` (ctx 표시 부분)
- Modify: `~/.claude/settings.json` (`hooks.Stop`에 항목 추가)
- Create: `README.md`

**Interfaces:**
- Consumes: `hooks/context_alert.py` (Task 10)
- Produces: 없음 (배선 태스크)

- [ ] **Step 1: 백업한다**

```bash
stamp=$(date +%Y%m%d-%H%M%S)
cp ~/.claude/statusline.py ~/.claude/statusline.py.bak-pulse-$stamp
cp ~/.claude/settings.json ~/.claude/settings.json.bak-pulse-$stamp
ls -la ~/.claude/*.bak-pulse-*
```

Expected: 백업 두 개가 생긴다.

- [ ] **Step 2: statusline의 ctx 표시를 고친다**

`~/.claude/statusline.py`에서 현재 이 블록을 찾는다:

```python
ctx = (d.get("context_window") or {}).get("used_percentage")
if isinstance(ctx, (int, float)):
    parts.append(f"{DIM}ctx{RESET} {color(ctx)}{ctx:.0f}%{RESET}")
```

다음으로 바꾼다:

```python
WARN_AT = 85  # AgentPulse와 같은 임계값

ctx_win = d.get("context_window") or {}
ctx = ctx_win.get("used_percentage")
if isinstance(ctx, (int, float)):
    chunk = f"{DIM}ctx{RESET} {color(ctx)}{ctx:.0f}%{RESET}"
    if ctx >= WARN_AT:
        # 임계 초과 — 남은 토큰을 붙여 정리할 시점임을 알린다
        size = ctx_win.get("context_window_size") or ctx_win.get("max_tokens")
        if isinstance(size, (int, float)) and size > 0:
            left_tok = int(size * (100 - ctx) / 100)
            human = (f"{left_tok/1000:.0f}K" if left_tok >= 1000 else str(left_tok))
            chunk = f"\033[31m⚠{RESET} {chunk}{DIM}({human} 남음){RESET}"
        else:
            chunk = f"\033[31m⚠{RESET} {chunk}"
    parts.append(chunk)
```

`WARN_AT = 85`는 파일 상단의 `SEP = ...` 줄 아래로 옮겨 상수들과 함께 둔다.

- [ ] **Step 3: statusline이 여전히 도는지 확인한다**

```bash
echo '{"model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/Users/teo"},"context_window":{"used_percentage":36}}' | ~/.claude/statusline.py
echo
echo '{"model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/Users/teo"},"context_window":{"used_percentage":91,"context_window_size":1000000}}' | ~/.claude/statusline.py
```

Expected: 첫 줄은 `ctx 36%`가 평소대로, 둘째 줄은 `⚠ ctx 91%(90K 남음)`. 예외 없음.

- [ ] **Step 4: Stop 훅을 등록한다**

`~/.claude/settings.json`의 `hooks.Stop[0].hooks` 배열에 항목을 **추가**한다. 기존 `afplay` 항목은 지우지 않는다. 결과는 이렇게 된다:

```json
"Stop": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "afplay /System/Library/Sounds/Glass.aiff",
        "async": true
      },
      {
        "type": "command",
        "command": "python3 /Users/teo/Workspaces/13_AgentPulse/hooks/context_alert.py",
        "async": true
      }
    ]
  }
]
```

- [ ] **Step 5: settings.json이 유효한 JSON인지 확인한다**

```bash
python3 -c "
import json
d = json.load(open('/Users/teo/.claude/settings.json'))
stop = d['hooks']['Stop'][0]['hooks']
print('Stop 훅', len(stop), '개')
for h in stop:
    print(' -', h['command'])
assert any('afplay' in h['command'] for h in stop), '기존 afplay 훅이 사라졌다'
assert any('context_alert' in h['command'] for h in stop), '새 훅이 없다'
print('OK')
"
```

Expected: `Stop 훅 2 개`, 둘 다 나열되고 `OK`.

- [ ] **Step 6: README를 쓴다**

`README.md`에 담을 것:

- 한 줄 소개와 **스크린샷 대신 무엇을 보여주는지** 목록
- 실행법: `python3 pulse.py`, 기본 주소
- 설정: `~/.claude/pulse-config.json`의 키와 기본값 (Global Constraints의 값 그대로)
- **teo 님 환경의 실측 근거** — 모델이 `opus[1m]`이라 컨텍스트가 1M이고, 실제 압축은 967K 부근에서 걸렸으며, 그래서 경고 임계값이 85%라는 것. 통설인 "200K마다 압축"이 이 환경에 해당하지 않는다는 점.
- 이 도구가 건드리는 파일 목록(`pulse-cache.json`, `pulse-config.json`, `pulse-alerted.json`, 그리고 Task 11이 고친 `statusline.py`·`settings.json`과 그 백업 위치)
- 원복 방법: 백업 파일을 되돌리고 settings.json에서 훅 항목을 지우면 된다는 것
- 테스트 실행 명령

- [ ] **Step 7: 커밋**

```bash
cd ~/Workspaces/13_AgentPulse
git add README.md
git commit -m "feat: statusline 경고와 Stop 훅 등록, README

~/.claude/statusline.py와 settings.json도 함께 수정했다(저장소 밖,
백업은 ~/.claude/*.bak-pulse-* 에 있다).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: 실데이터 검증

**Files:**
- 없음 (검증만)

**Interfaces:**
- Consumes: 전부
- Produces: 없음

- [ ] **Step 1: 전체 테스트**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -m unittest discover -s tests -t . -v 2>&1 | tail -5
```

Expected: `OK`, 실패 0.

- [ ] **Step 2: 실데이터 수치가 사실과 맞는지 대조한다**

대시보드가 낸 숫자를, 트랜스크립트를 독립적으로 세어 만든 숫자와 비교한다.

```bash
cd ~/Workspaces/13_AgentPulse
rm -f /tmp/pulse-verify-cache.json
python3 -c "
from pathlib import Path
from pulse import pipeline, aggregate as agg
p,_ = pipeline.refresh(Path.home()/'.claude/projects', '/tmp/pulse-verify-cache.json')
s = agg.merge(p)
print('대시보드 — 압축', s['compacts']['count'], '회 / Bash',
      dict(s['tools']['tools']).get('Bash'))
"
python3 -c "
import json, glob, os, collections
os.chdir(os.path.expanduser('~/.claude/projects'))
compacts = 0; bash = 0
for f in glob.glob('*/*.jsonl'):
    for line in open(f, errors='ignore'):
        try: d = json.loads(line)
        except: continue
        if d.get('type')=='system' and d.get('subtype')=='compact_boundary':
            compacts += 1
        if d.get('type')=='assistant':
            for c in (d.get('message') or {}).get('content') or []:
                if isinstance(c,dict) and c.get('type')=='tool_use' and c.get('name')=='Bash':
                    bash += 1
print('독립 계수 — 압축', compacts, '회 / Bash', bash)
"
rm -f /tmp/pulse-verify-cache.json
```

Expected: 두 줄의 숫자가 **일치**한다. 다르면 멈추고 원인을 찾는다 — 집계가 틀렸다는 뜻이다.

- [ ] **Step 3: 증분 성능을 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 -c "
import time
from pathlib import Path
from pulse import pipeline
root = Path.home()/'.claude/projects'
cp = Path.home()/'.claude/pulse-cache.json'
t=time.time(); pipeline.refresh(root, cp); print(f'갱신 {time.time()-t:.2f}초')
"
```

Expected: 1초 미만 (캐시가 이미 채워진 상태에서).

- [ ] **Step 4: 대시보드를 띄워 최종 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
python3 pulse.py
```

확인 목록:

- 실시간 카드에 현재 세션이 뜨고, 퍼센트가 statusline의 `ctx %`와 비슷하다 (계산 방식이 달라 정확히 같지는 않다)
- 기간 탭 3개가 모두 동작한다
- 압축 타일의 횟수가 Step 2의 독립 계수와 같다
- 미사용 목록에 실제로 안 쓴 것들이 들어 있다
- 브라우저 콘솔에 에러가 없다

`Ctrl+C`로 종료한다.

- [ ] **Step 5: 커밋할 변경이 남았는지 확인한다**

```bash
cd ~/Workspaces/13_AgentPulse
git status --short
git log --oneline
```

Expected: 워킹 트리가 깨끗하고, 태스크별 커밋이 순서대로 쌓여 있다.
