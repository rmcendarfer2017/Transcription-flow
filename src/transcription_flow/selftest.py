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
from .recorder import TARGET_RATE, resample

_PCM, _FLOAT, _EXTENSIBLE = 1, 3, 0xFFFE
_DTYPES = {(_PCM, 1): "u1", (_PCM, 2): "<i2", (_PCM, 4): "<i4",
           (_FLOAT, 4): "<f4", (_FLOAT, 8): "<f8"}


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
    if not channels or not rate or block % channels:
        raise ValueError("WAV file with a broken format chunk")
    width = block // channels
    data = chunks["data"][:len(chunks["data"]) // block * block]
    if (tag, width) == (_PCM, 3):  # 24-bit: widen to int32 with a zero low byte
        wide = np.zeros((len(data) // 3, 4), dtype=np.uint8)
        wide[:, 1:] = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        audio = wide.view("<i4").ravel().astype(np.float32) / 2.0 ** 31
    elif (tag, width) in _DTYPES:
        samples = np.frombuffer(data, dtype=_DTYPES[tag, width])
        if tag == _FLOAT:
            audio = samples.astype(np.float32)
        elif width == 1:
            audio = (samples.astype(np.float32) - 128.0) / 128.0
        else:
            audio = samples.astype(np.float32) / float(2 ** (8 * width - 1))
    else:
        raise ValueError(f"unsupported WAV format (format {tag}, {width}-byte samples)")
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
    report = {
        "ok": bool(text) and (expect is None or expect.lower() in text.lower()),
        "text": text,
        "model": Path(model_dir).name,
        "audio_seconds": round(len(audio) / TARGET_RATE, 2),
        "load_seconds": round(loaded - started, 2),
        "transcribe_seconds": round(finished - loaded, 2),
    }
    if not text:
        report["reason"] = "no speech detected"
    elif not report["ok"]:
        report["reason"] = "the expected text was not found"
    return report
