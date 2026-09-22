import json
import subprocess
import sys
import unittest
from pathlib import Path

from hooks import context_alert as alert
from tests import tmpdir

CFG = {"context_limit": 1_000_000, "warn_threshold": 0.85,
       "caution_threshold": 0.70, "active_window_minutes": 15, "port": 8787}

ROOT = Path(__file__).resolve().parent.parent


def rec(used, sidechain=False, kind="assistant", model="m"):
    return json.dumps({
        "type": kind, "sessionId": "s1", "isSidechain": sidechain,
        "message": {"role": "assistant", "model": model,
                    "usage": {"input_tokens": 0,
                              "cache_read_input_tokens": used,
                              "cache_creation_input_tokens": 0}},
    })


def transcript(lines):
    d = tmpdir()
    p = d / "t.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def payload(session="s1", transcript_path=""):
    # 실측한 Stop 훅 payload에는 context_window가 없다. session_id와
    # transcript_path가 훅이 쓰는 전부다.
    return {"session_id": session, "transcript_path": str(transcript_path),
            "hook_event_name": "Stop", "cwd": "/Users/teo"}


class TestContextUsed(unittest.TestCase):
    def test_reads_last_assistant_usage(self):
        p = transcript([rec(100), rec(900_000)])
        self.assertEqual(alert.context_used(p), 900_000)

    def test_skips_sidechain_records(self):
        # 서브에이전트는 부모와 세션 id를 공유한다. 그 점유는 사용자 것이 아니다.
        p = transcript([rec(900_000), rec(50, sidechain=True)])
        self.assertEqual(alert.context_used(p), 900_000)

    def test_ignores_non_assistant_records(self):
        p = transcript([rec(900_000), rec(1, kind="user")])
        self.assertEqual(alert.context_used(p), 900_000)

    def test_broken_lines_are_skipped(self):
        p = transcript([rec(900_000), "{깨진 줄"])
        self.assertEqual(alert.context_used(p), 900_000)

    def test_no_assistant_records_gives_none(self):
        p = transcript([rec(1, kind="user")])
        self.assertIsNone(alert.context_used(p))

    def test_missing_file_gives_none(self):
        self.assertIsNone(alert.context_used("/nonexistent/t.jsonl"))

    def test_empty_path_gives_none(self):
        self.assertIsNone(alert.context_used(""))

    def test_last_assistant_reports_the_model_too(self):
        """훅은 마지막 assistant 레코드를 이미 읽는다 — 그 레코드에 모델이 있다."""
        p = transcript([rec(100), rec(900_000, model="claude-haiku-4-5")])
        self.assertEqual(alert.last_assistant(p), (900_000, "claude-haiku-4-5"))

    def test_last_assistant_on_missing_file_is_none_none(self):
        self.assertEqual(alert.last_assistant("/nonexistent/t.jsonl"), (None, None))

    def test_finds_recent_record_in_a_file_larger_than_the_tail(self):
        # 꼬리만 읽는다. 오래된 줄로 파일을 부풀려도 최근 값을 찾아야 한다.
        filler = [rec(1)] * 4000
        p = transcript(filler + [rec(777_000)])
        self.assertGreater(p.stat().st_size, alert.TAIL_BYTES)
        self.assertEqual(alert.context_used(p), 777_000)


class TestShouldAlert(unittest.TestCase):
    def test_below_threshold_is_quiet(self):
        fire, _ = alert.should_alert(payload(), CFG, {}, 500_000)
        self.assertFalse(fire)

    def test_above_threshold_fires(self):
        fire, seen = alert.should_alert(payload(), CFG, {}, 900_000)
        self.assertTrue(fire)
        self.assertIn("s1", seen)

    def test_exactly_at_threshold_fires(self):
        fire, _ = alert.should_alert(payload(), CFG, {}, 850_000)
        self.assertTrue(fire)

    def test_same_session_fires_only_once(self):
        fire, seen = alert.should_alert(payload(), CFG, {}, 900_000)
        self.assertTrue(fire)
        fire2, _ = alert.should_alert(payload(), CFG, seen, 950_000)
        self.assertFalse(fire2)

    def test_different_session_fires_separately(self):
        _, seen = alert.should_alert(payload(session="s1"), CFG, {}, 900_000)
        fire, _ = alert.should_alert(payload(session="s2"), CFG, seen, 900_000)
        self.assertTrue(fire)

    def test_unknown_usage_is_quiet(self):
        fire, _ = alert.should_alert(payload(), CFG, {}, None)
        self.assertFalse(fire)

    def test_missing_session_id_is_quiet(self):
        fire, _ = alert.should_alert({"transcript_path": "/x"}, CFG, {}, 900_000)
        self.assertFalse(fire)

    def test_stays_quiet_while_occupancy_stays_high(self):
        """한 번 찬 컨텍스트에 두 번 울리지 않는다."""
        fire, seen = alert.should_alert(payload(), CFG, {}, 900_000)
        self.assertTrue(fire)
        for used in (880_000, 950_000, 990_000):
            fire, seen = alert.should_alert(payload(), CFG, seen, used)
            self.assertFalse(fire)
        self.assertIn("s1", seen)

    def test_re_arms_after_occupancy_drops_below_caution(self):
        """압축이 걸리면 점유가 떨어진다 — 다음 채움에는 다시 울려야 한다.

        실측: 압축이 한 번이라도 걸린 3개 세션 중 2개는 세 번씩 압축됐다.
        세션당 한 번만 울리면 7번 중 4번을, 그것도 가장 긴 세션에서 놓친다.
        """
        fire, seen = alert.should_alert(payload(), CFG, {}, 900_000)
        self.assertTrue(fire)

        fire, seen = alert.should_alert(payload(), CFG, seen, 300_000)
        self.assertFalse(fire)
        self.assertNotIn("s1", seen)  # 다시 장전됐다

        fire, _ = alert.should_alert(payload(), CFG, seen, 900_000)
        self.assertTrue(fire)

    def test_caution_band_does_not_re_arm(self):
        """주의(70%)와 경고(85%) 사이로 내려온 것은 압축이 아니다."""
        _, seen = alert.should_alert(payload(), CFG, {}, 900_000)
        _, seen = alert.should_alert(payload(), CFG, seen, 750_000)
        self.assertIn("s1", seen)
        fire, _ = alert.should_alert(payload(), CFG, seen, 900_000)
        self.assertFalse(fire)

    def test_uses_the_session_model_limit(self):
        # 180K는 1M의 18%지만 haiku의 200K로는 90%다
        fire, _ = alert.should_alert(payload(), CFG, {}, 180_000,
                                     model="claude-haiku-4-5")
        self.assertTrue(fire)

    def test_unknown_model_falls_back_to_the_configured_limit(self):
        fire, _ = alert.should_alert(payload(), CFG, {}, 180_000,
                                     model="알 수 없는 모델")
        self.assertFalse(fire)

    def test_zero_limit_is_quiet_not_a_division_error(self):
        cfg = {**CFG, "context_limit": 0}
        fire, _ = alert.should_alert(payload(), cfg, {}, 900_000)
        self.assertFalse(fire)


class TestLimitFor(unittest.TestCase):
    """모델→한도 표는 설정값을 아래로만 조정한다 — 위로 부풀리지 않는다.

    transcript의 model은 `claude-opus-5`처럼 맨 API id로 오고 `[1m]` 여부를
    알려주지 않는다. 표가 설정값보다 큰 한도를 준다고 믿을 근거가 없으므로
    상한은 항상 cfg["context_limit"]이다.
    """

    def test_haiku_under_1m_config_gives_200k(self):
        cfg = {**CFG, "context_limit": 1_000_000}
        self.assertEqual(alert.limit_for(cfg, "claude-haiku-4-5"), 200_000)

    def test_opus_under_1m_config_gives_1m(self):
        cfg = {**CFG, "context_limit": 1_000_000}
        self.assertEqual(alert.limit_for(cfg, "claude-opus-5"), 1_000_000)

    def test_opus_under_200k_config_is_capped_to_200k(self):
        """이게 오늘 실패하는 케이스다: opus가 200K 설정을 1M로 부풀린다."""
        cfg = {**CFG, "context_limit": 200_000}
        self.assertEqual(alert.limit_for(cfg, "claude-opus-5"), 200_000)

    def test_unknown_model_uses_the_configured_limit_either_way(self):
        for limit in (200_000, 1_000_000):
            with self.subTest(limit=limit):
                cfg = {**CFG, "context_limit": limit}
                self.assertEqual(alert.limit_for(cfg, "claude-mystery-9"), limit)


class TestMessage(unittest.TestCase):
    def test_message_reports_percent_and_remaining(self):
        title, body = alert.message(900_000, CFG)
        self.assertIn("90%", body)
        self.assertIn("100K", body)
        self.assertTrue(title)

    def test_message_uses_the_session_model_limit(self):
        title, body = alert.message(180_000, CFG, model="claude-haiku-4-5")
        self.assertIn("90%", body)
        self.assertIn("20K", body)

    def test_message_has_no_quotes(self):
        # osascript에 넘기는 문자열이라 따옴표가 새어나가면 안 된다
        _, body = alert.message(900_000, CFG)
        self.assertNotIn('"', body)


class TestScriptNeverFails(unittest.TestCase):
    def run_script(self, stdin_text, alerted_path, cwd=None):
        """설정 경로도 주입한다 — 안 그러면 실제 ~/.claude를 읽는다."""
        d = tmpdir()
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
        d = tmpdir()
        r = self.run_script("{not json", d / "alerted.json")
        self.assertEqual(r.returncode, 0)

    def test_empty_stdin_exits_zero(self):
        d = tmpdir()
        r = self.run_script("", d / "alerted.json")
        self.assertEqual(r.returncode, 0)

    def test_dry_run_prints_when_over_threshold(self):
        d = tmpdir()
        p = transcript([rec(990_000)])
        r = self.run_script(json.dumps(payload(transcript_path=p)),
                            d / "alerted.json")
        self.assertEqual(r.returncode, 0)
        self.assertIn("99", r.stdout)

    def test_quiet_when_transcript_missing(self):
        d = tmpdir()
        r = self.run_script(json.dumps(payload(transcript_path="/nope.jsonl")),
                            d / "alerted.json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_works_from_an_unrelated_cwd(self):
        """Claude Code는 사용자의 프로젝트 디렉터리를 cwd로 훅을 실행한다.

        프로젝트 루트에서만 돌려보면 sys.path가 우연히 맞아 통과한다 —
        그래서 이 테스트가 없으면 훅이 실무에서 조용히 죽는 것을 못 잡는다.
        """
        d = tmpdir()
        p = transcript([rec(990_000)])
        r = self.run_script(json.dumps(payload(transcript_path=p)),
                            d / "alerted.json", cwd=d)
        self.assertEqual(r.returncode, 0)
        self.assertIn("99", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_unwritable_alerted_path_exits_zero(self):
        p = transcript([rec(990_000)])
        r = self.run_script(json.dumps(payload(transcript_path=p)),
                            "/nonexistent-root/alerted.json")
        self.assertEqual(r.returncode, 0)

    def test_unwritable_alerted_path_does_not_alert_every_turn(self):
        """기록이 안 되면 조용히 넘어간다. 턴마다 울리는 알림이 더 나쁘다."""
        p = transcript([rec(990_000)])
        outs = [self.run_script(json.dumps(payload(transcript_path=p)),
                                "/nonexistent-root/alerted.json").stdout
                for _ in range(3)]
        self.assertEqual(outs, ["", "", ""])


if __name__ == "__main__":
    unittest.main()
