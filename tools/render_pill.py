"""Render the README's pill images from the app's real overlay code.

Runs ``transcription_flow.ui.overlay.Overlay`` (the shipped Tk pill) under a
virtual X display, drives it through recording (level meter fed by a
synthetic speech signal through the recorder's own ``_level``) and
transcribing, grabs each frame and writes ``docs/images/pill.gif`` and
``docs/images/pill.png``.

Linux only (Tk on X11). Differences from Windows, handled here and said in
the README:

- ``-transparentcolor`` is a Windows-only Tk attribute (X11 raises
  TclError), so it is skipped and the colour key is applied to the grabbed
  frames instead: pixels exactly equal to the key colour become the
  background, as Windows does for a layered window.
- The display is 192 DPI, so the pill is drawn at the app's 200% scale.

Needs python3-tk, Pillow (with X grab support), numpy and Xvfb, e.g. from
the repo root::

    docker run --rm -v "$PWD":/src -w /src debian:trixie-slim sh -c '
      apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        python3-tk python3-pil python3-numpy xvfb xauth >/dev/null &&
      xvfb-run -s "-screen 0 1280x720x24 -dpi 192" \
        python3 tools/render_pill.py'
"""

import collections
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from transcription_flow.recorder import NUM_LEVELS, TARGET_RATE, _level

OUT = ROOT / "docs" / "images"
BACKGROUND = (233, 234, 237)
KEY = (255, 0, 255)  # Tk's "magenta", the overlay's TRANSPARENT colour
PAD = (36, 24)  # background around the pill, in pixels (x, y)
FRAME_MS = 40  # the overlay's VISIBLE_TICK_MS
RECORD_FRAMES, TRANSCRIBE_FRAMES, HIDDEN_FRAMES = 75, 35, 15
STILL_FRAME = 40  # a recording frame mid-word, for pill.png


def speech_levels(frames: int, seed: int = 7) -> list:
    """Meter levels, one per FRAME_MS audio chunk, for a speech-like signal:
    noise shaped into syllables (about 4 a second) with short word gaps,
    measured by the recorder's own ``_level``."""
    rng = np.random.default_rng(seed)
    n = TARGET_RATE * FRAME_MS // 1000
    levels = []
    for f in range(frames):
        t = f * FRAME_MS / 1000
        syllable = max(math.sin(2 * math.pi * 4.0 * t), 0.0) ** 0.6
        word = 0.0 if 0.95 < t % 1.3 < 1.25 else 1.0
        loud = 0.045 + 0.03 * math.sin(t * 1.7)
        chunk = rng.standard_normal(n).astype(np.float32) * loud * syllable * word
        levels.append(_level(chunk))
    return levels


def key_out(image, box: tuple):
    """Put ``image`` (the grabbed window) on the background, treating pixels
    of exactly the KEY colour as transparent, padded by ``box`` (x, y)."""
    from PIL import Image

    rgb = image.convert("RGB")
    opaque = np.any(np.asarray(rgb) != KEY, axis=2)
    mask = Image.fromarray(opaque.astype(np.uint8) * 255, "L")
    out = Image.new("RGB", (rgb.width + 2 * box[0], rgb.height + 2 * box[1]), BACKGROUND)
    out.paste(rgb, box, mask)
    return out


def main() -> int:
    import tkinter as tk

    from PIL import Image, ImageGrab

    class _Tk(tk.Tk):
        def attributes(self, *args):
            if args and args[0] == "-transparentcolor":
                return ""  # Windows-only; the key is applied in key_out
            return super().attributes(*args)

    tk.Tk = _Tk
    from transcription_flow.ui.overlay import Overlay

    meter = collections.deque([0.0] * NUM_LEVELS, maxlen=NUM_LEVELS)
    feed = iter(speech_levels(RECORD_FRAMES + 10))

    def levels():
        meter.append(next(feed, 0.0))  # one chunk per tick, newest on the right
        return list(meter)

    overlay = Overlay(levels)
    root = overlay.root
    w, h = overlay._w, overlay._h
    print(f"Tk {tk.TkVersion}, scale {overlay.scale:.2f}, pill {w}x{h}")

    def pump(done) -> None:
        while not done():
            root.update()
            time.sleep(0.005)
        root.update()

    # Withdrawn: nothing on screen, only the background.
    blank = Image.new("RGB", (w + 2 * PAD[0], h + 2 * PAD[1]), BACKGROUND)
    plan = (["recording"] * RECORD_FRAMES + ["transcribing"] * TRANSCRIBE_FRAMES
            + ["hidden"] * HIDDEN_FRAMES)
    frames, state = [], "hidden"
    for want in plan:
        if want != state:
            overlay.post(want)
            if state == "hidden":
                # The hidden tick is slower; don't grab the window before it
                # is mapped and painted (X11 shows it black until then).
                pump(root.winfo_ismapped)
            state = want
        deadline = time.monotonic() + FRAME_MS / 1000
        pump(lambda: time.monotonic() >= deadline)  # noqa: B023 (called at once)
        if state == "hidden":
            frames.append(blank)
            continue
        x, y = root.winfo_rootx(), root.winfo_rooty()
        frames.append(key_out(ImageGrab.grab((x, y, x + w, y + h)), PAD))
    root.destroy()

    OUT.mkdir(parents=True, exist_ok=True)
    frames[STILL_FRAME].save(OUT / "pill.png", optimize=True)
    frames[0].save(OUT / "pill.gif", save_all=True, append_images=frames[1:],
                   duration=FRAME_MS, loop=0, optimize=True, disposal=1)
    for name in ("pill.png", "pill.gif"):
        print(f"{OUT / name}: {(OUT / name).stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
