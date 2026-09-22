"""활동 중인 세션과 컨텍스트 상태 판정. 순수 함수."""
import os
from datetime import datetime, timedelta, timezone

from pulse import config


def context_status(used, limit, caution, warn):
    if used is None or not limit:
        return "unknown"
    ratio = used / limit
    if ratio >= warn:
        return "danger"
    if ratio >= caution:
        return "caution"
    return "ok"


def _parse(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def active_sessions(sessions, cfg, now=None):
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None:
        # 순진한 datetime을 받아도 죽지 않는다 — last_ts는 항상 tz를 달고 오므로
        # 그대로 비교하면 TypeError가 나고, 5초마다 도는 페이지가 멈춘다
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(minutes=cfg["active_window_minutes"])

    rows = []
    for sid, s in sessions.items():
        when = _parse(s.get("last_ts") or "")
        if when is None or when < cutoff:
            continue
        used = s.get("last_ctx")
        model = s.get("model") or ""
        project = os.path.basename(s.get("cwd") or "") or "(알 수 없음)"
        # 세션마다 자기 모델의 창으로 잰다. 전역 cfg["context_limit"]은
        # settings.json에서 나오는데, `claude --model ...`은 settings.json을
        # 고치지 않는다 — 실측으로 실제 69%인 세션이 카드에 14% "여유"로
        # 떴고 훅도 조용했다. 모르는 모델일 때만 설정값으로 떨어진다.
        limit = config.limit_for_model(model, cfg["context_limit"])
        ratio = None
        if used is not None and limit:
            # 1.0으로 자른다. 한도가 어긋난 트레이스에서 4.400025가 나와
            # 카드가 440%를 찍은 적이 있다(실측).
            ratio = min(1.0, used / limit)
        rows.append({
            "session": sid,
            "title": s.get("title") or project,
            "project": project,
            "model": model,
            "last_ts": s.get("last_ts") or "",
            "used": used,
            "limit": limit,
            "ratio": ratio,
            "status": context_status(used, limit, cfg["caution_threshold"],
                                     cfg["warn_threshold"]),
        })

    rows.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0)))
    return rows
