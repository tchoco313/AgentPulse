"""파일별 부분집계 캐시의 입출력."""
import json
import os
import tempfile
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
    """임시 파일에 쓰고 rename — 쓰는 중에 죽어도 반쪽 캐시가 남지 않는다.

    임시 파일 이름은 호출마다 고유해야 한다. 고정 이름을 쓰면 두 스레드가 같은
    tmp를 쓰다가 한쪽이 os.replace로 치운 뒤 다른 쪽이 FileNotFoundError로
    죽는다(실측: 페이지 로드마다 두 요청 중 하나가 끊겼다).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
