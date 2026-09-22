#!/usr/bin/env python3
"""AgentPulse를 `~/.claude`에 연결한다 — Stop 훅 등록과 statusline `⚠` 패치.

경고 채널은 셋이다: 카드, statusline의 `⚠`, macOS 알림. 그런데 `⚠`는 이
기기의 `~/.claude/statusline.py`를 손으로 고친 것뿐이어서, 저장소를 복제하면
세 채널 중 둘만 따라온다. 게다가 원복 절차는 `statusline.py`를 백업으로
되돌리는데 — 그러면 `⚠`가 지워지고, 되살릴 원본이 저장소 어디에도 없었다.
그래서 패치를 여기에 담고, 적용과 원복을 둘 다 이 스크립트가 맡는다.

    python3 install.py              # 연결
    python3 install.py --uninstall  # 원복

`--claude-dir`로 대상 디렉터리를 주입할 수 있다. 테스트는 항상 그렇게 하고,
실제 `~/.claude`를 건드리지 않는다.
"""
import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
HOOK_REL = Path("hooks") / "context_alert.py"

# ---- statusline 패치 -------------------------------------------------------
# 이 기기의 패치 전/후 statusline.py를 그대로 뜬 것이다. 패치 엔진을 쓰지 않고
# 정확한 문자열 치환으로 간다 — 맞지 않으면 조용히 어긋나는 대신 멈춰야 한다.
STATUSLINE_ANCHOR = 'SEP   = f"{DIM} · {RESET}"\n'
STATUSLINE_CONST = "WARN_AT = 85  # AgentPulse와 같은 임계값\n"

STATUSLINE_ORIGINAL = r'''ctx = (d.get("context_window") or {}).get("used_percentage")
if isinstance(ctx, (int, float)):
    parts.append(f"{DIM}ctx{RESET} {color(ctx)}{ctx:.0f}%{RESET}")
'''

STATUSLINE_PATCHED = r'''ctx_win = d.get("context_window") or {}
ctx = ctx_win.get("used_percentage")
if isinstance(ctx, (int, float)):
    chunk = f"{DIM}ctx{RESET} {color(ctx)}{ctx:.0f}%{RESET}"
    if ctx >= WARN_AT:
        # 임계 초과 — 남은 토큰을 붙여 정리할 시점임을 알린다
        size = ctx_win.get("context_window_size") or ctx_win.get("max_tokens")
        if isinstance(size, (int, float)) and size > 0:
            left_tok = int(size * (100 - ctx) / 100)
            human = (f"{left_tok/1000:.0f}K" if left_tok >= 1000 else str(left_tok))
            chunk = f"\033[31m⚠{RESET} {chunk}{DIM}({human} 남음){RESET}"
        else:
            chunk = f"\033[31m⚠{RESET} {chunk}"
    parts.append(chunk)
'''


class InstallError(Exception):
    """되돌릴 수 있는 실패. main이 백업을 복구하고 비정상 종료한다."""


# ---- settings.json ---------------------------------------------------------
def hook_command(repo_root=REPO_ROOT):
    """이 체크아웃의 훅 명령. 경로를 박아두지 않는다 — 복제본마다 다르다."""
    return f"python3 {Path(repo_root).resolve() / HOOK_REL}"


def stop_commands(settings):
    """등록된 Stop 훅 명령 목록. 구조가 없으면 빈 리스트."""
    stop = (settings.get("hooks") or {}).get("Stop") or []
    if not stop or not isinstance(stop[0], dict):
        return []
    return [h.get("command", "") for h in (stop[0].get("hooks") or [])
            if isinstance(h, dict)]


def _stop_entries(settings):
    """hooks.Stop[0].hooks 리스트를 (없으면 만들어) 돌려준다."""
    stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
    if not stop or not isinstance(stop[0], dict):
        stop.insert(0, {"hooks": []})
    if not isinstance(stop[0].get("hooks"), list):
        stop[0]["hooks"] = []
    return stop[0]["hooks"]


def add_hook(settings, command):
    """(새 settings, 바뀌었는지). 뒤에 덧붙일 뿐 기존 항목은 건드리지 않는다.

    같은 command가 이미 있으면 아무것도 하지 않는다 — 두 번 돌려도 사본이
    생기지 않아야 한다.
    """
    settings = copy.deepcopy(settings)
    entries = _stop_entries(settings)
    if any(isinstance(h, dict) and h.get("command") == command for h in entries):
        return settings, False
    entries.append({"type": "command", "command": command, "async": True})
    return settings, True


def remove_hook(settings, command):
    """(새 settings, 바뀌었는지). 우리 항목만 뺀다 — afplay 등은 그대로 둔다."""
    if command not in stop_commands(settings):
        return settings, False
    settings = copy.deepcopy(settings)
    entries = _stop_entries(settings)
    entries[:] = [h for h in entries
                  if not (isinstance(h, dict) and h.get("command") == command)]
    return settings, True


# ---- statusline.py ---------------------------------------------------------
def patch_statusline(text):
    """(새 내용, 바뀌었는지). 이미 적용돼 있으면 그대로 돌려준다."""
    if STATUSLINE_PATCHED in text:
        return text, False
    if STATUSLINE_ORIGINAL not in text or STATUSLINE_ANCHOR not in text:
        raise InstallError(
            "statusline.py가 예상한 모양이 아닙니다 — 패치를 건너뜁니다.\n"
            "  직접 넣으려면 install.py의 STATUSLINE_PATCHED를 보세요.")
    if STATUSLINE_CONST not in text:
        text = text.replace(STATUSLINE_ANCHOR,
                            STATUSLINE_ANCHOR + STATUSLINE_CONST, 1)
    return text.replace(STATUSLINE_ORIGINAL, STATUSLINE_PATCHED, 1), True


def unpatch_statusline(text):
    """(새 내용, 바뀌었는지). 패치 전 모양으로 정확히 되돌린다."""
    if STATUSLINE_PATCHED not in text and STATUSLINE_CONST not in text:
        return text, False
    text = text.replace(STATUSLINE_ANCHOR + STATUSLINE_CONST,
                        STATUSLINE_ANCHOR, 1)
    return text.replace(STATUSLINE_PATCHED, STATUSLINE_ORIGINAL, 1), True


# ---- 파일 입출력 -----------------------------------------------------------
def backup(path, stamp):
    """고치기 전 타임스탬프 사본. 없는 파일은 백업할 것도 없다."""
    path = Path(path)
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak-pulse-{stamp}")
    shutil.copy2(path, dest)
    return dest


def _write(path, text):
    """임시 파일에 쓰고 rename — 쓰는 중에 죽어도 반쪽 settings.json이 없다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if path.exists():
            # 실행 권한(statusline.py)만 지킨다. copystat은 atime/mtime까지
            # 옮겨 쓰기 전 값 그대로 만들어버린다 — 그러면 내용이 바뀌었는데도
            # mtime이 똑같아서, mtime으로 변경을 감지하는 쪽(예: settings.json을
            # 지켜보는 도구)이 이 쓰기를 놓친다.
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_settings(path):
    """없으면 빈 dict. 깨져 있으면 멈춘다 — 덮어쓰면 사용자 설정이 날아간다."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise InstallError(f"{path}가 올바른 JSON이 아닙니다 ({e}). "
                           "먼저 고친 뒤 다시 실행하세요.") from e
    if not isinstance(data, dict):
        raise InstallError(f"{path}의 최상위가 객체가 아닙니다.")
    return data


def write_settings(path, settings):
    _write(path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")


def validate_settings(path, expected_commands):
    """다시 읽어 JSON이 성한지, 있어야 할 훅이 전부 남았는지 본다."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InstallError(f"쓰고 난 {path}를 다시 읽지 못했습니다: {e}") from e
    present = stop_commands(data)
    missing = [c for c in expected_commands if c not in present]
    if missing:
        raise InstallError("Stop 훅에 있어야 할 항목이 없습니다: "
                           + ", ".join(missing))
    return present


def restore(backup_path, target):
    if backup_path is not None:
        shutil.copy2(backup_path, target)


# ---- 실행 ------------------------------------------------------------------
def run(claude_dir, repo_root=REPO_ROOT, uninstall=False, stamp=None,
        out=print):
    """설치(또는 원복)를 수행하고 바뀐 것들을 돌려준다."""
    claude_dir = Path(claude_dir)
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"
    statusline_path = claude_dir / "statusline.py"
    command = hook_command(repo_root)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")

    # 둘 중 하나라도 건드리기 전에 둘 다 백업한다
    settings_bak = backup(settings_path, stamp)
    statusline_bak = backup(statusline_path, stamp)
    for label, b in (("settings.json", settings_bak),
                     ("statusline.py", statusline_bak)):
        out(f"  백업 {label} → {b}" if b else f"  백업 {label} → (파일 없음)")

    changed = {"hook": False, "statusline": False,
               "backups": {"settings": settings_bak, "statusline": statusline_bak}}

    settings = read_settings(settings_path)
    before = stop_commands(settings)
    new_settings, changed["hook"] = (
        remove_hook(settings, command) if uninstall
        else add_hook(settings, command))

    if changed["hook"]:
        write_settings(settings_path, new_settings)
        expected = [c for c in before if c != command]
        if not uninstall:
            expected = expected + [command]
        try:
            validate_settings(settings_path, expected)
        except InstallError:
            restore(settings_bak, settings_path)
            raise
        out(f"  Stop 훅 {'해제' if uninstall else '등록'}: {command}")
    else:
        out(f"  Stop 훅 — 할 일 없음 ({'이미 없음' if uninstall else '이미 등록됨'})")

    if statusline_path.exists():
        text = statusline_path.read_text(encoding="utf-8")
        try:
            new_text, changed["statusline"] = (
                unpatch_statusline(text) if uninstall else patch_statusline(text))
        except InstallError as e:
            out(f"  ! statusline.py: {e}")
        else:
            if changed["statusline"]:
                _write(statusline_path, new_text)
                out(f"  statusline ⚠ {'제거' if uninstall else '적용'}: "
                    f"{statusline_path}")
            else:
                out("  statusline ⚠ — 할 일 없음 "
                    f"({'이미 없음' if uninstall else '이미 적용됨'})")
    else:
        out(f"  statusline.py 없음 — 건너뜁니다 ({statusline_path})")

    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="AgentPulse를 ~/.claude에 연결한다 (Stop 훅 + statusline ⚠)")
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"),
                    help="대상 디렉터리. 기본값 ~/.claude (테스트는 임시 디렉터리를 준다)")
    ap.add_argument("--repo-root", default=str(REPO_ROOT),
                    help="훅 경로를 만들 체크아웃 위치. 기본값은 이 파일이 있는 곳")
    ap.add_argument("--uninstall", action="store_true",
                    help="훅 등록과 statusline 패치를 되돌린다 (afplay 항목은 그대로)")
    args = ap.parse_args(argv)

    print("AgentPulse 원복" if args.uninstall else "AgentPulse 설치")
    try:
        run(args.claude_dir, repo_root=args.repo_root, uninstall=args.uninstall)
    except InstallError as e:
        print(f"실패: {e}", file=sys.stderr)
        return 1
    print("끝. 새 터미널이나 새 Claude Code 세션부터 반영됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
