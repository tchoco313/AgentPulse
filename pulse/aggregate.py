"""이벤트 -> 부분집계(fold) -> 기간 집계(merge).

순수 함수만 있다. 파일 시스템을 모른다.
"""
from datetime import datetime

GROUPS = ("mcp", "skill", "tools")


def classify_tool(name, skill):
    """도구 이름을 (그룹, 표시 라벨)로 나눈다."""
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3 and parts[1]:
            return "mcp", parts[1]
        return "tools", name
    if name == "Skill":
        return "skill", skill or "(미상)"
    return "tools", name


def local_day(ts):
    """UTC ISO 문자열 -> 로컬 타임존의 YYYY-MM-DD.

    사용자가 보는 '하루'는 로컬 날짜다. UTC로 버킷하면 KST 오전 작업이
    전날로 밀린다.
    """
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone().strftime("%Y-%m-%d")


def empty_partial():
    return {"days": {}, "compacts": [], "sessions": {}, "bad_lines": 0}


def _empty_day():
    """도구 집계가 날짜 안에 있다 — 기간 필터가 순위에도 걸려야 하기 때문이다."""
    return {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
            "calls": {g: 0 for g in GROUPS},
            "labels": {g: {} for g in GROUPS}}


def _day_bucket(partial, day):
    return partial["days"].setdefault(day, _empty_day())


def _session(partial, sid):
    return partial["sessions"].setdefault(sid, {
        "title": None, "cwd": "", "model": "", "last_ts": "", "last_ctx": None,
    })


def fold(events, partial=None):
    """이벤트들을 부분집계에 접어 넣는다. partial을 주면 그 위에 누적한다.

    `sub`가 참인 이벤트(서브에이전트)의 토큰과 도구 호출은 일별 집계에 들어간다 —
    실제로 쓴 토큰이다. 다만 세션 상태(last_ts/last_ctx)는 갱신하지 않는다.
    서브에이전트는 부모와 같은 sessionId를 쓰기 때문에, 갱신하면 사용자의 실시간
    카드가 서브에이전트의 컨텍스트 점유를 사용자 것으로 표시한다.
    """
    p = empty_partial() if partial is None else partial

    for e in events:
        kind = e.get("kind")
        sid = e.get("session") or ""

        if kind == "usage":
            day = local_day(e["ts"])
            if day:
                b = _day_bucket(p, day)
                for k in ("input", "output", "cache_read", "cache_creation"):
                    b[k] += e.get(k, 0)
            if e.get("sub"):
                continue  # 토큰은 셌다. 세션 상태는 부모 것이므로 건드리지 않는다
            s = _session(p, sid)
            if e["ts"] >= s["last_ts"]:
                s["last_ts"] = e["ts"]
                s["last_ctx"] = (e.get("input", 0) + e.get("cache_read", 0)
                                 + e.get("cache_creation", 0))
                s["cwd"] = e.get("cwd") or s["cwd"]
                s["model"] = e.get("model") or s["model"]

        elif kind == "tool":
            group, label = classify_tool(e.get("name") or "", e.get("skill"))
            day = local_day(e["ts"])
            if day:
                b = _day_bucket(p, day)
                b["calls"][group] += 1
                b["labels"][group][label] = b["labels"][group].get(label, 0) + 1

        elif kind == "compact":
            pre, post = e.get("pre", 0), e.get("post", 0)
            p["compacts"].append({
                "ts": e["ts"], "day": local_day(e["ts"]),
                "trigger": e.get("trigger") or "unknown",
                "pre": pre, "post": post, "dropped": max(0, pre - post),
            })

        elif kind == "title":
            _session(p, sid)["title"] = e.get("title") or None

        elif kind == "activity":
            if e.get("sub"):
                continue
            s = _session(p, sid)
            if e["ts"] >= s["last_ts"]:
                s["last_ts"] = e["ts"]

    return p


def _merge_sessions(target, incoming):
    for sid, s in incoming.items():
        cur = target.get(sid)
        if cur is None:
            target[sid] = dict(s)
            continue
        # 제목은 한 번이라도 붙었으면 유지
        if s.get("title") and not cur.get("title"):
            cur["title"] = s["title"]
        if s.get("last_ts", "") >= cur.get("last_ts", ""):
            cur["last_ts"] = s["last_ts"]
            if s.get("last_ctx") is not None:
                cur["last_ctx"] = s["last_ctx"]
            cur["cwd"] = s.get("cwd") or cur.get("cwd", "")
            cur["model"] = s.get("model") or cur.get("model", "")


def merge(partials, since=None):
    """부분집계들을 기간(since 이상)으로 걸러 합친다."""
    days = {}
    tools = {g: {} for g in GROUPS}
    compacts = []
    sessions = {}
    bad_lines = 0

    for p in partials:
        bad_lines += p.get("bad_lines", 0)
        _merge_sessions(sessions, p.get("sessions", {}))

        for day, b in p.get("days", {}).items():
            if since and day < since:
                continue
            t = days.setdefault(day, _empty_day())
            for k in ("input", "output", "cache_read", "cache_creation"):
                t[k] += b.get(k, 0)
            for g in GROUPS:
                t["calls"][g] += (b.get("calls") or {}).get(g, 0)
                for label, cnt in ((b.get("labels") or {}).get(g) or {}).items():
                    tools[g][label] = tools[g].get(label, 0) + cnt

        for c in p.get("compacts", []):
            if since and c.get("day", "") < since:
                continue
            compacts.append(c)

    total_calls = sum(sum(d["calls"].values()) for d in days.values())
    totals = {k: sum(d[k] for d in days.values())
              for k in ("input", "output", "cache_read", "cache_creation")}
    totals["total"] = sum(totals.values())
    totals["calls"] = total_calls
    totals["cache_read_ratio"] = (
        totals["cache_read"] / totals["total"] if totals["total"] else 0.0
    )

    dropped_total = sum(c["dropped"] for c in compacts)
    compact_stats = {
        "count": len(compacts),
        "auto": sum(1 for c in compacts if c["trigger"] == "auto"),
        "manual": sum(1 for c in compacts if c["trigger"] != "auto"),
        "dropped_total": dropped_total,
        "dropped_avg": dropped_total // len(compacts) if compacts else 0,
    }

    return {
        "days": [{"day": d,
                  "total": sum(v[k] for k in ("input", "output",
                                              "cache_read", "cache_creation")),
                  **{k: v[k] for k in ("input", "output", "cache_read",
                                       "cache_creation", "calls")}}
                 for d, v in sorted(days.items())],
        "totals": totals,
        "tools": {g: sorted(([label, n] for label, n in tools[g].items()),
                            key=lambda x: (-x[1], x[0])) for g in GROUPS},
        "compacts": compact_stats,
        "sessions": sessions,
        "bad_lines": bad_lines,
    }
