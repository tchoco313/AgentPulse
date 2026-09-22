"""테스트 공용 도우미.

`tempfile.mkdtemp()`를 그냥 쓰면 만든 디렉터리가 절대 지워지지 않는다 —
실측으로 한 번 돌 때마다 71개가 남았고, 이 기기에는 이미 2,255개가 쌓여
있었다. 모든 임시 디렉터리를 루트 하나 아래에 만들고, 프로세스가 끝날 때
그 루트만 통째로 지운다. (테스트는 서브프로세스도 띄우므로 모듈 단위
tearDown보다 프로세스 종료 시점이 안전하다.)
"""
import atexit
import shutil
import tempfile
from pathlib import Path

_ROOT = tempfile.mkdtemp(prefix="agentpulse-tests-")
atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)


def tmpdir():
    """이 실행이 끝날 때 함께 지워지는 임시 디렉터리."""
    return Path(tempfile.mkdtemp(dir=_ROOT))
