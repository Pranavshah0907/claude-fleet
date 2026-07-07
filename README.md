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

## Link laptops together (cross-machine)

Show sessions from *other* laptops in the same widget — AnyDesk-style, with an **ID + PASS**:

- **ID** = a pairing code embedding the relay location (safe-ish to share).
- **PASS** = a passphrase that never leaves your machines and **end-to-end-encrypts**
  every record. The relay/provider only ever sees a random room id, opaque keys, and
  ciphertext — never your project names or hostnames.

The relay is an [Upstash Redis](https://upstash.com) database (free tier), reached over
plain HTTPS (firewall-friendly). Sessions auto-expire via TTL, so dead ones vanish on their own.

**One-time setup (on your first machine):**

1. Create a free Upstash Redis DB → copy its **REST URL** and **REST TOKEN**
   (Upstash console → your DB → "REST API").
2. Create the room and link this machine:
   ```bash
   claude-fleet init-room            # prompts for URL + token; prints a short ID + PASS
   ```

**On every other laptop** (after `uv tool install` + `claude-fleet-install`), save the
relay creds once, then join with the short room ID:
```bash
claude-fleet set-relay --url <URL> --token <TOKEN>
claude-fleet join --room <ID> --pass <PASS>       # ID is the short 8-char room code
```
The relay creds (long token) are entered once per machine; the **ID you share is just the
short room code**. Prefer a single self-contained string? `claude-fleet link --code <CODE> --pass <PASS>`
still works (the code embeds the relay creds, so it's long).

**Then, on each machine that should participate**, run one of:
- `claude-fleet` — the widget (also syncs in a background thread), or
- `claude-fleet agent` — headless sync only (for a box you don't watch; good for autostart).

Every machine running the widget shows the **whole fleet**; remote rows are tagged with a
dim machine name. `claude-fleet status` shows your link + connectivity; `claude-fleet unlink`
reverts to local-only. Local sessions stay instant (file-based); remote ones lag ~1–3 s.

> Live status needs both ends online: a remote session shows only while that laptop's
> session is active *and* its widget/agent is running and reachable.

## Notes / current limitations

- The installer edits your **global** `~/.claude/settings.json` (backed up to
  `settings.fleet-backup-<timestamp>.json`). Run `claude-fleet-install --dry-run` to preview.
- After you approve a permission prompt, the LED stays 🟡 until the turn ends (🟢). Enabling
  a `PreToolUse`/`PostToolUse` "back to red" hook is a documented opt-in (adds a hook fire per
  tool call) — not on by default to keep sessions snappy.
- Session name = Claude Code's auto-generated session title (falls back to the first prompt,
  then the folder name). Override per session with the `CLAUDE_FLEET_NAME` env var; override
  the machine label with `CLAUDE_FLEET_HOST`.
- Hooks never touch the network — all sync happens in the widget/agent, so hook latency stays zero.
- Tkinter (Windows-first). A polished PySide6 version is planned.
