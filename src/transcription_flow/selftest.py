"""``--selftest <wav>``: load the model on CPU, transcribe a file, report JSON.

Used by CI and by the packaged exe to prove that the model, ctranslate2 and
the VAD all load. No UI, no microphone, no network.
A failed report carries "error" when the test could not run (bad file,
model load failure) or "reason" when it ran but found no speech or not the
--expect text.
"""

import struct
import time
from pathlib import Path

import numpy as np

from .engine import WhisperEngine
from .hallucination import LITTLE_SPEECH, ShortPhraseDropped
from .recorder import TARGET_RATE, resample

_PCM, _FLOAT, _EXTENSIBLE = 1, 3, 0xFFFE
# A header rate outside this range is broken or hostile: rate=1 would ask the
# resampler for 16 000 times the samples (review N1).
MIN_RATE, MAX_RATE = 1000, 768000
# (format, bytes per sample) -> (numpy dtype, offset, scale) to map samples to -1..1.
# 24-bit PCM is widened to <i4 with a zero low byte first.
_DECODE = {(_PCM, 1): ("u1", 128.0, 128.0), (_PCM, 2): ("<i2", 0.0, 2.0 ** 15),
           (_PCM, 3): ("<i4", 0.0, 2.0 ** 31), (_PCM, 4): ("<i4", 0.0, 2.0 ** 31),
           (_FLOAT, 4): ("<f4", 0.0, 1.0), (_FLOAT, 8): ("<f8", 0.0, 1.0)}


def _chunks(raw: bytes) -> dict:
    """The fmt and data chunks of a RIFF/WAVE file. A data chunk cut short is
    returned as far as it goes."""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    found, pos = {}, 12
    while pos + 8 <= len(raw) and "data" not in found:
        cid, size = raw[pos:pos + 4], int.from_bytes(raw[pos + 4:pos + 8], "little")
        if cid in (b"fmt ", b"data"):
            found[cid.decode().strip()] = raw[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    if len(found.get("fmt", b"")) < 16 or "data" not in found:
        raise ValueError("WAV file without a format or data chunk")
    return found


def read_wav(path: Path) -> np.ndarray:
    """Read a WAV (PCM 8/16/24/32-bit or float 32/64-bit) as 16 kHz mono float32.

    Our own small reader: the stdlib wave module has no float WAVs, and
    faster_whisper.audio.decode_audio (1.2.1) passes metadata_errors= to
    av.open(), which PyAV 19 no longer accepts."""
    chunks = _chunks(Path(path).read_bytes())
    tag, channels, rate, _, block, _ = struct.unpack("<HHIIHH", chunks["fmt"][:16])
    if tag == _EXTENSIBLE and len(chunks["fmt"]) >= 26:
        tag = struct.unpack("<H", chunks["fmt"][24:26])[0]  # first 2 bytes of the sub-format
    if not channels or not block or block % channels:
        raise ValueError("WAV file with a broken format chunk")
    if not MIN_RATE <= rate <= MAX_RATE:
        raise ValueError(f"unsupported WAV sample rate ({rate} Hz)")
    width = block // channels
    data = chunks["data"][:len(chunks["data"]) // block * block]
    if (tag, width) not in _DECODE:
        raise ValueError(f"unsupported WAV format (format {tag}, {width}-byte samples)")
    dtype, offset, scale = _DECODE[tag, width]
    if width == 3:
        wide = np.zeros((len(data) // 3, 4), dtype=np.uint8)
        wide[:, 1:] = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        data = wide.tobytes()
    audio = (np.frombuffer(data, dtype=dtype).astype(np.float32) - offset) / scale
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return resample(audio, rate, TARGET_RATE)


def run(wav: Path, model_dir: Path, expect: str = None) -> dict:
    audio = read_wav(wav)
    started = time.monotonic()
    engine = WhisperEngine(model_dir, device="cpu").load()
    loaded = time.monotonic()
    try:
        text = engine.transcribe(audio)
        reason = None if text else "no speech detected"
    except ShortPhraseDropped:
        text, reason = "", LITTLE_SPEECH
    finished = time.monotonic()
    if reason is None and expect is not None and expect.lower() not in text.lower():
        reason = "the expected text was not found"
    report = {
        "ok": reason is None,
        "text": text,
        "model": Path(model_dir).name,
        "audio_seconds": round(len(audio) / TARGET_RATE, 2),
        "load_seconds": round(loaded - started, 2),
        "transcribe_seconds": round(finished - loaded, 2),
    }
    if reason:
        report["reason"] = reason
    return report
