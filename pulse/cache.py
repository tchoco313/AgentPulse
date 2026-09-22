"""파일별 부분집계 캐시의 입출력."""
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1


def empty_cache():
    return {"schema": SCHEMA_VERSION, "files": {}}


def load_cache(path):
    """스키마가 다르거나 읽을 수 없으면 빈 캐시 — 전체 재스캔을 유도한다."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty_cache()

    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        return empty_cache()
    if not isinstance(data.get("files"), dict):
        return empty_cache()
    return data


def save_cache(path, cache):
    """임시 파일에 쓰고 rename — 쓰는 중에 죽어도 반쪽 캐시가 남지 않는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, path)
