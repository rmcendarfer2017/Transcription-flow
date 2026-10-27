"""Entry point. Nothing heavy is imported at module level; the package
__init__ has already applied the privacy environment (_env)."""

import argparse
import functools
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
    p.add_argument("--model", choices=sorted(load_manifest()["models"]),
                   help=f"model name (default: the one in settings, else {DEFAULT_MODEL})")
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
    p.add_argument("--tray", action="store_true",
                   help="started at sign-in: stay in the tray (the M3 first-run window skips "
                        "itself)")
    p.add_argument("--reset-config", action="store_true",
                   help="put every setting back to its default, then exit")
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
    also gets the traceback. Once the tray is up, App.report_error adds a toast."""
    log.warning(message, exc_info=exc_info)


def run_app(args, models_dir: Path) -> int:
    """Everything that writes app.log or the settings: the single-instance
    guard comes first, so a second copy never opens the log (review-m1)."""
    from . import config, logsetup, models, paths, single_instance

    try:
        guard = single_instance.acquire(paths.data_dir())
    except single_instance.AlreadyRunning:
        if sys.stderr is not None:
            print("[!] Transcription Flow is already running; this copy will exit.",
                  file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    try:
        log_file = logsetup.setup(paths.logs_dir())
        if args.reset_config:
            config.save(config.Config(), paths.config_file())
            print(f"Settings reset: {paths.config_file()}")
            return EXIT_OK
        settings = config.load(paths.config_file())
        name = args.model or settings.model
        if args.download_model or args.verify:
            folder = _download(name, models_dir, verify=args.verify)
            if folder is None:
                return EXIT_SETUP
            print(folder)
            return EXIT_OK
        model_dir = args.model_dir or models.model_path(name, models_dir)
        if args.model_dir is None and not models.is_installed(name, models_dir):
            print(f"First run: downloading the {name} model (one time only).", file=sys.stderr)
            if _download(name, models_dir) is None:
                return EXIT_SETUP
        return _run_dictation(args, settings, model_dir, log_file)
    finally:
        guard.release()


def _run_dictation(args, settings, model_dir, log_file) -> int:
    from . import autostart, gpu_probe, paths
    from .app import App
    from .controller import Controller
    from .engine import WhisperEngine
    from .hotkey import Hotkey, HotkeyUnavailable
    from .output import make_output
    from .recorder import MicrophoneError, Recorder
    from .ui import display

    device = gpu_probe.choose_device(args.device, model_dir, paths.data_dir() / "gpu_probe.json")
    language = None if settings.language == "auto" else settings.language
    try:
        engine = WhisperEngine(model_dir, device=device, language=language).load()
    except Exception as err:
        _notify(f"The speech model could not be loaded ({type(err).__name__}). Log: {log_file}",
                exc_info=True)
        return EXIT_SETUP

    keep_warm = args.keep_mic_warm or settings.keep_mic_warm
    recorder = Recorder(keep_warm=keep_warm)
    startup_errors = []
    try:
        recorder.check()
        if keep_warm:
            recorder.open()
    except MicrophoneError as err:  # the recorder logged it; keep running
        startup_errors.append(str(err))

    log.info("DPI awareness: %s", display.enable_dpi_awareness())  # before the first window
    try:
        from .ui.overlay import Overlay
        from .ui.settings import SettingsWindow
        from .ui.tray import Tray

        overlay = Overlay(recorder.levels)
    except Exception as err:
        _notify(f"The on-screen indicator could not start ({type(err).__name__}).",
                exc_info=True)
        return EXIT_SETUP

    app = None

    def report(message):
        if app is None:
            _notify(message)
        else:
            app.report_error(message)

    def on_state(state):
        overlay.post(state)
        if app is not None:
            app.on_state(state)

    # The settle is a file-only setting, read at start.
    output_factory = functools.partial(make_output, settle=settings.paste_settle_seconds)
    controller = Controller(recorder, engine, output_factory(settings.output), ui=on_state,
                            on_error=report)
    controller.start()
    app = App(settings, paths.config_file(), controller,
              Hotkey(lambda unchecked: app.press(unchecked), controller.release,
                     lambda r: app.cancel_recording(r)),
              Tray, autostart.RunKey(), ui_call=overlay.call, stop_ui=overlay.quit,
              settings_factory=lambda *a: SettingsWindow(overlay.root, *a),
              output_factory=output_factory,
              launch_command=autostart.launch_command, log_dir=paths.logs_dir(), device=device)
    try:
        app.start()
    except HotkeyUnavailable as err:
        _notify(str(err))
        controller.shutdown(timeout=5)
        return EXIT_SETUP
    for message in startup_errors:
        app.report_error(message)
    log.info("ready on %s", device)
    if sys.stderr is not None:
        print(f"Ready. Hold {app.hotkey_label()} to dictate, release to type the text. "
              "Quit from the tray icon.", file=sys.stderr)
    try:
        overlay.run()
    except KeyboardInterrupt:
        pass
    finally:
        if not app.quitting:
            app.quit()
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
    from . import models, paths

    models_dir = args.models_dir or paths.models_dir()
    if args.probe_gpu or args.selftest:
        model_dir = args.model_dir or models.model_path(args.model or models.DEFAULT_MODEL,
                                                        models_dir)
        if args.probe_gpu:
            from . import gpu_probe

            return gpu_probe.run_child(model_dir)
        return _selftest(args.selftest, model_dir, args.expect)
    return run_app(args, models_dir)
