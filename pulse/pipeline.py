"""스캔·집계·캐시를 조립한다. 계산도 입출력도 스스로 하지 않는다."""
import os
from datetime import date, timedelta

from pulse import aggregate as agg
from pulse import cache as cache_mod
from pulse import scan

RANGES = {"7d": 7, "30d": 30}


def is_subagent(path):
    """서브에이전트 트랜스크립트는 <프로젝트>/<세션>/subagents/ 아래에 있다."""
    return "/subagents/" in str(path)


def since_for(range_key, today=None):
    """기간 키 -> 포함 시작일. 'all'이면 None."""
    if range_key == "all":
        return None
    days = RANGES.get(range_key, 7)
    base = date.fromisoformat(today) if today else date.today()
    return (base - timedelta(days=days - 1)).isoformat()


def refresh(projects_root, cache_path):
    """변한 파일만 읽어 캐시를 갱신하고, 전체 부분집계 리스트를 낸다."""
    cache = cache_mod.load_cache(cache_path)
    files = scan.find_transcripts(projects_root)
    known = cache["files"]
    fresh = {}

    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            continue

        entry = known.get(path)
        if (entry and entry.get("size") == st.st_size
                and entry.get("mtime") == st.st_mtime
                and isinstance(entry.get("partial"), dict)):
            fresh[path] = entry  # 변한 게 없다 — 읽지 않는다
            continue

        if entry and st.st_size >= entry.get("offset", 0):
            offset = entry.get("offset", 0)
            partial = entry.get("partial") or agg.empty_partial()
        else:
            offset = 0  # 새 파일이거나 잘렸다 — 처음부터
            partial = agg.empty_partial()

        events, new_offset, bad = scan.scan_file(path, offset)
        partial = agg.fold(events, partial, subagent=is_subagent(path))
        partial["bad_lines"] = partial.get("bad_lines", 0) + bad

        fresh[path] = {"size": st.st_size, "mtime": st.st_mtime,
                       "offset": new_offset, "partial": partial}

    cache["files"] = fresh  # 사라진 파일은 여기서 탈락한다
    cache_mod.save_cache(cache_path, cache)

    return [e["partial"] for e in fresh.values()], cache
