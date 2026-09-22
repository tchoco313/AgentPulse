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

    def test_concurrent_saves_all_succeed(self):
        """두 요청이 동시에 갱신해도 죽지 않는다 — 실측으로 터지던 경로다."""
        import threading

        errors = []

        def writer(i):
            data = cache.empty_cache()
            data["files"][f"/f{i}.jsonl"] = {"size": i, "mtime": 1.0,
                                             "offset": i, "partial": {}}
            try:
                for _ in range(20):
                    cache.save_cache(self.path, data)
            except Exception as e:  # noqa: BLE001 - 무엇이 터지든 기록한다
                errors.append(repr(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual([p.name for p in self.dir.iterdir()], ["pulse-cache.json"])
        self.assertEqual(cache.load_cache(self.path)["schema"], cache.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
