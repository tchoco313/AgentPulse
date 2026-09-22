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
