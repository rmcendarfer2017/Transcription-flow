"""Starting the app (main._run_dictation) with fake UI, audio and engine:
an offline start never downloads and never exits for a missing model
(review-m2 F6, QA B3), and with no model at all the setup window opens."""

import json
import socket
import sys
import types

import pytest

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


class FakeEngine:
    def __init__(self, model_dir, device="cpu", language="en"):
        self.model_dir, self.device, self.language = model_dir, device, language

    def load(self):
        Seen.engines.append(self)
        if self.model_dir.name == "broken":
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

    def run(self):  # the user quits at once
        pass

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
    monkeypatch.setattr(models, "load_manifest", fake_manifest)
    monkeypatch.setattr(models, "download", lambda *a, **k: pytest.fail("downloaded at start"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network at start"))
    from transcription_flow import engine, hotkey, recorder

    monkeypatch.setattr(engine, "WhisperEngine", FakeEngine)
    monkeypatch.setattr(recorder, "Recorder", FakeRecorder)
    monkeypatch.setattr(hotkey, "Hotkey", FakeHotkey)
    import transcription_flow.ui as ui_pkg
    from transcription_flow.ui import tray, wizard

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
