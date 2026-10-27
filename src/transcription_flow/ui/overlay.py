"""The floating pill (black, with a white sound wave) at the bottom of the screen.

Only the Tk main thread touches Tk: other threads call ``post(state)`` or
``call(fn)``, which put work on a queue that one ``after`` loop drains
(review L2). That same loop animates the bars, so there is never more than
one animation chain (review L1). The Tk root is this pill; the tray's
settings window is a Toplevel of it. Sizes are scaled by the screen DPI and
the pill sits above the taskbar in the work area (review L3). Windows-only
for now (``-transparentcolor``).
"""

import logging
import math
import queue
import tkinter as tk

from .display import pill_position, work_area

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 75, 27  # at 100% (96 DPI)
PILL = "#000000"
TRANSPARENT = "magenta"
NUM_BARS = 6
VISIBLE_TICK_MS = 40
HIDDEN_TICK_MS = 100


class Overlay:
    def __init__(self, levels):
        """levels() returns NUM_BARS floats in 0..1 (the recorder's meter)."""
        self._levels = levels
        self._events = queue.Queue()
        self._state = "hidden"
        self._t = 0.0

        root = self.root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TRANSPARENT)
        root.attributes("-transparentcolor", TRANSPARENT)
        self.scale = scale = max(root.winfo_fpixels("1i") / 96.0, 1.0)
        w, h = self._w, self._h = round(WIDTH * scale), round(HEIGHT * scale)
        x, y = pill_position((w, h), (root.winfo_screenwidth(), root.winfo_screenheight()),
                             work_area(), scale)
        root.geometry(f"{w}x{h}+{x}+{y}")

        canvas = self._canvas = tk.Canvas(root, width=w, height=h, bg=TRANSPARENT,
                                          highlightthickness=0)
        canvas.pack()
        canvas.create_oval(0, 0, h, h, fill=PILL, outline=PILL)
        canvas.create_oval(w - h, 0, w, h, fill=PILL, outline=PILL)
        canvas.create_rectangle(h // 2, 0, w - h // 2, h, fill=PILL, outline=PILL)
        self._mid = h / 2
        self._bar_min, self._bar_max = 2 * scale, h / 2 - 3 * scale
        self._margin = w * 0.22
        self._gap = (w - 2 * self._margin) / (NUM_BARS - 1)
        self._bars = [
            canvas.create_line(self._bar_x(i), self._mid - self._bar_min, self._bar_x(i),
                               self._mid + self._bar_min, fill="white",
                               width=max(2, round(2 * scale)), capstyle=tk.ROUND)
            for i in range(NUM_BARS)
        ]
        root.withdraw()
        root.after(HIDDEN_TICK_MS, self._tick)

    def _bar_x(self, i: int) -> float:
        return self._margin + i * self._gap

    def post(self, state: str) -> None:
        """Thread-safe: 'recording', 'transcribing' or 'hidden'."""
        self._events.put(state)

    def call(self, fn) -> None:
        """Thread-safe: run ``fn()`` on the Tk thread."""
        self._events.put(fn)

    def _tick(self) -> None:
        while True:
            try:
                item = self._events.get_nowait()
            except queue.Empty:
                break
            if callable(item):
                try:
                    item()
                except Exception:
                    log.exception("UI action failed")
            else:
                self._show(item)
        self._animate()
        self.root.after(HIDDEN_TICK_MS if self._state == "hidden" else VISIBLE_TICK_MS,
                        self._tick)

    def _show(self, state: str) -> None:
        if state == self._state:
            return
        if state == "hidden":
            self.root.withdraw()
        elif self._state == "hidden":
            self.root.deiconify()
        self._state = state

    def _animate(self) -> None:
        self._t += 0.12
        if self._state == "recording":
            heights = self._levels()
        elif self._state == "transcribing":
            heights = [math.sin(self._t + i * 0.7) * 0.5 + 0.5 for i in range(NUM_BARS)]
        else:
            return
        for i, (bar, level) in enumerate(zip(self._bars, heights, strict=False)):
            h = self._bar_min + level * (self._bar_max - self._bar_min)
            self._canvas.coords(bar, self._bar_x(i), self._mid - h, self._bar_x(i), self._mid + h)

    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        """Tk thread only (use ``call(overlay.quit)`` elsewhere)."""
        self.root.quit()
