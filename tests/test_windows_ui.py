"""Tk windows, on windows-latest only (Linux CI and the dev box have no Tk display).

These build the real widgets, so a typo in the Tk code fails CI rather than
the first time someone opens Settings.
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Tk UI runs on Windows only")


@pytest.fixture
def root():
    import tkinter as tk

    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


def test_settings_window_round_trip(root):
    from transcription_flow.ui.settings import Form, SettingsWindow

    saved, closed = [], []
    form = Form("caps_lock", "base.en", "en", "paste", True, False)
    win = SettingsWindow(root, form, True, saved.append, lambda: closed.append(True))
    root.update()
    assert win.form() == form
    assert "disabled" in win.language.state()  # .en models: English only
    win.hotkey.set("F9")
    win.autostart.set(True)
    win.save()
    assert saved == [Form("f9", "base.en", "en", "paste", True, True)]
    assert closed == [True] and win.window is None


def test_settings_window_without_autostart_disables_the_box(root):
    from tkinter import ttk

    from transcription_flow.ui.settings import Form, SettingsWindow

    win = SettingsWindow(root, Form("right_ctrl", "small.en", "en", "type", False, False),
                         False, lambda f: None)
    boxes = [w for w in win.window.winfo_children()[0].winfo_children()
             if isinstance(w, ttk.Checkbutton)]
    assert "disabled" in boxes[-1].state()
    win.close()


def test_overlay_builds_scaled_and_runs_posted_calls():
    from transcription_flow.ui import display
    from transcription_flow.ui.overlay import Overlay

    display.enable_dpi_awareness()
    overlay = Overlay(lambda: [0.5] * 6)
    try:
        ran = []
        overlay.post("recording")
        overlay.call(lambda: ran.append(True))
        overlay.call(lambda: 1 / 0)  # logged, the loop keeps going
        overlay._tick()
        assert ran == [True] and overlay._state == "recording"
        assert overlay.root.winfo_width() >= 1 and overlay.scale >= 1.0
    finally:
        overlay.root.destroy()
