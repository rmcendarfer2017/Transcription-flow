"""The setup window's flow and texts (WizardFlow), without a display."""

import dataclasses

import pytest

from transcription_flow.app import _Job
from transcription_flow.config import Config
from transcription_flow.recorder import OPEN_FAILED_MESSAGE, LevelMeter, MicrophoneOpenError
from transcription_flow.ui import wizard
from transcription_flow.ui.settings import NO_METER, MeterPoller
from transcription_flow.ui.wizard import STEPS, WizardFlow


class FakeService:
    def __init__(self):
        self.have = set()

    def names(self):
        return ["tiny.en", "base.en", "small.en"]

    def size(self, name):
        return {"tiny.en": 77_000_000, "base.en": 145_000_000, "small.en": 484_000_000}[name]

    def installed(self):
        return [n for n in self.names() if n in self.have]

    def smaller_than(self, name):
        i = self.names().index(name)
        return self.names()[i - 1] if i else None


class FakeApp:
    def __init__(self):
        self.config = Config()
        self.models = FakeService()
        self.model = None
        self.job = None
        self.mics = ["Headset mic", "Laptop mic"]
        self.updates = []
        self.retried = 0

    def mic_list(self):
        return self.mics

    def update_config(self, new, start_with_windows=None):
        self.updates.append(new)
        self.config = new

    def retry_model(self):
        self.retried += 1

    def use_model(self, name, language):
        self.used = (name, language)

    # used by the Tk window only (test_windows_ui.py)
    meter_factory = None
    opened = ()

    def cancel_download(self):
        self.job = None

    def open_mic_privacy(self):
        self.opened = (*self.opened, "privacy")

    def open_models_folder(self):
        self.opened = (*self.opened, "models")

    def hotkey_label(self):
        return "F9"


def job(name, percent=0, downloading=True):
    j = _Job(name, "en", downloading)
    j.percent = percent
    return j


@pytest.fixture
def flow():
    return WizardFlow(FakeApp())


def walk_to(flow, step):
    flow.index = STEPS.index(step)


def test_steps_in_order_with_back_and_next(flow):
    assert flow.step == "welcome" and not flow.can_back()
    flow.next()
    assert flow.step == "mic" and flow.can_back()
    flow.back()
    assert flow.step == "welcome"


def test_the_model_step_waits_for_a_ready_model(flow):
    walk_to(flow, "model")
    assert not flow.can_next()
    flow.next()
    assert flow.step == "model"  # Next does nothing yet
    flow.app.job = job("small.en")
    assert not flow.can_next()
    flow.app.model = "small.en"
    assert not flow.can_next()  # a job still running (a new pick)
    flow.app.job = None
    assert flow.can_next()
    flow.next()
    assert flow.step == "hotkey"


def test_done_is_last_and_has_no_back(flow):
    walk_to(flow, "done")
    assert not flow.can_next() and not flow.can_back()
    assert "F9" in flow.done_text() and "tray" in flow.done_text()


def test_mic_choices_keep_an_unplugged_saved_mic(flow):
    assert flow.mic_choices() == {"": "Windows default microphone", "Headset mic": "Headset mic",
                                  "Laptop mic": "Laptop mic"}
    flow.app.config = Config(mic="USB mic")
    assert flow.mic_choices()["USB mic"] == "USB mic (not connected)"


def test_choosing_a_mic_saves_it_once(flow):
    flow.choose_mic("Headset mic")
    flow.choose_mic("Headset mic")
    assert [c.mic for c in flow.app.updates] == ["Headset mic"]


@pytest.mark.parametrize("status", [
    LevelMeter.LISTENING, LevelMeter.HEARD, LevelMeter.QUIET, LevelMeter.BLOCKED])
def test_each_mic_status_has_a_text_and_only_blocked_names_the_privacy_switch(status):
    assert ("Privacy & security" in wizard.MIC_TEXT[status]) is (status == LevelMeter.BLOCKED)


def test_model_choices_show_sizes_default_and_downloaded(flow):
    flow.app.models.have = {"tiny.en"}
    choices = flow.model_choices()
    assert list(choices) == ["tiny.en", "base.en", "small.en"]
    assert choices["tiny.en"] == "tiny.en (77 MB, downloaded)"  # sizes from the service
    assert choices["small.en"] == "small.en (484 MB, recommended)"


def test_choosing_a_model_saves_it_with_a_language_it_knows(flow):
    flow.app.config = Config(model="small.en", language="en")
    flow.error = "old failure"
    flow.choose_model("base.en")
    assert flow.app.config.model == "base.en" and flow.app.config.language == "en"
    assert flow.app.used == ("base.en", "en") and flow.error is None


def test_choosing_the_configured_model_still_fetches_it(flow):
    """First run: small.en is already in the config but not downloaded."""
    flow.choose_model("small.en")
    assert flow.app.used == ("small.en", "en")


@pytest.mark.parametrize("state, text, percent, busy, failed", [
    ({"job": job("base.en", 42)}, "Downloading base.en: 42% of 145 MB.", 42, True, False),
    ({"job": job("base.en", 100)}, "Getting base.en ready...", None, True, False),
    ({"job": job("base.en", 0, downloading=False)}, "Getting base.en ready", None, True, False),
    ({"error": "The download failed: offline."}, "The download failed: offline. Check", None,
     False, True),
    ({"model": "small.en"}, "small.en is ready.", None, False, False),
    ({}, "Pick a model and press Download.", None, False, False),
])
def test_model_view(flow, state, text, percent, busy, failed):
    flow.app.job = state.get("job")
    flow.app.model = state.get("model")
    flow.error = state.get("error")
    view = flow.model_view()
    assert view.text.startswith(text)
    assert (view.percent, view.busy, view.failed) == (percent, busy, failed)


def test_a_running_job_wins_over_an_old_error(flow):
    flow.error = "The download failed."
    flow.app.job = job("base.en", 10)
    assert flow.model_view().busy and not flow.model_view().failed


def test_retry_and_smaller_model(flow):
    flow.error = "x"
    flow.retry()
    assert flow.app.retried == 1 and flow.error is None
    assert flow.smaller_model() == "base.en"  # from the configured small.en
    flow.use_smaller_model()
    assert flow.app.config.model == "base.en"
    flow.app.job = job("tiny.en")
    assert flow.smaller_model() is None  # from the model being fetched
    flow.use_smaller_model()
    assert flow.app.config.model == "base.en"


def test_choosing_a_hotkey_saves_it_once(flow):
    flow.choose_hotkey("f9")
    flow.choose_hotkey("f9")
    assert [c.hotkey for c in flow.app.updates] == ["f9"]


def test_texts_say_inspired_by_and_never_clone():
    texts = [wizard.WELCOME, *wizard.MIC_TEXT.values(), wizard.TITLE]
    assert "Inspired by Wispr Flow" in wizard.WELCOME
    assert not any("clone" in t.lower() for t in texts)


def test_try_text_names_the_hotkey(flow):
    flow.app.config = dataclasses.replace(flow.app.config, hotkey="f9")
    assert "hold F9" in flow.try_text()


# -- MeterPoller without a display (review-m3 R11) ----------------------------------

class FakeWidget:
    def __init__(self):
        self.after_calls, self.cancelled = [], []

    def after(self, ms, fn):
        self.after_calls.append((ms, fn))
        return f"after#{len(self.after_calls)}"

    def after_cancel(self, ident):
        self.cancelled.append(ident)


class StubMeter:
    def __init__(self, device, error=None):
        self.device, self.error, self.stopped = device, error, False

    def start(self):
        if self.error:
            raise self.error

    def stop(self):
        self.stopped = True


@pytest.mark.parametrize("error, message", [
    (MicrophoneOpenError(OPEN_FAILED_MESSAGE), OPEN_FAILED_MESSAGE),
    (OSError("PortAudio library not found"), NO_METER),
])
def test_a_meter_that_fails_to_open_says_why_and_never_ticks(error, message):
    widget, ticks = FakeWidget(), []
    poller = MeterPoller(widget, lambda device: StubMeter(device, error), ticks.append)
    assert poller.start("Headset mic") == message
    assert ticks == [] and widget.after_calls == []


def test_a_meter_ticks_until_stopped_and_one_runs_at_a_time():
    widget, ticks, meters = FakeWidget(), [], []
    poller = MeterPoller(widget, lambda d: meters.append(StubMeter(d)) or meters[-1], ticks.append)
    assert poller.start(None) is None
    assert ticks == [meters[0]] and widget.after_calls[0][0] == MeterPoller.METER_MS
    widget.after_calls[0][1]()  # the Tk loop runs the next tick
    assert ticks == [meters[0], meters[0]]
    poller.start("Headset mic")
    assert meters[0].stopped and widget.cancelled == ["after#2"]
    poller.stop()
    assert meters[1].stopped and widget.cancelled == ["after#2", "after#3"]
