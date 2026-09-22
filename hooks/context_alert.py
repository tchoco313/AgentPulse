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
    if n >= 1_000_000:
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


def context_used(transcript_path):
    """세션의 현재 컨텍스트 점유(토큰). 알 수 없으면 None.

    Stop 훅 payload에는 context_window가 없다(실측). transcript_path는 있으므로
    마지막 assistant 레코드의 usage에서 직접 계산한다. 서브에이전트(isSidechain)
    레코드는 부모와 세션 id를 공유하므로 건너뛴다 — 그러지 않으면 서브에이전트의
    점유를 사용자 것으로 착각한다.
    """
    if not transcript_path:
        return None
    try:
        lines = _tail_lines(transcript_path)
    except OSError:
        return None

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
        usage = (d.get("message") or {}).get("usage") or {}
        if not usage:
            continue
        return (usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0))
    return None


def should_alert(payload, cfg, seen, used):
    """(발화할지, 갱신된 seen). 세션당 한 번만 발화한다."""
    session = payload.get("session_id")
    limit = cfg["context_limit"]

    if not session or used is None or not limit:
        return False, seen
    if used / limit < cfg["warn_threshold"]:
        return False, seen
    if session in seen:
        return False, seen

    return True, {**seen, session: round(used / limit * 100, 1)}


def message(used, cfg):
    limit = cfg["context_limit"]
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

    used = context_used(payload.get("transcript_path"))
    fire, seen = should_alert(payload, cfg, seen, used)
    if not fire:
        return

    # 기록이 먼저다. 알린 뒤에 기록하면, 기록이 계속 실패할 때(디스크 가득,
    # 권한) 매 턴 알림이 다시 뜬다. 턴마다 울리는 알림은 한 번도 안 울리는
    # 것보다 나쁘다 — 사용자는 그냥 알림을 꺼버린다.
    alerted_path.parent.mkdir(parents=True, exist_ok=True)
    alerted_path.write_text(json.dumps(seen), encoding="utf-8")

    title, body = message(used, cfg)
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
