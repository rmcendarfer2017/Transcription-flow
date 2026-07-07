import collections
import math
import threading
import time
import tkinter as tk

import keyboard
import numpy as np
import pyperclip
import sounddevice as sd
from faster_whisper import WhisperModel

HOTKEY = "right ctrl"
SAMPLE_RATE = 16000
MODEL_SIZE = "small.en"

print(f"Loading Whisper model '{MODEL_SIZE}' (GPU if available)...")
try:
    model = WhisperModel(MODEL_SIZE, device="cuda", compute_type="float16")
    print("Model loaded on GPU.")
except Exception as e:
    print(f"GPU load failed ({e}), falling back to CPU.")
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

recording = False
frames = []

NUM_BARS = 6
levels = collections.deque([0.0] * NUM_BARS, maxlen=NUM_BARS)


def audio_callback(indata, frame_count, time_info, status):
    if recording:
        frames.append(indata.copy())
        rms = float(np.sqrt(np.mean(np.square(indata))))
        levels.append(min(rms * 12.0, 1.0))


stream = sd.InputStream(
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="float32",
    callback=audio_callback,
)
stream.start()

# ---- floating overlay indicator (black pill, white sound wave) ----
root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
TRANSPARENT = "magenta"
root.configure(bg=TRANSPARENT)
root.attributes("-transparentcolor", TRANSPARENT)

WIDTH, HEIGHT = 75, 27
RADIUS = HEIGHT // 2
screen_w = root.winfo_screenwidth()
screen_h = root.winfo_screenheight()
x = (screen_w - WIDTH) // 2
y = screen_h - HEIGHT - 90
root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=TRANSPARENT, highlightthickness=0)
canvas.pack()

PILL = "#000000"
canvas.create_oval(0, 0, HEIGHT, HEIGHT, fill=PILL, outline=PILL)
canvas.create_oval(WIDTH - HEIGHT, 0, WIDTH, HEIGHT, fill=PILL, outline=PILL)
canvas.create_rectangle(RADIUS, 0, WIDTH - RADIUS, HEIGHT, fill=PILL, outline=PILL)

MID_Y = HEIGHT // 2
BAR_MIN_HALF = 2
BAR_MAX_HALF = HEIGHT / 2 - 3
INNER_MARGIN = WIDTH * 0.22
USABLE_WIDTH = WIDTH - 2 * INNER_MARGIN
BAR_GAP = USABLE_WIDTH / (NUM_BARS - 1)
bar_x_start = INNER_MARGIN

bar_ids = []
for i in range(NUM_BARS):
    bx = bar_x_start + i * BAR_GAP
    bar_ids.append(
        canvas.create_line(
            bx, MID_Y - BAR_MIN_HALF, bx, MID_Y + BAR_MIN_HALF,
            fill="white", width=2, capstyle=tk.ROUND,
        )
    )

root.withdraw()

state = "hidden"
anim_t = 0.0


def animate():
    global anim_t
    anim_t += 0.12

    if state == "recording":
        for i, bar_id in enumerate(bar_ids):
            level = levels[i]
            h = BAR_MIN_HALF + level * (BAR_MAX_HALF - BAR_MIN_HALF)
            bx = bar_x_start + i * BAR_GAP
            canvas.coords(bar_id, bx, MID_Y - h, bx, MID_Y + h)
    elif state == "transcribing":
        for i, bar_id in enumerate(bar_ids):
            h = BAR_MIN_HALF + (math.sin(anim_t + i * 0.7) * 0.5 + 0.5) * (BAR_MAX_HALF - BAR_MIN_HALF)
            bx = bar_x_start + i * BAR_GAP
            canvas.coords(bar_id, bx, MID_Y - h, bx, MID_Y + h)

    if state != "hidden":
        root.after(40, animate)


def set_overlay(new_state):
    def _update():
        global state
        state = new_state
        if new_state == "hidden":
            root.withdraw()
        else:
            root.deiconify()
            animate()

    root.after(0, _update)


def start_recording():
    global recording, frames
    if recording:
        return
    frames = []
    levels.extend([0.0] * NUM_BARS)
    recording = True
    print("\n[recording...]")
    set_overlay("recording")


def stop_recording_and_transcribe():
    global recording
    if not recording:
        return
    recording = False
    print("[transcribing...]")
    set_overlay("transcribing")
    threading.Thread(target=_transcribe, daemon=True).start()


def _transcribe():
    try:
        if not frames:
            print("[no audio captured]")
            return

        audio = np.concatenate(frames, axis=0).flatten()
        duration = len(audio) / SAMPLE_RATE
        if duration < 0.3:
            print("[too short, ignored]")
            return

        segments, _ = model.transcribe(audio, language="en", vad_filter=True)
        text = "".join(segment.text for segment in segments).strip()

        if not text:
            print("[no speech detected]")
            return

        print(f"[transcribed]: {text}")
        pyperclip.copy(text)
        time.sleep(0.05)
        keyboard.write(text)
    finally:
        set_overlay("hidden")


keyboard.on_press_key(HOTKEY, lambda _: start_recording())
keyboard.on_release_key(HOTKEY, lambda _: stop_recording_and_transcribe())

print(f"Ready. Hold '{HOTKEY}' to dictate, release to transcribe + paste. Ctrl+C to quit.")

root.mainloop()

stream.stop()
stream.close()
