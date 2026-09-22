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
    def run_script(self, stdin_text, alerted_path, cwd=None):
        """설정 경로도 주입한다 — 안 그러면 실제 ~/.claude를 읽는다."""
        d = Path(tempfile.mkdtemp())
        settings = d / "settings.json"
        settings.write_text(json.dumps({"model": "opus[1m]"}), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "context_alert.py"),
             "--alerted-path", str(alerted_path),
             "--config-path", str(d / "absent.json"),
             "--settings-path", str(settings),
             "--dry-run"],
            input=stdin_text, text=True, capture_output=True,
            cwd=cwd or ROOT,
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

    def test_works_from_an_unrelated_cwd(self):
        """Claude Code는 사용자의 프로젝트 디렉터리를 cwd로 훅을 실행한다.

        프로젝트 루트에서만 돌려보면 sys.path가 우연히 맞아 통과한다 —
        그래서 이 테스트가 없으면 훅이 실무에서 조용히 죽는 것을 못 잡는다.
        """
        d = Path(tempfile.mkdtemp())
        r = self.run_script(json.dumps(payload(pct=99.0)),
                            d / "alerted.json", cwd=d)
        self.assertEqual(r.returncode, 0)
        self.assertIn("99", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_unwritable_alerted_path_exits_zero(self):
        r = self.run_script(json.dumps(payload(pct=99.0)),
                            "/nonexistent-root/alerted.json")
        self.assertEqual(r.returncode, 0)

    def test_unwritable_alerted_path_does_not_alert_every_turn(self):
        """기록이 안 되면 조용히 넘어간다. 턴마다 울리는 알림이 더 나쁘다."""
        outs = [self.run_script(json.dumps(payload(pct=99.0)),
                                "/nonexistent-root/alerted.json").stdout
                for _ in range(3)]
        self.assertEqual(outs, ["", "", ""])


if __name__ == "__main__":
    unittest.main()
