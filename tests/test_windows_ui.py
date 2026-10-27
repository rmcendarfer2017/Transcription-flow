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


class FakeMeter:
    def __init__(self, device, status="blocked", fail=False):
        self.device, self._status, self.fail = device, status, fail
        self.started = self.stopped = 0

    def start(self):
        self.started += 1
        if self.fail:
            from transcription_flow.recorder import NoMicrophoneError

            raise NoMicrophoneError("No microphone found.")

    def stop(self):
        self.stopped += 1

    def level(self):
        return 0.0

    def status(self):
        return self._status


def test_wizard_window_walks_every_step(root):
    from test_wizard import FakeApp, job

    from transcription_flow.ui.wizard import WizardWindow

    app, meters, closed = FakeApp(), [], []
    app.meter_factory = lambda device: meters.append(FakeMeter(device)) or meters[-1]
    win = WizardWindow(root, app, lambda: closed.append(True))
    root.update()
    assert win.flow.step == "welcome" and "disabled" in win.back_button.state()
    win.next()  # mic: the meter runs and the blocked case shows the privacy button
    root.update()
    assert meters[0].started == 1 and meters[0].device is None
    win._tick_meter()
    root.update()
    assert win.privacy_button.winfo_ismapped()
    assert "blocking" in win.mic_label.cget("text")
    win.mic_combo.set("Headset mic")
    win.mic_combo.event_generate("<<ComboboxSelected>>")
    root.update()
    assert app.config.mic == "Headset mic" and meters[0].stopped
    assert meters[1].device == "Headset mic"
    win.next()  # model: Next waits for a model
    root.update()
    assert meters[1].stopped and "disabled" in win.next_button.state()
    app.job = job("small.en", 42)
    win.model_changed()
    root.update()
    assert win.progress.winfo_ismapped() and win.cancel_button.winfo_ismapped()
    app.job = None
    win.model_changed("The download failed: offline.")
    root.update()
    assert win.retry_button.winfo_ismapped() and win.smaller_button.winfo_ismapped()
    assert win.folder_button.winfo_ismapped() and not win.progress.winfo_ismapped()
    app.model = "small.en"
    win.model_changed()
    root.update()
    assert not win.next_button.instate(["disabled"])
    win.next()  # hotkey
    win.hotkey_combo.set("F9")
    win.hotkey_combo.event_generate("<<ComboboxSelected>>")
    assert app.config.hotkey == "f9"
    win.next()  # try
    root.update()
    assert win.try_box.winfo_exists()
    win.next()  # done
    root.update()
    assert win.next_button.cget("text") == "Finish"
    win.next()
    assert closed == [True] and win.window is None


def test_wizard_window_with_no_microphone_says_so(root):
    from test_wizard import FakeApp

    from transcription_flow.ui.wizard import WizardWindow

    app = FakeApp()
    app.meter_factory = lambda device: FakeMeter(device, fail=True)
    win = WizardWindow(root, app)
    win.next()
    root.update()
    assert win.mic_label.cget("text") == "No microphone found."
    win.close()


def test_settings_window_lists_mics_and_runs_the_meter(root):
    from test_wizard import FakeApp

    from transcription_flow.ui.settings import Form, SettingsWindow

    app, meters, saved = FakeApp(), [], []
    app.meter_factory = lambda device: meters.append(FakeMeter(device)) or meters[-1]
    form = Form("right_ctrl", "small.en", "en", "type", False, False, "USB mic")
    win = SettingsWindow(root, form, True, saved.append, app=app)
    root.update()
    assert win.mic.get() == "USB mic (not connected)" and meters[0].device == "USB mic"
    win.mic.set("Laptop mic")
    win.mic.event_generate("<<ComboboxSelected>>")
    root.update()
    assert meters[0].stopped and meters[1].device == "Laptop mic"
    win.save()
    assert saved[0].mic == "Laptop mic" and meters[1].stopped
