"""The always-on-top Tkinter widget: one row per session, name + colored LED.

Polls ~/.claude/fleet/ every POLL_MS and updates the LEDs in place (no flicker).
Frameless, draggable by the header, remembers its corner. Right-click for a menu.
"""

from __future__ import annotations

import sys
import time
import tkinter as tk

from . import common

POLL_MS = 500
STALE_SECS = 15 * 60      # dim to gray after this with no update
HIDE_SECS = 4 * 3600      # drop from the list entirely (e.g. terminal killed)

BG = "#1b1b1d"
HEADER_BG = "#111113"
FG = "#e6e6e6"
DIM = "#8a8a8a"
ACCENT = "#4c8bf5"
ROW_H = 26
HEADER_H = 24
WIDTH = 300
NAME_CHARS = 34
LED_R = 6

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

        self._restore_position()
        self.refresh()

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
        canvas = tk.Canvas(fr, width=18, height=ROW_H, bg=BG, highlightthickness=0)
        canvas.pack(side="right", padx=(4, 10))
        cx, cy = 9, ROW_H // 2
        oval = canvas.create_oval(cx - LED_R, cy - LED_R, cx + LED_R, cy + LED_R,
                                  fill=common.COLORS[common.IDLE], outline="")
        name_lbl = tk.Label(fr, text="", fg=FG, bg=BG, font=("Segoe UI", 9), anchor="w")
        name_lbl.pack(side="left", fill="x", expand=True, padx=(12, 0))
        return {"frame": fr, "canvas": canvas, "oval": oval, "name": name_lbl}

    # --- refresh loop ---------------------------------------------------------
    def refresh(self) -> None:
        now = time.time()
        sessions = common.read_all_sessions()
        visible = [s for s in sessions if now - s.get("updated_at", 0) < HIDE_SECS]
        visible.sort(key=lambda s: (PRIORITY.get(s.get("status"), 9),
                                    str(s.get("name", "")).lower()))

        seen = set()
        for s in visible:
            sid = s.get("session_id")
            seen.add(sid)
            status = s.get("status", common.IDLE)
            stale = (now - s.get("updated_at", 0)) > STALE_SECS
            color = common.STALE_COLOR if stale else common.COLORS.get(
                status, common.COLORS[common.IDLE])
            if sid not in self.rows:
                self.rows[sid] = self._make_row()
            row = self.rows[sid]
            row["name"].config(text=self._truncate(str(s.get("name", "?")), NAME_CHARS))
            row["canvas"].itemconfig(row["oval"], fill=color)

        for sid in list(self.rows):
            if sid not in seen:
                self.rows.pop(sid)["frame"].destroy()

        self._update_empty(len(visible) == 0)
        self._resize(len(visible))
        self.root.after(POLL_MS, self.refresh)

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
        self.root.mainloop()


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
