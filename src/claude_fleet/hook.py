"""Hook entry point. Claude Code invokes this on lifecycle events.

Usage (wired by the installer into ~/.claude/settings.json):
    claude-fleet-hook <status>       # status in {working, waiting, done, idle, end}

Reads the event JSON from stdin (gives us session_id + cwd), then writes or
removes that session's state file. It must be fast and must NEVER fail the
session, so every path is guarded and it always exits 0.
"""

from __future__ import annotations

import json
import os
import sys

from . import common

# CLI arg -> internal status
STATUS_MAP = {
    "working": common.WORKING,
    "waiting": common.WAITING,
    "done": common.DONE,
    "idle": common.IDLE,
    "end": "end",  # special: delete the session file
}


def _read_payload() -> dict:
    """Claude Code passes event JSON on stdin. Tolerate empty / malformed input."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _snippet(text: str, n: int = 50) -> str:
    s = " ".join(str(text).split()).strip()
    return (s[:n].rstrip() + "…") if len(s) > n else s


def _title_from_transcript(path: str | None) -> str | None:
    """Claude Code's own session name.

    Prefer the auto-generated title (``{"type":"ai-title","aiTitle": ...}``); if
    it hasn't been generated yet, fall back to the first user prompt. One cheap
    pass, JSON-parsing only the few candidate lines.
    """
    if not path or not os.path.exists(path):
        return None
    title = None
    prompt = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if '"ai-title"' in line:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("type") == "ai-title" and obj.get("aiTitle"):
                        title = obj["aiTitle"]  # last one wins (title can update)
                elif '"lastPrompt"' in line and prompt is None:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("lastPrompt"):
                        prompt = obj["lastPrompt"]
    except OSError:
        return None
    if title:
        return title.strip()
    if prompt:
        return _snippet(prompt)
    return None


def _session_name(payload: dict, cwd: str) -> str:
    override = os.environ.get("CLAUDE_FLEET_NAME")
    if override:
        return override
    title = _title_from_transcript(payload.get("transcript_path"))
    if title:
        return title
    base = os.path.basename(cwd.rstrip("/\\"))
    return base or cwd or "session"


def _run() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "idle"
    status = STATUS_MAP.get(arg, common.IDLE)

    payload = _read_payload()
    session_id = (
        payload.get("session_id")
        or os.environ.get("CLAUDE_SESSION_ID")
        or "unknown"
    )
    cwd = payload.get("cwd") or os.getcwd()
    name = _session_name(payload, cwd)

    if status == "end":
        common.remove_session(session_id)
    else:
        common.write_session(session_id, name, status, cwd)


def main() -> None:
    try:
        _run()
    except Exception:
        pass  # never break the session because of the widget
    sys.exit(0)


if __name__ == "__main__":
    main()
