"""설치돼 있지만 기간 내 쓰이지 않은 MCP 서버·스킬을 찾는다."""
import json
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_CLAUDE_JSON = Path.home() / ".claude.json"
DEFAULT_SKILL_ROOTS = [
    CLAUDE_DIR / "skills",
    CLAUDE_DIR / "plugins" / "cache",
]
"""모드 번들(~/.claude/modes)은 일부러 뺀다.

모드는 전환식이라 한 번에 하나만 로드되는데, 디스크에는 어느 쪽이 활성인지
표시가 없다. 전부 세면 실측 기준 미사용이 314개가 되어(ecc 번들 하나가
292개) 타일이 잡음이 된다. 사용자가 손댈 수 있는 것은 자기 스킬과 설치한
플러그인 스킬이다.
"""


def installed_mcp_servers(claude_json_path=DEFAULT_CLAUDE_JSON):
    try:
        with open(claude_json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return set(servers) if isinstance(servers, dict) else set()


def installed_skills(roots=None):
    """SKILL.md를 담은 디렉터리의 이름이 스킬 이름이다."""
    roots = DEFAULT_SKILL_ROOTS if roots is None else roots
    names = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for skill_md in root.rglob("SKILL.md"):
            names.add(skill_md.parent.name)
    return names


def _bare(label):
    """'superpowers:brainstorming' -> 'brainstorming'"""
    return label.rsplit(":", 1)[-1]


def unused(summary_tools, mcp, skills):
    used_mcp = {label for label, _ in summary_tools.get("mcp", [])}
    used_skill = {_bare(label) for label, _ in summary_tools.get("skill", [])}

    unused_mcp = sorted(mcp - used_mcp)
    unused_skill = sorted({s for s in skills if _bare(s) not in used_skill})

    return {"mcp": unused_mcp, "skill": unused_skill,
            "count": len(unused_mcp) + len(unused_skill)}
