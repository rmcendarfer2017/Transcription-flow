"""``--check-bundle`` (hidden): import every part the app loads lazily or
through a platform backend, then print a JSON report. CI runs it on the
built onedir and on the installed copy (review-m4 F1): ``--selftest`` never
touches PortAudio, the keyboard hook, the tray or Tk, so a bundle that
lacks one of them would pass CI and crash at start on the user's PC.

No window is shown, nothing is recorded and no hook is installed.
"""

import importlib
import json
import sys

# Every module of the app except __main__ (which would start it). A test
# checks this list against the source tree, so a new module can't be missed.
APP_MODULES = (
    "_env", "app", "autostart", "bundlecheck", "clipboard", "config", "controller", "engine",
    "gpu_probe", "hallucination", "hotkey", "logsetup", "main", "model_service", "models",
    "output", "paths", "recorder", "selftest", "single_instance",
    "ui", "ui.display", "ui.overlay", "ui.settings", "ui.tray", "ui.wizard",
)

# The backends pynput and pystray pick at run time (importlib, so
# PyInstaller's analysis can't see them).
BACKENDS = {"win32": ("pynput.keyboard._win32", "pystray._win32")}


def _sounddevice() -> str:
    import sounddevice  # loads the PortAudio DLL: an ImportError/OSError here fails

    try:
        devices = sounddevice.query_devices()
    except sounddevice.PortAudioError as err:  # no audio device on the runner is fine
        return f"PortAudio loaded; no devices ({err})"
    return f"PortAudio loaded; {len(devices)} devices"


def _backends(platform: str) -> str:
    names = BACKENDS.get(platform, ())
    for name in names:
        importlib.import_module(name)
    from pynput import keyboard

    keyboard.Controller  # noqa: B018 (resolves the backend class)
    import pystray

    pystray.Icon  # noqa: B018
    return ", ".join(names) or f"no fixed backend on {platform}"


def _tk() -> str:
    import tkinter

    root = tkinter.Tk()  # needs the bundled Tcl/Tk data folders
    try:
        root.withdraw()
        return f"Tk {root.call('info', 'patchlevel')}"
    finally:
        root.destroy()


def _app_modules() -> str:
    for name in APP_MODULES:
        importlib.import_module(f"transcription_flow.{name}")
    return f"{len(APP_MODULES)} modules"


def run(platform: str = sys.platform) -> dict:
    checks, ok = {}, True
    for name, check in (("sounddevice", _sounddevice),
                        ("backends", lambda: _backends(platform)),
                        ("tkinter", _tk), ("app modules", _app_modules)):
        try:
            checks[name] = check()
        except Exception as err:  # report every part, not just the first missing one
            checks[name] = f"FAIL {type(err).__name__}: {err}"
            ok = False
    return {"ok": ok, "checks": checks}


def main(platform: str = sys.platform) -> int:
    report = run(platform)
    print(json.dumps(report))
    return 0 if report["ok"] else 1
