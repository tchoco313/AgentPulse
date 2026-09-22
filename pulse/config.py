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
    """settings.json의 모델 문자열로 컨텍스트 창 크기를 추정한다.

    세션이 자기 모델을 알려줄 때는 limit_for_model이 우선이다. 이 값은
    모델을 모를 때의 기본값 — `claude --model ...`은 settings.json을
    다시 쓰지 않으므로 settings.json만으로는 세션별 실제 창을 알 수 없다.
    """
    model = _read_json(settings_path).get("model") or ""
    return 1_000_000 if "[1m]" in model else 200_000


MODEL_LIMITS = {
    "opus-5": 1_000_000,
    "opus-4-8": 1_000_000,
    "opus-4-7": 1_000_000,
    "opus-4-6": 1_000_000,
    "sonnet-5": 1_000_000,
    "sonnet-4-6": 1_000_000,
    "fable-5-1": 1_000_000,
    "fable-5": 1_000_000,
    "haiku": 200_000,
}
"""모델 이름 조각 -> 컨텍스트 창 크기.

트랜스크립트의 model은 `claude-opus-4-6-20260514`처럼 날짜가 붙어 오므로
정확히 일치시키지 않고 조각이 들어 있는지로 본다. 목록에 없는 모델은
추측하지 않는다 — 잘못 추측한 한도는 조용한 자동 압축으로 이어지는데,
그게 바로 이 도구가 막으려는 일이다.
"""

# 긴 조각부터 본다. 'fable-5-1'이 'fable-5'보다 먼저 걸려야 한다.
_MODEL_KEYS = sorted(MODEL_LIMITS, key=len, reverse=True)


def limit_for_model(model, fallback):
    """이 모델의 컨텍스트 창. 모르는 모델이면 fallback(설정값)."""
    name = (model or "").lower()
    for key in _MODEL_KEYS:
        if key in name:
            return MODEL_LIMITS[key]
    return fallback


def load_config(config_path=None, settings_path=None):
    config_path = DEFAULT_CONFIG_PATH if config_path is None else config_path
    settings_path = DEFAULT_SETTINGS_PATH if settings_path is None else settings_path

    cfg = dict(DEFAULTS)
    cfg.update(_read_json(config_path))

    if cfg.get("context_limit") is None:
        cfg["context_limit"] = detect_context_limit(settings_path)

    return cfg
