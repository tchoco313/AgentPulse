"""트랜스크립트 JSONL을 증분으로 읽어 정규화 이벤트를 낸다.

디스크만 안다 — 집계가 무엇인지는 모른다.
"""
import json
from pathlib import Path


def find_transcripts(root):
    """<root>/<project>/<session>.jsonl 들을 정렬해 돌려준다."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(str(p) for p in root.glob("*/*.jsonl"))


def _int(d, key):
    v = d.get(key)
    return v if isinstance(v, int) else 0


def parse_line(line):
    """한 줄 -> 이벤트 리스트. 관심 없거나 깨진 줄은 None."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None

    kind = d.get("type")
    session = d.get("sessionId") or ""
    ts = d.get("timestamp") or ""

    if kind == "assistant":
        msg = d.get("message") or {}
        usage = msg.get("usage") or {}
        events = [{
            "kind": "usage", "ts": ts, "session": session,
            "cwd": d.get("cwd") or "", "model": msg.get("model") or "",
            "input": _int(usage, "input_tokens"),
            "output": _int(usage, "output_tokens"),
            "cache_read": _int(usage, "cache_read_input_tokens"),
            "cache_creation": _int(usage, "cache_creation_input_tokens"),
        }]
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input")
                skill = inp.get("skill") if isinstance(inp, dict) else None
                events.append({
                    "kind": "tool", "ts": ts, "session": session,
                    "name": block.get("name") or "",
                    "skill": skill if isinstance(skill, str) else None,
                })
        return events

    if kind == "system" and d.get("subtype") == "compact_boundary":
        meta = d.get("compactMetadata") or {}
        return [{
            "kind": "compact", "ts": ts, "session": session,
            "trigger": meta.get("trigger") or "unknown",
            "pre": _int(meta, "preTokens"), "post": _int(meta, "postTokens"),
        }]

    if kind == "ai-title":
        return [{"kind": "title", "session": session,
                 "title": d.get("aiTitle") or ""}]

    if kind == "user":
        return [{"kind": "activity", "ts": ts, "session": session}]

    return None


def scan_file(path, offset=0):
    """offset부터 줄 단위로 읽는다. (events, new_offset, bad_lines).

    개행으로 끝나지 않는 마지막 줄은 아직 쓰이는 중일 수 있으므로 버리고,
    offset을 그 줄 시작으로 물린다.
    """
    events = []
    bad = 0
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            pos = offset
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # 미완성 줄 — pos를 올리지 않는다
                pos += len(raw)
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                parsed = parse_line(text)
                if parsed is None:
                    if text.startswith("{"):
                        bad += 1
                    continue
                events.extend(parsed)
    except OSError:
        return [], offset, 0

    return events, pos, bad
