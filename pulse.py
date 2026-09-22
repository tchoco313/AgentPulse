#!/usr/bin/env python3
"""AgentPulse — Claude Code 사용량 대시보드."""
import argparse
from pathlib import Path

from pulse import config, inventory, server

CLAUDE_DIR = Path.home() / ".claude"
HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description="Claude Code 사용량 대시보드")
    ap.add_argument("--port", type=int, default=None, help="기본값은 설정 파일의 포트")
    args = ap.parse_args()

    cfg = config.load_config()
    port = args.port or cfg["port"]

    state = server.State(
        cfg=cfg,
        projects_root=CLAUDE_DIR / "projects",
        cache_path=CLAUDE_DIR / "pulse-cache.json",
        claude_json=inventory.DEFAULT_CLAUDE_JSON,
        skill_roots=None,
    )
    print(f"컨텍스트 한도 {cfg['context_limit']:,} · 경고 {cfg['warn_threshold']:.0%}")
    print("첫 실행은 트랜스크립트 전체를 읽느라 시간이 걸립니다…")
    server.serve(state, port, HERE / "web")


if __name__ == "__main__":
    main()
