"""The always-on-top Tkinter widget: one row per session, name + colored LED.

Polls ~/.claude/fleet/ every POLL_MS and updates the LEDs in place (no flicker).
Frameless, draggable by the header, remembers its corner. Right-click for a menu.
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk

from . import common, remote

POLL_MS = 500
STALE_SECS = 30 * 60      # gray a SETTLED session after this long idle (never while working)
HIDE_SECS = 4 * 3600      # drop a settled session entirely (working sessions never auto-hide)

BG = "#1b1b1d"
HEADER_BG = "#111113"
FG = "#e6e6e6"
DIM = "#8a8a8a"
ACCENT = "#4c8bf5"
HOST_FG = "#6b7bb0"        # dim blue tag for sessions on other machines
AGE_FG = "#707070"        # dim gray "time since last activity"
DISMISS_FG = "#4a4a4a"     # faint ✕ (dismiss row)
DISMISS_HOVER = "#ff6b6b"  # brightens red on hover
ROW_H = 26
HEADER_H = 24
WIDTH = 320
NAME_CHARS = 30
LED_R = 6


def _ago(secs: float) -> str:
    """Compact 'time since last activity': now / 5m / 2h / 3d."""
    s = int(secs)
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"

# needs-you first, then running, then done, then idle
PRIORITY = {common.WAITING: 0, common.WORKING: 1, common.DONE: 2, common.IDLE: 3}


class FleetWidget:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Claude Fleet")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        try:
            self.root.attributes("-alpha", 0.97)
        except tk.TclError:
            pass

        self._build_header()
        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.rows: dict[str, dict] = {}       # session_id -> widgets
        self.empty_label: tk.Label | None = None
        self._drag = {"x": 0, "y": 0}
        self._name_cache: dict = {}           # transcript_path -> (ts, name)
        self._dismissed: dict = {}            # key -> updated_at when dismissed

        self._restore_position()
        self._start_sync()
        self.refresh()

    def _start_sync(self) -> None:
        """If a room is configured, run the cross-machine sync in a daemon thread."""
        self._sync_stop = None
        try:
            if remote.is_configured():
                self._sync_stop = threading.Event()
                threading.Thread(target=remote.run_sync, args=(self._sync_stop,),
                                 daemon=True).start()
        except Exception:
            pass

    # --- layout ---------------------------------------------------------------
    def _build_header(self) -> None:
        hdr = tk.Frame(self.root, bg=HEADER_BG, height=HEADER_H)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        dot = tk.Label(hdr, text="●", fg=ACCENT, bg=HEADER_BG, font=("Segoe UI", 9))
        dot.pack(side="left", padx=(8, 4))
        title = tk.Label(hdr, text="Claude Fleet", fg=FG, bg=HEADER_BG,
                         font=("Segoe UI", 9, "bold"))
        title.pack(side="left")
        close = tk.Label(hdr, text="✕", fg=DIM, bg=HEADER_BG,
                         font=("Segoe UI", 9), cursor="hand2")
        close.pack(side="right", padx=8)
        close.bind("<Button-1>", lambda e: self.root.destroy())
        for w in (hdr, dot, title):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)
        self.root.bind("<Button-3>", self._context_menu)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def _make_row(self) -> dict:
        fr = tk.Frame(self.body, bg=BG, height=ROW_H)
        fr.pack(fill="x")
        fr.pack_propagate(False)
        dismiss = tk.Label(fr, text="✕", fg=DISMISS_FG, bg=BG,
                           font=("Segoe UI", 8), cursor="hand2")
        dismiss.pack(side="right", padx=(0, 6))
        dismiss.bind("<Enter>", lambda e: dismiss.config(fg=DISMISS_HOVER))
        dismiss.bind("<Leave>", lambda e: dismiss.config(fg=DISMISS_FG))
        canvas = tk.Canvas(fr, width=16, height=ROW_H, bg=BG, highlightthickness=0)
        canvas.pack(side="right", padx=(2, 2))
        cx, cy = 8, ROW_H // 2
        oval = canvas.create_oval(cx - LED_R, cy - LED_R, cx + LED_R, cy + LED_R,
                                  fill=common.COLORS[common.IDLE], outline="")
        host_lbl = tk.Label(fr, text="", fg=HOST_FG, bg=BG, font=("Segoe UI", 7))
        host_lbl.pack(side="right", padx=(0, 2))
        age_lbl = tk.Label(fr, text="", fg=AGE_FG, bg=BG, font=("Segoe UI", 7))
        age_lbl.pack(side="right", padx=(0, 4))
        name_lbl = tk.Label(fr, text="", fg=FG, bg=BG, font=("Segoe UI", 9), anchor="w")
        name_lbl.pack(side="left", fill="x", expand=True, padx=(12, 0))
        return {"frame": fr, "canvas": canvas, "oval": oval, "name": name_lbl,
                "host": host_lbl, "age": age_lbl, "dismiss": dismiss}

    # --- refresh loop ---------------------------------------------------------
    def refresh(self) -> None:
        now = time.time()
        sessions = common.read_all_sessions() + common.read_remote_sessions()
        # keep actively-working sessions no matter how long the turn runs (e.g.
        # overnight); only settled sessions are auto-hidden after HIDE_SECS
        visible = [s for s in sessions
                   if self._is_active(s)
                   or now - s.get("updated_at", 0) < HIDE_SECS]
        visible.sort(key=lambda s: (PRIORITY.get(s.get("status"), 9),
                                    str(s.get("host") or ""),
                                    str(s.get("name", "")).lower()))

        seen = set()
        for s in visible:
            host = s.get("host") or ""       # remote sessions carry their machine
            key = f"{host}/{s.get('session_id')}"
            updated = s.get("updated_at", now)
            dm = self._dismissed.get(key)
            if dm is not None:
                if updated > dm:
                    del self._dismissed[key]  # new activity -> bring it back
                else:
                    continue                  # dismissed and quiet -> stay hidden
            seen.add(key)
            status = s.get("status", common.IDLE)
            # "active" = hook says working OR the transcript is still being written.
            # An active session never grays and shows no idle timer; the "time since"
            # clock only runs once the session has actually settled.
            active = self._is_active(s)
            stale = (not active) and (now - updated) > STALE_SECS
            if active:
                color = common.COLORS[common.WORKING]
            elif stale:
                color = common.STALE_COLOR
            else:
                color = common.COLORS.get(status, common.COLORS[common.IDLE])
            if key not in self.rows:
                self.rows[key] = self._make_row()
                self.rows[key]["dismiss"].bind("<Button-1>", lambda e, k=key: self._dismiss(k))
            row = self.rows[key]
            row["updated_at"] = updated
            name = str(s.get("name", "?"))
            if not host and s.get("transcript_path"):  # local: re-resolve title live
                name = self._local_name(s["transcript_path"], s.get("cwd"), name, now)
            row["name"].config(text=self._truncate(name, NAME_CHARS))
            row["host"].config(text=host)
            row["age"].config(text="" if active else _ago(now - updated))
            row["canvas"].itemconfig(row["oval"], fill=color)

        for key in list(self.rows):
            if key not in seen:
                self.rows.pop(key)["frame"].destroy()

        self._update_empty(len(self.rows) == 0)
        self._resize(len(self.rows))
        self.root.after(POLL_MS, self.refresh)

    def _is_active(self, s: dict) -> bool:
        """Working per the hook, or (for local sessions) transcript still growing.
        Remote sessions are trusted as pushed — the origin agent marks them active."""
        if s.get("status") == common.WORKING:
            return True
        if not (s.get("host") or ""):
            return common.recently_active(s.get("transcript_path"))
        return False

    def _dismiss(self, key: str) -> None:
        """Hide a row until the session shows new activity (updated_at advances)."""
        row = self.rows.get(key)
        self._dismissed[key] = row.get("updated_at", 0) if row else 0
        if row:
            row["frame"].destroy()
            self.rows.pop(key, None)

    def _update_empty(self, is_empty: bool) -> None:
        if is_empty and self.empty_label is None:
            self.empty_label = tk.Label(self.body, text="no active sessions",
                                        fg=DIM, bg=BG, font=("Segoe UI", 9, "italic"))
            self.empty_label.pack(pady=10)
        elif not is_empty and self.empty_label is not None:
            self.empty_label.destroy()
            self.empty_label = None

    def _resize(self, n: int) -> None:
        rows_h = n * ROW_H if n else 40
        self.root.geometry(f"{WIDTH}x{HEADER_H + rows_h + 6}")

    def _local_name(self, tp: str, cwd, fallback: str, now: float) -> str:
        cached = self._name_cache.get(tp)
        if cached and now - cached[0] < 3.0:   # throttle transcript re-parsing
            return cached[1]
        try:
            name = common.resolve_name(tp, cwd) or fallback
        except Exception:
            name = fallback
        self._name_cache[tp] = (now, name)
        return name

    @staticmethod
    def _truncate(s: str, n: int) -> str:
        return s if len(s) <= n else s[: n - 1] + "…"

    # --- window position / drag ----------------------------------------------
    def _default_position(self) -> tuple[int, int]:
        self.root.update_idletasks()
        return self.root.winfo_screenwidth() - WIDTH - 24, 48

    def _restore_position(self) -> None:
        cfg = common.load_widget_config()
        if "x" in cfg and "y" in cfg:
            x, y = int(cfg["x"]), int(cfg["y"])
        else:
            x, y = self._default_position()
        self.root.geometry(f"{WIDTH}x60+{x}+{y}")

    def _reset_position(self) -> None:
        x, y = self._default_position()
        self.root.geometry(f"+{x}+{y}")
        common.save_widget_config({"x": x, "y": y})

    def _start_drag(self, e) -> None:
        self._drag["x"] = e.x_root - self.root.winfo_x()
        self._drag["y"] = e.y_root - self.root.winfo_y()

    def _on_drag(self, e) -> None:
        x = e.x_root - self._drag["x"]
        y = e.y_root - self._drag["y"]
        self.root.geometry(f"+{x}+{y}")
        common.save_widget_config({"x": x, "y": y})

    def _context_menu(self, e) -> None:
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Reset position", command=self._reset_position)
        m.add_separator()
        m.add_command(label="Quit", command=self.root.destroy)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass  # Ctrl+C in a foreground terminal -> quiet exit, no traceback


def main() -> None:
    if "--selftest" in sys.argv:
        w = FleetWidget()
        w.root.after(300, w.root.destroy)
        w.run()
        print("selftest ok")
        return
    FleetWidget().run()


if __name__ == "__main__":
    main()
