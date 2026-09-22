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
