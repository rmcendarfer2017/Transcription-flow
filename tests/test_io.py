"""Hotkey tracking and logging setup."""

import io
import logging
import logging.handlers
import sys

import pytest

from helpers import flush_logs
from transcription_flow import logsetup
from transcription_flow.hotkey import HotkeyTracker


@pytest.fixture
def events():
    log = []
    tracker = HotkeyTracker(lambda: log.append("press"), lambda: log.append("release"),
                            lambda reason: log.append("other"))
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


def test_none_streams_go_to_the_log(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    path = logsetup.setup(tmp_path / "logs")
    print("progress bar text")
    sys.stderr.write("partial ")
    sys.stderr.write("warning line\n")
    sys.stderr.flush()
    flush_logs()
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


def test_console_gets_one_line_and_the_file_gets_the_traceback(tmp_path, monkeypatch):
    console = io.StringIO()
    monkeypatch.setattr(sys, "stderr", console)
    path = logsetup.setup(tmp_path)
    log = logging.getLogger("transcription_flow.test")
    log.info("routine detail")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.warning("The overlay could not start.", exc_info=True)
    flush_logs()
    assert console.getvalue() == "[!] The overlay could not start.\n"  # QA B1
    text = path.read_text("utf-8")
    assert "routine detail" in text and "Traceback" in text and "RuntimeError: boom" in text
