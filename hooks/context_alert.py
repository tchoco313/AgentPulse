#!/usr/bin/env python3
"""Stop 훅: 컨텍스트가 임계값을 넘으면 macOS 알림을 한 번 띄운다.

절대 실패하지 않는다 — 어떤 예외에도 exit 0 한다. 이 훅이 죽으면
Claude Code의 턴이 막힌다.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Claude Code는 훅을 **사용자의 프로젝트 디렉터리**를 cwd로 실행한다.
# 이 줄이 없으면 `from pulse import config`가 실패하고, 아래에서 모든 예외를
# 삼키도록 설계돼 있어 아무 알림도 안 뜬 채 조용히 끝난다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_ALERTED_PATH = CLAUDE_DIR / "pulse-alerted.json"

TAIL_BYTES = 512 * 1024


def _human(n):
    # 경계는 "반올림해서 1000이 되는 값"에 둔다 — 1_000_000에 두면 999,999가
    # "1000K"로 나온다(web/app.js의 human()과 같은 이유, 같은 경계).
    if n >= 999_950:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def _tail_lines(path, max_bytes=TAIL_BYTES):
    """파일 끝에서 최대 max_bytes만 읽어 줄 목록으로 돌려준다.

    훅은 매 턴 돈다. 트랜스크립트는 수십 MB까지 자라므로 전체를 읽으면
    턴마다 지연이 붙는다.
    """
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - max_bytes)
        f.seek(start)
        data = f.read()
    lines = data.split(b"\n")
    if start:
        lines = lines[1:]  # 잘렸을 첫 줄은 버린다
    return lines


def last_assistant(transcript_path):
    """마지막 사용자 턴의 (점유 토큰, 모델). 알 수 없으면 (None, None).

    Stop 훅 payload에는 context_window가 없다(실측). transcript_path는 있으므로
    마지막 assistant 레코드의 usage에서 직접 계산한다. 서브에이전트(isSidechain)
    레코드는 부모와 세션 id를 공유하므로 건너뛴다 — 그러지 않으면 서브에이전트의
    점유를 사용자 것으로 착각한다.

    모델도 같이 낸다. 이미 읽고 있는 그 레코드에 들어 있고, 세션이 실제로
    쓰는 컨텍스트 창을 고르는 데 필요하다.
    """
    if not transcript_path:
        return None, None
    try:
        lines = _tail_lines(transcript_path)
    except OSError:
        return None, None

    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") != "assistant":
            continue
        if d.get("isSidechain"):
            continue
        msg = d.get("message") or {}
        usage = msg.get("usage") or {}
        if not usage:
            continue
        used = (usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0))
        return used, (msg.get("model") or None)
    return None, None


def context_used(transcript_path):
    """세션의 현재 컨텍스트 점유(토큰). 알 수 없으면 None."""
    return last_assistant(transcript_path)[0]


def limit_for(cfg, model):
    """이 세션의 컨텍스트 창. 모르는 모델이면 설정값(cfg)으로 떨어진다.

    import를 함수 안에서 한다 — 모듈 최상단에서 하면 pulse를 못 찾을 때
    import 단계에서 죽고, 아래의 '무슨 일이 있어도 exit 0' 보호막 밖이다.

    표는 설정값을 아래로만 조정한다 — 위로 부풀리지 않는다. transcript의
    model은 `claude-opus-5`처럼 맨 API id로 오고 `[1m]`이 붙었는지는
    알려주지 않으므로, 표가 1M로 재도 이 계정이 실제로 1M 창을 쓰는지는
    표만으로 알 수 없다. 과소평가는 일찍 경고하고, 과대평가는 영원히
    경고하지 않는다 — 그래서 설정값(cfg["context_limit"])은 상한이다.
    """
    from pulse import config
    return min(config.limit_for_model(model, cfg["context_limit"]),
               cfg["context_limit"])


def should_alert(payload, cfg, seen, used, model=None):
    """(발화할지, 갱신된 seen). **한 번 찬 컨텍스트당** 한 번만 발화한다.

    seen은 '이번 채움에서 이미 알렸다'는 표시이고, 점유가 caution 아래로
    떨어지면(= 압축이 걸리면) 지워져 다시 장전된다. 세션당 한 번만 울리면
    긴 세션에서 두 번째·세 번째 압축을 통째로 놓친다 — 실측으로 압축이
    걸린 3개 세션 중 2개가 세 번씩 압축됐고, 그 4번을 전부 놓쳤다.

    값은 존재 여부만 읽는다. 예전에는 알린 시점의 %를 넣어뒀지만 어디서도
    읽지 않는 죽은 값이었다.
    """
    session = payload.get("session_id")
    limit = limit_for(cfg, model)

    if not session or used is None or not limit:
        return False, seen

    ratio = used / limit
    if ratio >= cfg["warn_threshold"]:
        if session in seen:
            return False, seen  # 같은 채움에 두 번 울리지 않는다
        return True, {**seen, session: True}

    if ratio < cfg["caution_threshold"] and session in seen:
        # 압축이 걸렸거나 세션이 비워졌다 — 다음 채움을 위해 장전한다
        return False, {k: v for k, v in seen.items() if k != session}

    return False, seen


def message(used, cfg, model=None):
    limit = limit_for(cfg, model)
    pct = used / limit * 100
    remaining = max(0, limit - used)
    title = "컨텍스트 경고"
    body = (f"컨텍스트 {pct:.0f}% 사용 · 약 {_human(remaining)} 남음. "
            f"정리하거나 새 세션으로 옮길 때입니다.")
    return title, body


def _notify(title, body):
    """osascript로 알림 배너. 인자는 -e 문자열이 아니라 별도 인자로 넘긴다."""
    script = ('on run argv\n'
              '  display notification (item 1 of argv) with title (item 2 of argv)\n'
              'end run')
    subprocess.run(["osascript", "-e", script, body, title],
                   capture_output=True, timeout=5)


def _write_seen(path, seen):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerted-path", default=str(DEFAULT_ALERTED_PATH))
    # 설정 경로도 주입할 수 있어야 테스트가 실제 ~/.claude를 읽지 않는다.
    ap.add_argument("--config-path", default=None)
    ap.add_argument("--settings-path", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="알림을 띄우는 대신 표준출력에 찍는다 (테스트용)")
    args = ap.parse_args()

    from pulse import config

    payload = json.loads(sys.stdin.read())
    cfg = config.load_config(config_path=args.config_path,
                             settings_path=args.settings_path)

    alerted_path = Path(args.alerted_path)
    try:
        seen = json.loads(alerted_path.read_text(encoding="utf-8"))
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, ValueError):
        seen = {}
    prev_seen = dict(seen)

    used, model = last_assistant(payload.get("transcript_path"))
    fire, seen = should_alert(payload, cfg, seen, used, model)
    if not fire:
        # 발화하지 않아도 seen이 줄었을 수 있다(점유가 떨어져 장전된 경우).
        # 그 상태를 기록해두지 않으면 다음 채움에서 또 억제된다.
        if seen != prev_seen:
            _write_seen(alerted_path, seen)
        return

    # 기록이 먼저다. 알린 뒤에 기록하면, 기록이 계속 실패할 때(디스크 가득,
    # 권한) 매 턴 알림이 다시 뜬다. 턴마다 울리는 알림은 한 번도 안 울리는
    # 것보다 나쁘다 — 사용자는 그냥 알림을 꺼버린다.
    _write_seen(alerted_path, seen)

    title, body = message(used, cfg, model)
    if args.dry_run:
        print(f"{title}: {body}")
    else:
        _notify(title, body)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Exception만 잡으면 argparse의 SystemExit이 새어나가 종료코드가 2가 된다.
        # 훅은 무슨 일이 있어도 Claude Code를 막지 않는다.
        pass
    sys.exit(0)
