# claude-fleet

A tiny always-on-top desktop widget that shows a live **LED per Claude Code session**:

| Session | LED |
|---|---|
| `embedded_ai_library` | 🔴 running |
| `OnBaSys` | 🟡 needs you |
| `MHE_Paper` | 🟢 done |

- 🔴 **red** — Claude is actively working
- 🟡 **amber** — blocked, waiting for your input (permission / attention)
- 🟢 **green** — turn finished, control handed back to you
- ⚪ **gray** — idle, or gone stale (no update in 15 min)

Every Claude Code session on the machine registers **automatically** — no per-project
setup — because the reporting is wired into your *global* Claude Code hooks.

## How it works

```
 Claude session ──(hooks fire on: UserPromptSubmit / Notification / Stop / Session*)──▶
      writes ~/.claude/fleet/<session_id>.json  ──(polled every 500 ms)──▶  the widget
```

No server, no ports, no daemon. Each session owns one small JSON state file; the widget
watches the folder. Concurrent sessions just work (one file each).

| Hook event | Status written | LED |
|---|---|---|
| `SessionStart` | idle | ⚪ |
| `UserPromptSubmit` | working | 🔴 |
| `Notification` | waiting | 🟡 |
| `Stop` | done | 🟢 |
| `SessionEnd` | (file removed) | — |

## Install

Requires [uv](https://docs.astral.sh/uv/). Then, on any laptop:

```bash
git clone <this-repo> claude-fleet && cd claude-fleet
uv tool install .          # puts claude-fleet / claude-fleet-hook / claude-fleet-install on PATH
claude-fleet-install       # merges hooks into ~/.claude/settings.json (backs it up first)
claude-fleet               # launch the widget
```

To try before installing: `uv run claude-fleet` (widget) — but the hooks need a real
install (`claude-fleet-install`) so sessions can report.

### Remove

```bash
claude-fleet-install --uninstall
uv tool uninstall claude-fleet
```

## Notes / current limitations (v1)

- The installer edits your **global** `~/.claude/settings.json` (backed up to
  `settings.fleet-backup-<timestamp>.json`). Run `claude-fleet-install --dry-run` to preview.
- After you approve a permission prompt, the LED stays 🟡 until the turn ends (🟢). Enabling
  a `PreToolUse`/`PostToolUse` "back to red" hook is a documented opt-in (adds a hook fire per
  tool call) — not on by default to keep sessions snappy.
- Session name = the working-directory folder name. Override per session with the
  `CLAUDE_FLEET_NAME` env var.
- Tkinter (Windows-first). A polished PySide6 version is planned.
