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
