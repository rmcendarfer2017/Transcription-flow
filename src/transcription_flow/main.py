"""Entry point. Nothing heavy is imported at module level; the package
__init__ has already applied the privacy environment (_env)."""

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__

log = logging.getLogger("transcription_flow")

EXIT_OK, EXIT_FAIL, EXIT_SETUP, EXIT_ALREADY_RUNNING = 0, 1, 2, 3
EXIT_INTERRUPTED = 130


def _parser() -> argparse.ArgumentParser:
    from .models import DEFAULT_MODEL, load_manifest

    p = argparse.ArgumentParser(prog="transcription-flow",
                                description="Local push-to-talk dictation.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(load_manifest()["models"]),
                   help="model name (default: %(default)s)")
    p.add_argument("--models-dir", type=Path, help="folder that holds downloaded models")
    p.add_argument("--model-dir", type=Path,
                   help="use this model folder as is (skips the manifest check and download)")
    p.add_argument("--device", choices=["cpu", "auto"], default="cpu",
                   help="cpu (default), or auto: use an NVIDIA GPU if the GPU probe passes")
    p.add_argument("--keep-mic-warm", action="store_true",
                   help="keep the microphone open between dictations (adds pre-roll)")
    p.add_argument("--download-model", action="store_true",
                   help="download and verify --model, then exit")
    p.add_argument("--verify", action="store_true",
                   help="like --download-model, but re-check the checksums of every "
                        "installed file first (re-downloads any that fail)")
    p.add_argument("--selftest", type=Path, metavar="WAV",
                   help="transcribe WAV on CPU, print JSON, exit 0 (ok) or 1")
    p.add_argument("--expect", help="with --selftest: text the transcript must contain")
    p.add_argument("--probe-gpu", action="store_true", help=argparse.SUPPRESS)
    return p


class _ConsoleProgress:
    """A one-line progress display; end_line() closes an unfinished line so
    the next message starts on its own line (QA B2)."""

    def __init__(self, name: str):
        self._name = name
        self._last = -1
        self._open = False

    def __call__(self, done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 100
        if pct != self._last and sys.stderr is not None:
            self._last = pct
            self._open = done < total
            print(f"\rDownloading {self._name}: {pct:3d}% ({done >> 20} of {total >> 20} MB)",
                  end="" if self._open else "\n", file=sys.stderr, flush=True)

    def end_line(self) -> None:
        if self._open and sys.stderr is not None:
            print(file=sys.stderr, flush=True)
        self._open = False


def _download(name: str, models_dir: Path, verify: bool = False):
    """Download with a console progress line; returns the folder or None."""
    from . import models

    progress = _ConsoleProgress(name)
    try:
        try:
            return models.download(name, models_dir, progress=progress, verify=verify)
        finally:
            progress.end_line()
    except models.ModelError as err:
        _notify(f"Model download failed: {err}")
        return None


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


def _notify(message: str, exc_info: bool = False) -> None:
    """User-facing problem: the console (one line) and the log file, which
    also gets the traceback. M2 adds tray toasts."""
    log.warning(message, exc_info=exc_info)


def run_app(args, model_dir: Path, models_dir: Path) -> int:
    from . import logsetup, models, paths, single_instance

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
            if _download(args.model, models_dir) is None:
                return EXIT_SETUP
        return _run_dictation(args, model_dir, log_file)
    finally:
        guard.release()


def _run_dictation(args, model_dir, log_file) -> int:
    from . import gpu_probe, paths
    from .controller import Controller
    from .engine import WhisperEngine
    from .hotkey import DEFAULT_HOTKEY, KeyboardHotkey
    from .output import KeyboardTyper
    from .recorder import MicrophoneError, Recorder

    device = gpu_probe.choose_device(args.device, model_dir, paths.data_dir() / "gpu_probe.json")
    try:
        engine = WhisperEngine(model_dir, device=device).load()
    except Exception as err:
        _notify(f"The speech model could not be loaded ({type(err).__name__}). Log: {log_file}",
                exc_info=True)
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
        _notify(f"The on-screen indicator could not start ({type(err).__name__}).",
                exc_info=True)
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
    args = _parser().parse_args(argv)
    try:
        return _main(args)
    except KeyboardInterrupt:
        if sys.stderr is not None:  # logging may not be set up yet
            print("[!] Cancelled. A model download that was cut short resumes next time.",
                  file=sys.stderr, flush=True)
        return EXIT_INTERRUPTED


def _main(args) -> int:
    from . import logsetup, models, paths

    models_dir = args.models_dir or paths.models_dir()
    model_dir = args.model_dir or models.model_path(args.model, models_dir)
    if args.probe_gpu:
        from . import gpu_probe

        return gpu_probe.run_child(model_dir)
    if args.selftest:
        return _selftest(args.selftest, model_dir, args.expect)
    if args.download_model or args.verify:
        logsetup.setup(paths.logs_dir())
        folder = _download(args.model, models_dir, verify=args.verify)
        if folder is None:
            return EXIT_SETUP
        print(folder)
        return EXIT_OK
    return run_app(args, model_dir, models_dir)
