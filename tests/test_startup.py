"""Starting the app (main._run_dictation) with fake UI, audio and engine:
an offline start never downloads and never exits for a missing model
(review-m2 F6, QA B3), and with no model at all the setup window opens."""

import json
import socket
import sys
import types

import pytest

from helpers import wait_for
from transcription_flow import config, models, paths
from transcription_flow import main as app_main

NAMES = ("tiny.en", "base.en", "small.en")


def fake_manifest():
    return {"version": 1, "models": {
        name: {"repo": f"x/{name}", "revision": "r" * 40, "license": "MIT",
               "files": [{"name": "model.bin", "size": 10 * (i + 1), "sha256": "0" * 64}]}
        for i, name in enumerate(NAMES)}}


def install(name):
    """Make ``name`` count as installed (right sizes and marker)."""
    spec = fake_manifest()["models"][name]
    folder = models.model_path(name, paths.models_dir())
    folder.mkdir(parents=True, exist_ok=True)
    for f in spec["files"]:
        (folder / f["name"]).write_bytes(b"x" * f["size"])
    (folder / models.MARKER).write_text(json.dumps(models._marker_for(spec)), "utf-8")


class Seen:
    engines, windows, wizards, trays, overlays = [], [], [], [], []
    while_running = None  # called from FakeOverlay.run, while the app "runs"
    broken = {"broken"}  # model folders whose load fails


class FakeEngine:
    def __init__(self, model_dir, device="cpu", language="en"):
        self.model_dir, self.device, self.language = model_dir, device, language

    def load(self):
        Seen.engines.append(self)
        if len(Seen.engines) > 10:  # a retry loop: pytest.fail gets past main's except Exception
            pytest.fail("the start kept loading models")
        if self.model_dir.name in Seen.broken:
            raise RuntimeError("bad model.bin")
        return self


class FakeRecorder:
    def __init__(self, device=None, keep_warm=False):
        self.device, self.keep_warm, self.is_open = device, keep_warm, False

    def check(self):
        pass

    def levels(self):
        return [0.0] * 6

    def close(self):
        pass


class FakeOverlay:
    def __init__(self, levels):
        self.root = "root"
        self.calls = []
        Seen.overlays.append(self)

    def post(self, state):
        pass

    def call(self, fn):
        fn()

    def run(self):  # the user quits at once, unless a test gave the copy something to do
        if Seen.while_running is not None:
            Seen.while_running()

    def quit(self):
        pass


class FakeTray:
    def __init__(self, actions):
        self.notes, self.statuses = [], []
        Seen.trays.append(self)

    def start(self):
        pass

    def set_status(self, status, paused=None):
        self.statuses.append(status)

    def notify(self, message):
        self.notes.append(message)

    def stop(self):
        pass


class FakeWizard:
    def __init__(self, parent, app, on_close):
        self.app = app
        Seen.wizards.append(self)

    def close(self):
        pass


class FakeSettings:
    def __init__(self, parent, form, available, on_save, on_close, app=None):
        Seen.windows.append(self)

    def lift(self):
        pass

    def close(self):
        pass


class FakeHotkey:
    def __init__(self, *callbacks):
        pass

    def start(self, key):
        pass

    def stop(self):
        pass


@pytest.fixture
def started(monkeypatch):
    for name in ("engines", "windows", "wizards", "trays", "overlays"):
        setattr(Seen, name, [])
    Seen.broken = {"broken"}
    Seen.while_running = None
    monkeypatch.setattr(models, "load_manifest", fake_manifest)
    monkeypatch.setattr(models, "download", lambda *a, **k: pytest.fail("downloaded at start"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network at start"))
    from transcription_flow import engine, hotkey, recorder

    monkeypatch.setattr(engine, "WhisperEngine", FakeEngine)
    monkeypatch.setattr(recorder, "Recorder", FakeRecorder)
    monkeypatch.setattr(hotkey, "Hotkey", FakeHotkey)
    import transcription_flow.ui as ui_pkg
    from transcription_flow.ui import settings, tray, wizard

    monkeypatch.setattr(settings, "SettingsWindow", FakeSettings)
    monkeypatch.setattr(tray, "Tray", FakeTray)
    monkeypatch.setattr(wizard, "WizardWindow", FakeWizard)
    # overlay.py imports tkinter at the top, which a test box may not have.
    fake_overlay = types.SimpleNamespace(Overlay=FakeOverlay)
    monkeypatch.setitem(sys.modules, "transcription_flow.ui.overlay", fake_overlay)
    monkeypatch.setattr(ui_pkg, "overlay", fake_overlay, raising=False)

    def run(*argv, model="small.en"):
        config.save(config.Config(model=model), paths.config_file())
        return app_main.main(["--tray", *argv])

    return run


def test_offline_start_with_the_chosen_model_missing_uses_an_installed_one(started):
    """QA B3: base.en chosen but not downloaded, offline: it starts with small.en."""
    install("small.en")
    assert started(model="base.en") == app_main.EXIT_OK
    assert [e.model_dir.name for e in Seen.engines] == ["small.en"]
    assert Seen.trays[0].notes == ["base.en isn't downloaded yet; using small.en. Open "
                                   "Settings and press Save to download it."]
    assert Seen.wizards == []
    assert config.load(paths.config_file()).model == "base.en"  # the choice is kept


def test_the_fallback_prefers_the_default_then_the_largest_installed(started):
    install("tiny.en")
    install("base.en")
    started(model="small.en")
    assert Seen.engines[-1].model_dir.name == "base.en"


def test_a_start_with_the_chosen_model_installed_says_ready(started):
    install("tiny.en")
    install("small.en")
    started(model="tiny.en")
    assert Seen.engines[-1].model_dir.name == "tiny.en"
    assert Seen.trays[0].notes == ["Ready. Hold Right Ctrl to dictate."]


def test_no_model_installed_opens_the_setup_window_and_keeps_running(started):
    assert started() == app_main.EXIT_OK  # never EXIT_SETUP: no sign-in loop
    assert Seen.engines == [] and len(Seen.wizards) == 1
    assert Seen.trays[0].statuses[-1] == "No speech model yet"


def test_a_model_that_fails_to_load_keeps_the_app_running_for_the_setup_window(started):
    assert started("--model-dir", str(paths.data_dir() / "broken")) == app_main.EXIT_OK
    assert len(Seen.wizards) == 1  # a way to fetch one again, not an exit
    assert "could not be loaded" in (paths.logs_dir() / "app.log").read_text("utf-8")


def test_a_chosen_model_that_fails_to_load_gives_way_to_another_installed_one(started):
    """QA B2: a corrupt small.en with a good tiny.en starts on tiny.en, says
    so in a toast and doesn't open the setup window."""
    install("tiny.en")
    install("small.en")
    Seen.broken = {"small.en"}
    assert started(model="small.en") == app_main.EXIT_OK
    assert [e.model_dir.name for e in Seen.engines] == ["small.en", "tiny.en"]
    assert Seen.wizards == []
    assert Seen.trays[0].notes == ["small.en could not be loaded; using tiny.en. Open Settings "
                                   "and press Save to download it again."]
    assert Seen.trays[0].statuses[-1] == "Ready (CPU)"


def test_a_fallback_that_fails_too_tries_the_next_and_the_toast_says_not_downloaded(started):
    install("tiny.en")
    install("small.en")
    Seen.broken = {"small.en"}  # the default fallback for a missing base.en
    started(model="base.en")
    assert [e.model_dir.name for e in Seen.engines] == ["small.en", "tiny.en"]
    assert Seen.trays[0].notes == ["base.en isn't downloaded yet; using tiny.en. Open "
                                   "Settings and press Save to download it."]


def test_when_every_installed_model_fails_to_load_the_setup_window_opens_once(started):
    install("tiny.en")
    install("small.en")
    Seen.broken = {"small.en", "tiny.en"}
    assert started(model="small.en") == app_main.EXIT_OK
    assert [e.model_dir.name for e in Seen.engines] == ["small.en", "tiny.en"]
    assert len(Seen.wizards) == 1
    assert Seen.trays[0].statuses[-1] == "No speech model yet"


def second_launch(*argv):
    """Start another copy while the first runs; returns its exit code once
    the first copy has had time to react."""
    codes = []

    def launch():
        codes.append(app_main.main(list(argv)))
        wait_for(lambda: Seen.windows or len(Seen.wizards) > 1, timeout=2)

    Seen.while_running = launch
    return codes


def test_a_second_launch_opens_settings_in_the_running_copy_and_exits_0(started, capsys):
    """review-m3 deviation 3: under pythonw a second copy used to exit with
    nothing on screen."""
    install("small.en")
    codes = second_launch()
    assert started() == app_main.EXIT_OK
    assert codes == [app_main.EXIT_OK]
    assert len(Seen.windows) == 1 and Seen.wizards == []
    assert "already running; its window is open" in capsys.readouterr().err


def test_a_second_launch_with_no_model_brings_up_the_setup_window(started):
    codes = second_launch()
    started()
    assert codes == [app_main.EXIT_OK]
    assert len(Seen.wizards) == 1 and Seen.windows == []  # the open one, lifted


@pytest.mark.parametrize("argv", [("--tray",), ("--reset-config",),
                                  ("--download-model", "--model", "tiny.en")])
def test_a_sign_in_start_or_a_one_off_command_opens_nothing(started, argv):
    install("small.en")
    codes = second_launch(*argv)
    started()
    assert codes == [app_main.EXIT_ALREADY_RUNNING]
    assert Seen.windows == []


def test_after_quit_the_listener_is_gone(started):
    install("small.en")
    started()
    from transcription_flow import single_instance

    assert single_instance.request_show(paths.data_dir()) is False
