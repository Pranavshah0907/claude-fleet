"""Shared state: where session files live, the status vocabulary, and safe I/O.

Each Claude Code session gets one small JSON file in ``~/.claude/fleet/`` named
``<session_id>.json``. Hooks write these files; the widget reads them. Files whose
name starts with ``_`` are widget config, not sessions.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("CLAUDE_FLEET_DIR") or (Path.home() / ".claude" / "fleet"))
REMOTE_DIR = STATE_DIR / "remote"   # sessions mirrored from other machines
_CONFIG = STATE_DIR / "_widget.json"

# --- status vocabulary --------------------------------------------------------
WORKING = "working"   # Claude is actively running a turn
WAITING = "waiting"   # Claude is blocked, needs the user (permission / attention)
DONE = "done"         # turn finished, control handed back
IDLE = "idle"         # session alive, no work yet (or just started)

# LED colors (dark widget)
COLORS = {
    WORKING: "#f0503c",  # red
    WAITING: "#f5b301",  # amber
    DONE: "#3fb950",     # green
    IDLE: "#6e6e6e",     # gray
}
STALE_COLOR = "#4a4a4a"  # dimmed: no update in a while, no clean SessionEnd


def ensure_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


def _session_file(session_id: str) -> Path:
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_") or "unknown"
    return STATE_DIR / f"{safe}.json"


def write_session(session_id: str, name: str, status: str, cwd: str,
                  transcript_path: str | None = None) -> None:
    """Atomically write one session's state (temp file + os.replace)."""
    ensure_dir()
    data = {
        "session_id": session_id,
        "name": name,
        "status": status,
        "cwd": cwd,
        "updated_at": time.time(),
    }
    if transcript_path:
        data["transcript_path"] = transcript_path
    target = _session_file(session_id)
    fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, target)  # atomic on Windows and POSIX
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def remove_session(session_id: str) -> None:
    try:
        _session_file(session_id).unlink()
    except OSError:
        pass


def read_all_sessions() -> list[dict]:
    """Return every valid session dict; skip config files and half-written JSON."""
    if not STATE_DIR.exists():
        return []
    out = []
    for p in STATE_DIR.glob("*.json"):
        if p.name.startswith("_"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue  # keep last-good; a writer may be mid-write
        if isinstance(d, dict) and "status" in d:
            d.setdefault("session_id", p.stem)
            out.append(d)
    return out


def _snippet(text: str, n: int = 50) -> str:
    s = " ".join(str(text).split()).strip()
    return (s[:n].rstrip() + "…") if len(s) > n else s


def _title_from_transcript(path: str | None) -> str | None:
    """Claude Code's own session name: prefer the auto-generated ``ai-title``;
    else the first user prompt. One cheap pass, parsing only candidate lines."""
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


def resolve_name(transcript_path: str | None, cwd: str | None) -> str:
    """Best available session name, resolved fresh (called live, not frozen at
    hook time): CLAUDE_FLEET_NAME override -> ai-title -> first prompt -> folder."""
    override = os.environ.get("CLAUDE_FLEET_NAME")
    if override:
        return override
    title = _title_from_transcript(transcript_path)
    if title:
        return title
    base = os.path.basename((cwd or "").rstrip("/\\"))
    return base or (cwd or "session")


def read_remote_sessions() -> list[dict]:
    """Sessions from other machines, mirrored into REMOTE_DIR by the sync agent.

    Plain JSON read (already decrypted by the agent) — no crypto needed here, so
    the widget can display remote rows without importing ``cryptography``.
    """
    if not REMOTE_DIR.exists():
        return []
    out = []
    for p in REMOTE_DIR.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(d, dict) and "status" in d:
            out.append(d)
    return out


# --- widget position config ---------------------------------------------------
def load_widget_config() -> dict:
    try:
        with open(_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_widget_config(cfg: dict) -> None:
    ensure_dir()
    try:
        with open(_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass
