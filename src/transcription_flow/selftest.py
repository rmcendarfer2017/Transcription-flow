"""``--selftest <wav>``: load the model on CPU, transcribe a file, report JSON.

Used by CI and by the packaged exe to prove that the model, ctranslate2 and
the VAD all load. No UI, no microphone, no network.
"""

import time
import wave
from pathlib import Path

import numpy as np

from .engine import WhisperEngine
from .recorder import TARGET_RATE, resample

_DTYPES = {1: np.uint8, 2: np.int16, 4: np.int32}


def read_wav(path: Path) -> np.ndarray:
    """Read a PCM WAV as 16 kHz mono float32."""
    with wave.open(str(path), "rb") as wav:
        width, channels, rate = wav.getsampwidth(), wav.getnchannels(), wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if width not in _DTYPES:
        raise ValueError(f"unsupported WAV sample width: {width} bytes")
    data = np.frombuffer(raw, dtype=np.dtype(_DTYPES[width]).newbyteorder("<"))
    if width == 1:
        audio = (data.astype(np.float32) - 128.0) / 128.0
    else:
        audio = data.astype(np.float32) / float(2 ** (8 * width - 1))
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return resample(audio, rate, TARGET_RATE)


def run(wav: Path, model_dir: Path, expect: str = None) -> dict:
    audio = read_wav(wav)
    started = time.monotonic()
    engine = WhisperEngine(model_dir, device="cpu").load()
    loaded = time.monotonic()
    text = engine.transcribe(audio)
    finished = time.monotonic()
    ok = bool(text) and (expect is None or expect.lower() in text.lower())
    return {
        "ok": ok,
        "text": text,
        "model": Path(model_dir).name,
        "audio_seconds": round(len(audio) / TARGET_RATE, 2),
        "load_seconds": round(loaded - started, 2),
        "transcribe_seconds": round(finished - loaded, 2),
    }
