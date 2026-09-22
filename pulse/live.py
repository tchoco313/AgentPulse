"""활동 중인 세션과 컨텍스트 상태 판정. 순수 함수."""
import os
from datetime import datetime, timedelta, timezone


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
    limit = cfg["context_limit"]

    rows = []
    for sid, s in sessions.items():
        if s.get("sub"):
            continue  # 서브에이전트 세션 — 총계에는 있지만 카드에는 안 띄운다
        when = _parse(s.get("last_ts") or "")
        if when is None or when < cutoff:
            continue
        used = s.get("last_ctx")
        project = os.path.basename(s.get("cwd") or "") or "(알 수 없음)"
        rows.append({
            "session": sid,
            "title": s.get("title") or project,
            "project": project,
            "model": s.get("model") or "",
            "last_ts": s.get("last_ts") or "",
            "used": used,
            "limit": limit,
            "ratio": (used / limit) if (used is not None and limit) else None,
            "status": context_status(used, limit, cfg["caution_threshold"],
                                     cfg["warn_threshold"]),
        })

    rows.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0)))
    return rows
