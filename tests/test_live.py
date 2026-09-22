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

    def test_naive_now_does_not_raise(self):
        sessions = {"s1": {"title": "작업", "cwd": "/x", "model": "m",
                           "last_ts": iso(self.now - timedelta(minutes=1)),
                           "last_ctx": 100}}

        result = live.active_sessions(sessions, CFG,
                                      now=self.now.replace(tzinfo=None))

        self.assertEqual(len(result), 1)

    def test_session_model_decides_the_limit_not_the_global_config(self):
        """settings.json이 opus[1m]이어도 haiku로 도는 세션은 200K다.

        실패 사례: `claude --model ...`은 settings.json을 고치지 않는다.
        전역 한도로 재면 카드가 14% "여유"를 찍는 동안 실제로는 69%였다.
        """
        sessions = {"s1": {"title": "작업", "cwd": "/x",
                           "model": "claude-haiku-4-5",
                           "last_ts": iso(self.now - timedelta(minutes=1)),
                           "last_ctx": 140_000}}
        result = live.active_sessions(sessions, CFG, now=self.now)
        self.assertEqual(result[0]["limit"], 200_000)
        self.assertAlmostEqual(result[0]["ratio"], 0.70)
        self.assertEqual(result[0]["status"], "caution")

    def test_unknown_model_uses_the_configured_limit(self):
        sessions = {"s1": {"title": "작업", "cwd": "/x", "model": "알 수 없는 모델",
                           "last_ts": iso(self.now - timedelta(minutes=1)),
                           "last_ctx": 140_000}}
        result = live.active_sessions(sessions, CFG, now=self.now)
        self.assertEqual(result[0]["limit"], 1_000_000)

    def test_ratio_is_clamped_to_one(self):
        """실측: 한도가 어긋난 트레이스에서 ratio 4.400025 — 카드가 440%였다."""
        sessions = {"s1": {"title": "작업", "cwd": "/x",
                           "model": "claude-haiku-4-5",
                           "last_ts": iso(self.now - timedelta(minutes=1)),
                           "last_ctx": 880_005}}
        result = live.active_sessions(sessions, CFG, now=self.now)
        self.assertEqual(result[0]["ratio"], 1.0)
        self.assertEqual(result[0]["status"], "danger")

    def test_unparseable_timestamp_is_excluded(self):
        sessions = {"s1": {"title": "x", "cwd": "/x", "model": "m",
                           "last_ts": "not-a-date", "last_ctx": 1}}
        self.assertEqual(live.active_sessions(sessions, CFG, now=self.now), [])


if __name__ == "__main__":
    unittest.main()
