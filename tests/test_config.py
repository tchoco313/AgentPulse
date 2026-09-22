import json
import unittest
from pathlib import Path

from pulse import config
from tests import tmpdir


class TestDetectContextLimit(unittest.TestCase):
    def _settings(self, data):
        d = tmpdir()
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
        d = tmpdir()
        p = d / "settings.json"
        p.write_text("{not json", encoding="utf-8")
        self.assertEqual(config.detect_context_limit(p), 200_000)


class TestLimitForModel(unittest.TestCase):
    """세션이 실제로 쓰는 모델로 컨텍스트 창을 고른다.

    settings.json 하나로 모든 세션을 재는 것이 실패하던 지점이다:
    `claude --model ...`은 settings.json을 다시 쓰지 않는다.
    """

    def test_known_1m_models(self):
        for m in ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
                  "claude-opus-4-6", "claude-sonnet-5", "claude-sonnet-4-6",
                  "claude-fable-5-1", "claude-fable-5"):
            with self.subTest(model=m):
                self.assertEqual(config.limit_for_model(m, 12345), 1_000_000)

    def test_dated_model_id_still_matches(self):
        self.assertEqual(
            config.limit_for_model("claude-opus-4-6-20260514", 12345), 1_000_000)

    def test_haiku_is_200k(self):
        self.assertEqual(config.limit_for_model("claude-haiku-4-5", 12345), 200_000)

    def test_unknown_model_falls_back_to_configured_limit(self):
        # 모르는 모델은 추측하지 않는다 — 설정값 그대로 쓴다
        self.assertEqual(config.limit_for_model("claude-mystery-9", 12345), 12345)

    def test_empty_model_falls_back(self):
        self.assertEqual(config.limit_for_model("", 12345), 12345)
        self.assertEqual(config.limit_for_model(None, 12345), 12345)


class TestLoadConfig(unittest.TestCase):
    def test_defaults_when_no_config_file(self):
        d = tmpdir()
        settings = d / "settings.json"
        settings.write_text(json.dumps({"model": "opus[1m]"}), encoding="utf-8")

        cfg = config.load_config(config_path=d / "absent.json", settings_path=settings)

        self.assertEqual(cfg["context_limit"], 1_000_000)
        self.assertEqual(cfg["warn_threshold"], 0.85)
        self.assertEqual(cfg["caution_threshold"], 0.70)
        self.assertEqual(cfg["active_window_minutes"], 15)
        self.assertEqual(cfg["port"], 8787)

    def test_user_config_overrides(self):
        d = tmpdir()
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
        d = tmpdir()
        settings = d / "settings.json"
        settings.write_text(json.dumps({"model": "opus[1m]"}), encoding="utf-8")
        user = d / "pulse-config.json"
        user.write_text(json.dumps({"context_limit": None}), encoding="utf-8")

        cfg = config.load_config(config_path=user, settings_path=settings)

        self.assertEqual(cfg["context_limit"], 1_000_000)


if __name__ == "__main__":
    unittest.main()
