"""Wire the fleet hooks into the user's global Claude Code settings.

Adds hooks to ~/.claude/settings.json so EVERY session on this machine
auto-registers with the widget. Idempotent (re-running replaces our entries) and
non-destructive (backs up the file and preserves any other hooks you have).

    claude-fleet-install            # install / update
    claude-fleet-install --uninstall
    claude-fleet-install --dry-run  # print what would change, write nothing
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"

# (event, matcher, status). matcher "" = all; for Pre/PostToolUse it's a tool-name
# pattern. The AskUserQuestion/ExitPlanMode hooks make the LED go amber while Claude
# waits on a question/plan approval (mid-turn, so Notification doesn't fire), then
# back to red once answered. They only fire on those rare tools -> no per-tool cost.
HOOKS = [
    ("UserPromptSubmit", "", "working"),                    # turn starts   -> red
    ("Notification", "", "waiting"),                        # permission    -> amber
    ("PreToolUse", "AskUserQuestion|ExitPlanMode", "waiting"),   # asks you  -> amber
    ("PostToolUse", "AskUserQuestion|ExitPlanMode", "working"),  # answered  -> red
    ("Stop", "", "done"),                                   # turn done     -> green
    ("SessionStart", "", "idle"),                           # appears now   -> gray
    ("SessionEnd", "", "end"),                              # remove from list
]


def _q(path: str) -> str:
    """Forward-slash the path; quote only if it contains a space."""
    p = path.replace("\\", "/")
    return f'"{p}"' if " " in p else p


def _base_command() -> str:
    """How to invoke the hook. Prefer the installed shim; fall back to `-m`."""
    exe = shutil.which("claude-fleet-hook")
    if exe:
        return _q(exe)
    return f"{_q(sys.executable)} -m claude_fleet.hook"


def _is_fleet(entry: dict) -> bool:
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if "claude_fleet.hook" in cmd or "claude-fleet-hook" in cmd:
            return True
    return False


def _load_settings() -> dict:
    if not SETTINGS.exists():
        return {}
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"! {SETTINGS} is not valid JSON ({e}); fix it first.")


def _write_settings(settings: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _backup(settings: dict) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = SETTINGS.with_name(f"settings.fleet-backup-{stamp}.json")
    backup.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return backup


def _strip_fleet(hooks: dict) -> None:
    for event in list(hooks):
        arr = hooks.get(event) or []
        arr[:] = [e for e in arr if not _is_fleet(e)]
        if not arr:
            hooks.pop(event, None)


def main() -> None:
    uninstall = "--uninstall" in sys.argv
    dry_run = "--dry-run" in sys.argv

    settings = _load_settings()
    hooks = settings.setdefault("hooks", {})

    if uninstall:
        _strip_fleet(hooks)
        if not hooks:
            settings.pop("hooks", None)
        action = "Removed fleet hooks"
    else:
        base = _base_command()
        _strip_fleet(hooks)  # clear any prior fleet entries first
        for event, matcher, status in HOOKS:
            hooks.setdefault(event, []).append({
                "matcher": matcher,
                "hooks": [{"type": "command", "command": f"{base} {status}"}],
            })
        action = f"Installed fleet hooks (using: {base} <status>)"

    if dry_run:
        print("--- dry run: would write to", SETTINGS, "---")
        print(json.dumps(settings, indent=2))
        return

    if SETTINGS.exists():
        print("Backed up existing settings ->", _backup(settings).name)
    _write_settings(settings)
    print(action)
    print("Settings:", SETTINGS)
    if not uninstall:
        print("\nDone. Start the widget with:  claude-fleet")
        print("New Claude Code sessions will register automatically.")


if __name__ == "__main__":
    main()
