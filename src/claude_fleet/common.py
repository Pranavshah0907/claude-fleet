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

STATE_DIR = Path.home() / ".claude" / "fleet"
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


def write_session(session_id: str, name: str, status: str, cwd: str) -> None:
    """Atomically write one session's state (temp file + os.replace)."""
    ensure_dir()
    data = {
        "session_id": session_id,
        "name": name,
        "status": status,
        "cwd": cwd,
        "updated_at": time.time(),
    }
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
