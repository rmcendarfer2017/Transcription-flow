"""The floating pill (black, with a white sound wave) at the bottom of the screen.

Only the Tk main thread touches Tk: other threads call ``post(state)``, which
puts the state on a queue that one ``after`` loop drains (review L2). That
same loop animates the bars, so there is never more than one animation chain
(review L1). Windows-only for now (``-transparentcolor``).
"""

import math
import queue
import tkinter as tk

WIDTH, HEIGHT = 75, 27
RADIUS = HEIGHT // 2
PILL = "#000000"
TRANSPARENT = "magenta"
NUM_BARS = 6
MID_Y = HEIGHT // 2
BAR_MIN_HALF = 2
BAR_MAX_HALF = HEIGHT / 2 - 3
INNER_MARGIN = WIDTH * 0.22
BAR_GAP = (WIDTH - 2 * INNER_MARGIN) / (NUM_BARS - 1)
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
        x = (root.winfo_screenwidth() - WIDTH) // 2
        y = root.winfo_screenheight() - HEIGHT - 90
        root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

        canvas = self._canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=TRANSPARENT,
                                          highlightthickness=0)
        canvas.pack()
        canvas.create_oval(0, 0, HEIGHT, HEIGHT, fill=PILL, outline=PILL)
        canvas.create_oval(WIDTH - HEIGHT, 0, WIDTH, HEIGHT, fill=PILL, outline=PILL)
        canvas.create_rectangle(RADIUS, 0, WIDTH - RADIUS, HEIGHT, fill=PILL, outline=PILL)
        self._bars = [
            canvas.create_line(self._bar_x(i), MID_Y - BAR_MIN_HALF, self._bar_x(i),
                               MID_Y + BAR_MIN_HALF, fill="white", width=2, capstyle=tk.ROUND)
            for i in range(NUM_BARS)
        ]
        root.withdraw()
        root.after(HIDDEN_TICK_MS, self._tick)

    @staticmethod
    def _bar_x(i: int) -> float:
        return INNER_MARGIN + i * BAR_GAP

    def post(self, state: str) -> None:
        """Thread-safe: 'recording', 'transcribing' or 'hidden'."""
        self._events.put(state)

    def call_soon(self, fn) -> None:
        """Thread-safe: run fn on the Tk thread (e.g. root.quit)."""
        self._events.put(fn)

    def _tick(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if callable(event):
                event()
            else:
                self._show(event)
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
            h = BAR_MIN_HALF + level * (BAR_MAX_HALF - BAR_MIN_HALF)
            self._canvas.coords(bar, self._bar_x(i), MID_Y - h, self._bar_x(i), MID_Y + h)

    def run(self) -> None:
        self.root.mainloop()
