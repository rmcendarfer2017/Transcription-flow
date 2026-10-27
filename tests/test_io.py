"""Hotkey tracking, typing output and logging setup."""

import io
import logging
import logging.handlers
import sys

import pytest

from transcription_flow import logsetup
from transcription_flow.hotkey import HotkeyTracker, KeyboardHotkey
from transcription_flow.output import KeyboardTyper


@pytest.fixture
def events():
    log = []
    tracker = HotkeyTracker(lambda: log.append("press"), lambda: log.append("release"),
                            lambda: log.append("other"))
    return tracker, log


def test_hold_and_release(events):
    tracker, log = events
    tracker.key_down(True)
    tracker.key_down(True)  # auto-repeat
    tracker.key_up(True)
    assert log == ["press", "release"]


def test_other_key_while_held_cancels(events):
    tracker, log = events
    tracker.key_down(True)
    tracker.key_down(False)
    tracker.key_up(False)
    tracker.key_up(True)
    assert log == ["press", "other", "release"]


def test_other_keys_when_not_held_are_ignored(events):
    tracker, log = events
    tracker.key_down(False)
    tracker.key_up(False)
    tracker.key_up(True)  # stray up (e.g. held before start)
    assert log == []


class FakeKeyboard:
    def __init__(self):
        self.handler = None
        self.unhooked = None
        self.written = []

    def key_to_scan_codes(self, name):
        assert name == "right ctrl"
        return (157,)

    def hook(self, handler):
        self.handler = handler
        return "remove-token"

    def unhook(self, token):
        self.unhooked = token

    def write(self, text):
        self.written.append(text)

    def send(self, scan_code, event_type):
        self.handler(type("E", (), {"scan_code": scan_code, "event_type": event_type})())


def test_keyboard_hotkey_adapter_routes_events():
    kb, log = FakeKeyboard(), []
    hk = KeyboardHotkey("right ctrl", keyboard_module=kb)
    hk.start(lambda: log.append("press"), lambda: log.append("release"),
             lambda: log.append("other"))
    kb.send(157, "down")
    kb.send(46, "down")  # C
    kb.send(157, "up")
    hk.stop()
    assert log == ["press", "other", "release"]
    assert kb.unhooked == "remove-token"


def test_typer_types_and_touches_nothing_else():
    kb = FakeKeyboard()
    KeyboardTyper(keyboard_module=kb).emit("Hello world.")
    assert kb.written == ["Hello world."]


def test_none_streams_go_to_the_log(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    path = logsetup.setup(tmp_path / "logs")
    print("progress bar text")
    sys.stderr.write("partial ")
    sys.stderr.write("warning line\n")
    sys.stderr.flush()
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text("utf-8")
    assert "progress bar text" in text and "partial warning line" in text
    assert not sys.stderr.isatty()


def test_setup_is_idempotent_and_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    logsetup.setup(tmp_path)
    logsetup.setup(tmp_path)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_tflow", False)]
    files = [h for h in ours if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(files) == 1 and len(ours) == 2
    assert files[0].maxBytes == 1_000_000 and files[0].backupCount == 3
