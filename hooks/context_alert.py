#!/usr/bin/env python3
"""Stop 훅: 컨텍스트가 임계값을 넘으면 macOS 알림을 한 번 띄운다.

절대 실패하지 않는다 — 어떤 예외에도 exit 0 한다. 이 훅이 죽으면
Claude Code의 턴이 막힌다.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

# Claude Code는 훅을 **사용자의 프로젝트 디렉터리**를 cwd로 실행한다.
# 이 줄이 없으면 `from pulse import config`가 실패하고, 아래에서 모든 예외를
# 삼키도록 설계돼 있어 아무 알림도 안 뜬 채 조용히 끝난다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_ALERTED_PATH = CLAUDE_DIR / "pulse-alerted.json"


def _human(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def should_alert(payload, cfg, seen):
    """(발화할지, 갱신된 seen). 세션당 한 번만 발화한다."""
    session = payload.get("session_id")
    window = payload.get("context_window") or {}
    pct = window.get("used_percentage")

    if not session or not isinstance(pct, (int, float)):
        return False, seen
    if pct / 100 < cfg["warn_threshold"]:
        return False, seen
    if session in seen:
        return False, seen

    return True, {**seen, session: pct}


def message(payload, cfg):
    pct = (payload.get("context_window") or {}).get("used_percentage") or 0
    limit = cfg["context_limit"]
    remaining = max(0, int(limit * (100 - pct) / 100))
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

    fire, seen = should_alert(payload, cfg, seen)
    if not fire:
        return

    # 기록이 먼저다. 알린 뒤에 기록하면, 기록이 계속 실패할 때(디스크 가득,
    # 권한) 매 턴 알림이 다시 뜬다. 턴마다 울리는 알림은 한 번도 안 울리는
    # 것보다 나쁘다 — 사용자는 그냥 알림을 꺼버린다.
    alerted_path.parent.mkdir(parents=True, exist_ok=True)
    alerted_path.write_text(json.dumps(seen), encoding="utf-8")

    title, body = message(payload, cfg)
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
