"""GPU probe in a child process (review H1).

``WhisperModel(device="cuda")`` can load while cuBLAS or cuDNN is missing, and
then the first transcription fails, sometimes with a native abort that kills
the process. So we try a one-second silent transcription on CUDA in a child
process with a timeout, and use the GPU only if that child reports success.
Every other outcome (crash, timeout, garbage, no CUDA device) means CPU.
v1 runs on CPU by default; the probe only runs for ``--device auto``.
"""

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 90.0
_PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent)


@dataclass
class ProbeResult:
    ok: bool
    reason: str


def child_command(model_dir: Path) -> list:
    args = ["--probe-gpu", "--model-dir", str(model_dir)]
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return [sys.executable, *args]
    return [sys.executable, "-P", "-m", "transcription_flow", *args]


def _child_env() -> dict:
    """The child imports the package, which applies the privacy env itself.
    From a source checkout (dictation.py) the package is not installed, so
    put its folder on the child's path."""
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (_PACKAGE_PARENT, env.get("PYTHONPATH")) if p)
    return env


def probe(command: list, timeout: float = PROBE_TIMEOUT) -> ProbeResult:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              env=_child_env(), stdin=subprocess.DEVNULL, creationflags=flags)
    except subprocess.TimeoutExpired:
        return ProbeResult(False, f"timed out after {timeout:.0f} s")
    except OSError as err:
        return ProbeResult(False, f"could not start: {type(err).__name__}")
    if done.returncode != 0:
        return ProbeResult(False, f"exited with code {done.returncode}")
    lines = done.stdout.strip().splitlines()
    try:
        report = json.loads(lines[-1])
        return ProbeResult(report["ok"] is True, str(report.get("reason", "")))
    except (IndexError, ValueError, KeyError, TypeError):
        return ProbeResult(False, "unreadable probe output")


def run_child(model_dir: Path) -> int:
    """Body of ``--probe-gpu``: prints one JSON line, exits 0 unless it crashes."""
    import ctranslate2

    if ctranslate2.get_cuda_device_count() < 1:
        print(json.dumps({"ok": False, "reason": "no CUDA device"}), flush=True)
        return 0
    import numpy as np

    from .engine import WhisperEngine
    from .hallucination import ShortPhraseDropped

    engine = WhisperEngine(model_dir, device="cuda").load()
    try:
        engine.transcribe(np.zeros(16000, dtype=np.float32))
    except ShortPhraseDropped:  # a "you" heard in the silence: CUDA still ran
        pass
    print(json.dumps({"ok": True, "reason": "CUDA transcription worked"}), flush=True)
    return 0


def choose_device(requested: str, model_dir: Path, cache_file: Path, run_probe=probe) -> str:
    """'cpu' unless requested == 'auto' and a (cached) probe passed. Never raises."""
    if requested != "auto":
        return "cpu"
    key = {"app": __version__, "model": Path(model_dir).name}
    try:
        cached = json.loads(cache_file.read_text("utf-8"))
        if cached.get("key") == key:
            return "cuda" if cached.get("ok") is True else "cpu"
    except (OSError, ValueError, AttributeError):
        pass
    try:
        result = run_probe(child_command(model_dir))
    except Exception as err:  # fail safe
        result = ProbeResult(False, f"probe error: {type(err).__name__}")
    log.info("GPU probe: ok=%s (%s)", result.ok, result.reason)
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"key": key, "ok": result.ok, "reason": result.reason}),
                              "utf-8")
    except OSError as err:
        log.warning("could not cache the GPU probe result: %r", err)
    return "cuda" if result.ok else "cpu"
