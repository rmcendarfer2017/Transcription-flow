"""Entry point. Nothing heavy is imported at module level: the privacy
environment (_env) is applied first, then faster-whisper and friends load."""

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__, _env

log = logging.getLogger("transcription_flow")

EXIT_OK, EXIT_FAIL, EXIT_SETUP, EXIT_ALREADY_RUNNING = 0, 1, 2, 3


def _parser() -> argparse.ArgumentParser:
    from .models import DEFAULT_MODEL

    p = argparse.ArgumentParser(prog="transcription-flow",
                                description="Local push-to-talk dictation.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--model", default=DEFAULT_MODEL, help="model name (default: %(default)s)")
    p.add_argument("--models-dir", type=Path, help="folder that holds downloaded models")
    p.add_argument("--model-dir", type=Path,
                   help="use this model folder as is (skips the manifest check and download)")
    p.add_argument("--device", choices=["cpu", "auto"], default="cpu",
                   help="cpu (default), or auto: use an NVIDIA GPU if the GPU probe passes")
    p.add_argument("--keep-mic-warm", action="store_true",
                   help="keep the microphone open between dictations (adds pre-roll)")
    p.add_argument("--download-model", action="store_true",
                   help="download and verify --model, then exit")
    p.add_argument("--selftest", type=Path, metavar="WAV",
                   help="transcribe WAV on CPU, print JSON, exit 0 (ok) or 1")
    p.add_argument("--expect", help="with --selftest: text the transcript must contain")
    p.add_argument("--probe-gpu", action="store_true", help=argparse.SUPPRESS)
    return p


def _console_progress(name: str):
    last = [-1]

    def progress(done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 100
        if pct != last[0] and sys.stderr is not None:
            last[0] = pct
            end = "\n" if done >= total else ""
            print(f"\rDownloading {name}: {pct:3d}% ({done >> 20} of {total >> 20} MB)",
                  end=end, file=sys.stderr, flush=True)

    return progress


def _download(name: str, models_dir: Path) -> int:
    from . import models

    try:
        folder = models.download(name, models_dir, progress=_console_progress(name))
    except models.ModelError as err:
        print(f"Model download failed: {err}", file=sys.stderr)
        return EXIT_SETUP
    print(folder)
    return EXIT_OK


def _selftest(wav: Path, model_dir: Path, expect) -> int:
    if not model_dir.is_dir():
        report = {"ok": False, "error": f"model folder not found: {model_dir}"}
    else:
        from . import selftest

        try:
            report = selftest.run(wav, model_dir, expect=expect)
        except Exception as err:  # report, don't crash: CI reads the JSON
            report = {"ok": False, "error": f"{type(err).__name__}: {err}"}
    print(json.dumps(report))
    return EXIT_OK if report["ok"] else EXIT_FAIL


def _notify(message: str) -> None:
    """User-facing problem. M1: stderr and the log; M2 adds tray toasts."""
    if sys.stderr is not None:
        print(f"[!] {message}", file=sys.stderr, flush=True)


def run_app(args, model_dir: Path, models_dir: Path) -> int:
    from . import gpu_probe, logsetup, models, paths, single_instance

    log_file = logsetup.setup(paths.logs_dir())
    try:
        guard = single_instance.acquire(paths.data_dir())
    except single_instance.AlreadyRunning:
        log.error("Transcription Flow is already running; this copy will exit.")
        return EXIT_ALREADY_RUNNING
    try:
        if args.model_dir is None and not models.is_installed(args.model, models_dir):
            print(f"First run: downloading the {args.model} model (one time only).",
                  file=sys.stderr)
            try:
                models.download(args.model, models_dir, progress=_console_progress(args.model))
            except models.ModelError as err:
                log.error("model download failed: %s", err)
                _notify(f"Model download failed: {err}")
                return EXIT_SETUP
        return _run_dictation(args, model_dir, gpu_probe, paths, log_file)
    finally:
        guard.release()


def _run_dictation(args, model_dir, gpu_probe, paths, log_file) -> int:
    from .controller import Controller
    from .engine import WhisperEngine
    from .hotkey import DEFAULT_HOTKEY, KeyboardHotkey
    from .output import KeyboardTyper
    from .recorder import MicrophoneError, Recorder

    device = gpu_probe.choose_device(args.device, model_dir, paths.data_dir() / "gpu_probe.json")
    try:
        engine = WhisperEngine(model_dir, device=device).load()
    except Exception as err:
        log.exception("model load failed")
        _notify(f"The speech model could not be loaded ({type(err).__name__}). Log: {log_file}")
        return EXIT_SETUP

    recorder = Recorder(keep_warm=args.keep_mic_warm)
    try:
        recorder.check()
        if args.keep_mic_warm:
            recorder.open()
    except MicrophoneError as err:
        _notify(str(err))  # keep running: the next press tries again

    try:
        from .ui.overlay import Overlay

        overlay = Overlay(recorder.levels)
    except Exception as err:
        log.exception("overlay failed")
        _notify(f"The on-screen indicator could not start ({type(err).__name__}).")
        return EXIT_SETUP

    controller = Controller(recorder, engine, KeyboardTyper(), ui=overlay.post, on_error=_notify)
    controller.start()
    hotkey = KeyboardHotkey(DEFAULT_HOTKEY)
    try:
        hotkey.start(controller.press, controller.release, controller.other_key)
    except ImportError:
        _notify("The global hotkey needs Windows (the 'keyboard' package) for now.")
        controller.shutdown(timeout=5)
        return EXIT_SETUP
    log.info("ready on %s", device)
    print(f"Ready. Hold {DEFAULT_HOTKEY} to dictate, release to type the text.", file=sys.stderr)
    try:
        overlay.run()
    except KeyboardInterrupt:
        pass
    finally:
        hotkey.stop()
        controller.shutdown(timeout=5)
    return EXIT_OK


def main(argv=None) -> int:
    _env.apply()
    args = _parser().parse_args(argv)
    from . import models, paths

    models_dir = args.models_dir or paths.models_dir()
    model_dir = args.model_dir or models.model_path(args.model, models_dir)
    if args.probe_gpu:
        from . import gpu_probe

        return gpu_probe.run_child(model_dir)
    if args.selftest:
        return _selftest(args.selftest, model_dir, args.expect)
    if args.download_model:
        return _download(args.model, models_dir)
    return run_app(args, model_dir, models_dir)
