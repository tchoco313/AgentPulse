import json
import unittest
from pathlib import Path

from pulse import inventory as inv
from tests import tmpdir


class TestInstalledMcpServers(unittest.TestCase):
    def test_reads_mcp_servers_key(self):
        d = tmpdir()
        p = d / ".claude.json"
        p.write_text(json.dumps({"mcpServers": {"dbhub": {}, "other": {}}}),
                     encoding="utf-8")
        self.assertEqual(inv.installed_mcp_servers(p), {"dbhub", "other"})

    def test_missing_file_gives_empty_set(self):
        self.assertEqual(inv.installed_mcp_servers("/nonexistent/.claude.json"), set())

    def test_broken_json_gives_empty_set(self):
        d = tmpdir()
        p = d / ".claude.json"
        p.write_text("{broken", encoding="utf-8")
        self.assertEqual(inv.installed_mcp_servers(p), set())


class TestInstalledSkills(unittest.TestCase):
    def test_finds_skill_dirs_by_skill_md(self):
        root = tmpdir()
        (root / "alpha").mkdir()
        (root / "alpha" / "SKILL.md").write_text("x", encoding="utf-8")
        (root / "beta" / "nested").mkdir(parents=True)
        (root / "beta" / "nested" / "SKILL.md").write_text("x", encoding="utf-8")
        (root / "gamma").mkdir()  # SKILL.md 없음

        self.assertEqual(inv.installed_skills([root]), {"alpha", "nested"})

    def test_missing_root_is_skipped(self):
        self.assertEqual(inv.installed_skills(["/nonexistent"]), set())


class TestDefaultSkillRoots(unittest.TestCase):
    def test_mode_bundles_are_not_counted(self):
        """모드 번들을 세면 미사용이 314개가 되어 타일이 잡음이 된다(실측)."""
        names = [p.name for p in inv.DEFAULT_SKILL_ROOTS]
        self.assertNotIn("modes", names)
        self.assertIn("skills", names)


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
