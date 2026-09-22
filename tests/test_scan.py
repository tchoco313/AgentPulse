import json
import unittest
from pathlib import Path

from pulse import scan
from tests import tmpdir


def assistant_line(ts="2026-09-22T01:00:00.000Z", session="s1", usage=None,
                   model="claude-opus-5", tools=(), cwd="/Users/teo",
                   sidechain=False):
    content = [{"type": "text", "text": "hi"}]
    for name, inp in tools:
        content.append({"type": "tool_use", "id": "t", "name": name, "input": inp})
    msg = {"role": "assistant", "model": model, "content": content}
    if usage is not None:
        msg["usage"] = usage
    return json.dumps({
        "type": "assistant", "timestamp": ts, "sessionId": session,
        "cwd": cwd, "isSidechain": sidechain, "message": msg,
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

    def test_sidechain_records_are_marked(self):
        # 서브에이전트 트랜스크립트는 부모와 같은 sessionId를 쓴다.
        # isSidechain만이 둘을 가르는 신호다.
        ev = scan.parse_line(assistant_line(usage={}, sidechain=True,
                                            tools=[("Bash", {})]))
        self.assertTrue(all(e["sub"] for e in ev))

    def test_non_sidechain_records_are_not_marked(self):
        ev = scan.parse_line(assistant_line(usage={}, tools=[("Bash", {})]))
        self.assertFalse(any(e["sub"] for e in ev))

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
        self.dir = tmpdir()
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
        root = tmpdir()
        (root / "projB").mkdir()
        (root / "projA").mkdir()
        (root / "projB" / "b.jsonl").write_text("", encoding="utf-8")
        (root / "projA" / "a.jsonl").write_text("", encoding="utf-8")
        (root / "projA" / "notes.txt").write_text("", encoding="utf-8")

        found = scan.find_transcripts(root)

        self.assertEqual([Path(p).name for p in found], ["a.jsonl", "b.jsonl"])

    def test_finds_subagent_transcripts(self):
        # 실측 117개 중 53개가 <프로젝트>/<세션>/subagents/ 아래에 있다
        root = tmpdir()
        (root / "proj" / "sess" / "subagents").mkdir(parents=True)
        (root / "proj" / "main.jsonl").write_text("", encoding="utf-8")
        (root / "proj" / "sess" / "subagents" / "agent-x.jsonl").write_text(
            "", encoding="utf-8")

        found = scan.find_transcripts(root)

        # 전체 경로로 정렬하므로 proj/main.jsonl 이
        # proj/sess/subagents/agent-x.jsonl 보다 앞선다
        self.assertEqual([Path(p).name for p in found],
                         ["main.jsonl", "agent-x.jsonl"])

    def test_missing_root_gives_empty_list(self):
        self.assertEqual(scan.find_transcripts("/nonexistent/projects"), [])


if __name__ == "__main__":
    unittest.main()
