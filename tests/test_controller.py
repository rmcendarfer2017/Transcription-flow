import threading

import pytest

from helpers import flush_logs, tone, wait_for
from transcription_flow.controller import Controller, State
from transcription_flow.recorder import NoMicrophoneError, SilentMicrophoneError


class FakeRecorder:
    def __init__(self):
        self.clips = []  # audio returned by successive stop() calls
        self.start_error = None
        self.stop_error = None
        self.calls = []

    def start(self):
        self.calls.append("start")
        if self.start_error:
            raise self.start_error

    def stop(self):
        self.calls.append("stop")
        if self.stop_error:
            raise self.stop_error
        return self.clips.pop(0) if self.clips else tone(1.0)

    def cancel(self):
        self.calls.append("cancel")

    def close(self):
        self.calls.append("close")

    def open(self):
        self.calls.append("open")
        if self.start_error:
            raise self.start_error


class FakeTranscriber:
    def __init__(self, texts=None, block=False):
        self.texts = list(texts or [])
        self.gate = threading.Event()
        if not block:
            self.gate.set()
        self.active = 0
        self.max_active = 0
        self.threads = set()
        self.calls = 0
        self.error = None
        self._lock = threading.Lock()

    def transcribe(self, audio):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.threads.add(threading.current_thread().name)
        try:
            assert self.gate.wait(5), "test gate never opened"
            if self.error:
                raise self.error
            return self.texts.pop(0) if self.texts else "hello"
        finally:
            with self._lock:
                self.active -= 1


class FakeOutput:
    def __init__(self):
        self.typed = []

    def emit(self, text):
        self.typed.append(text)


@pytest.fixture
def parts(timers):
    rec, eng, out, ui, errors = FakeRecorder(), FakeTranscriber(), FakeOutput(), [], []
    ctl = Controller(rec, eng, out, ui=ui.append, on_error=errors.append, timer=timers)
    ctl.start()
    yield ctl, rec, eng, out, ui, errors
    eng.gate.set()
    ctl.shutdown(timeout=5)


def idle(ctl):
    return wait_for(lambda: ctl.state is State.IDLE)


def test_press_release_types_the_text(parts):
    ctl, rec, eng, out, ui, errors = parts
    eng.texts = ["Hello there."]
    ctl.press()
    assert ctl.state is State.RECORDING
    ctl.release()
    assert idle(ctl)
    assert out.typed == ["Hello there."]
    assert ui == ["recording", "transcribing", "hidden"]
    assert errors == []


def test_press_during_transcription_is_ignored_and_overlay_stays(parts, timers):
    ctl, rec, eng, out, ui, errors = parts
    eng.gate.clear()
    eng.texts = ["first", "second"]
    ctl.press()
    ctl.release()
    assert wait_for(lambda: eng.active == 1)
    ctl.press()  # H4: used to start a second recording over the first's buffer
    ctl.release()
    assert ctl.state is State.TRANSCRIBING
    assert rec.calls == ["start", "stop"]
    assert ui == ["recording", "transcribing"]  # no early "hidden"
    eng.gate.set()
    assert idle(ctl)
    assert out.typed == ["first"]
    assert ui == ["recording", "transcribing", "hidden"]


def test_one_worker_transcribes_clips_in_order(parts):
    ctl, rec, eng, out, ui, errors = parts
    eng.texts = [f"clip {i}" for i in range(5)]
    for _ in range(5):
        ctl.press()
        ctl.release()
        assert idle(ctl)
    assert out.typed == [f"clip {i}" for i in range(5)]
    assert eng.max_active == 1 and eng.threads == {"transcriber"}


def test_auto_repeat_press_while_recording_is_ignored(parts):
    ctl, rec, *_ = parts
    ctl.press()
    ctl.press()
    ctl.press()
    assert rec.calls == ["start"]


def test_release_without_press_does_nothing(parts):
    ctl, rec, eng, out, ui, errors = parts
    ctl.release()
    assert rec.calls == [] and ui == []


def test_other_key_cancels_the_recording(parts):
    ctl, rec, eng, out, ui, errors = parts
    ctl.press()
    ctl.cancel("another key was pressed")
    assert ctl.state is State.IDLE
    ctl.release()
    assert rec.calls == ["start", "cancel"]
    assert ui == ["recording", "hidden"]
    assert eng.calls == 0 and out.typed == []


def test_other_key_when_idle_does_nothing(parts):
    ctl, rec, eng, out, ui, errors = parts
    ctl.cancel("another key was pressed")
    assert rec.calls == [] and ui == []


def test_too_short_clip_is_dropped(parts):
    ctl, rec, eng, out, ui, errors = parts
    rec.clips = [tone(0.2)]
    ctl.press()
    ctl.release()
    assert ctl.state is State.IDLE
    assert eng.calls == 0 and ui == ["recording", "hidden"]


def test_clip_at_the_minimum_is_kept(parts):
    ctl, rec, eng, out, ui, errors = parts
    rec.clips = [tone(0.3)]
    ctl.press()
    ctl.release()
    assert idle(ctl) and eng.calls == 1


def test_cap_timer_stops_and_transcribes(parts, timers):
    ctl, rec, eng, out, ui, errors = parts
    ctl.press()
    assert timers.timers[-1].seconds == 300.0
    timers.timers[-1].fire()
    assert idle(ctl)
    assert out.typed == ["hello"]
    ctl.release()  # the key comes up later: nothing more happens
    assert rec.calls == ["start", "stop"]


def test_release_cancels_the_cap_timer(parts, timers):
    ctl, *_ = parts
    ctl.press()
    ctl.release()
    assert timers.timers[-1].cancelled


def test_cancel_cancels_the_cap_timer(parts, timers):
    ctl, *_ = parts
    ctl.press()
    ctl.cancel("another key was pressed")
    assert timers.timers[-1].cancelled


def test_mic_error_on_press_reports_and_stays_idle(parts):
    ctl, rec, eng, out, ui, errors = parts
    rec.start_error = NoMicrophoneError("No microphone found.")
    ctl.press()
    assert ctl.state is State.IDLE
    assert errors == ["No microphone found."] and ui == []
    rec.start_error = None
    ctl.press()  # recovers once a mic is plugged in
    assert ctl.state is State.RECORDING


def test_silent_mic_on_release_reports(parts):
    ctl, rec, eng, out, ui, errors = parts
    rec.stop_error = SilentMicrophoneError("blocked")
    ctl.press()
    ctl.release()
    assert ctl.state is State.IDLE
    assert errors == ["blocked"] and ui == ["recording", "hidden"] and eng.calls == 0


def test_transcriber_failure_reports_and_recovers(parts):
    ctl, rec, eng, out, ui, errors = parts
    eng.error = RuntimeError("cuBLAS missing")
    ctl.press()
    ctl.release()
    assert idle(ctl)
    assert len(errors) == 1 and "RuntimeError" in errors[0]
    eng.error = None
    ctl.press()
    ctl.release()
    assert idle(ctl) and out.typed == ["hello"]


def test_empty_text_types_nothing(parts):
    ctl, rec, eng, out, ui, errors = parts
    eng.texts = [""]
    ctl.press()
    ctl.release()
    assert idle(ctl)
    assert out.typed == [] and ui[-1] == "hidden"


def test_worker_receives_the_recorder_audio(parts):
    ctl, rec, eng, out, ui, errors = parts
    seen = []
    eng.transcribe = lambda audio: seen.append(audio) or "x"
    clip = tone(0.5)
    rec.clips = [clip]
    ctl.press()
    ctl.release()
    assert idle(ctl)
    assert seen[0] is clip


def test_shutdown_while_recording_cancels_and_closes(timers):
    rec = FakeRecorder()
    ctl = Controller(rec, FakeTranscriber(), FakeOutput(), timer=timers)
    ctl.start()
    ctl.press()
    ctl.shutdown(timeout=5)
    assert rec.calls == ["start", "cancel", "close"]
    assert ctl.state is State.IDLE


def test_default_timer_fires_the_cap():
    rec = FakeRecorder()
    ctl = Controller(rec, FakeTranscriber(), FakeOutput(), max_seconds=0.05)
    ctl.start()
    try:
        ctl.press()
        assert wait_for(lambda: "stop" in rec.calls, timeout=2)
    finally:
        ctl.shutdown(timeout=5)



def test_stale_cap_callback_does_not_end_the_next_recording(parts, timers):
    """Timer.cancel() can't stop a callback already waiting for the lock (review F8)."""
    ctl, rec, eng, out, ui, errors = parts
    ctl.press()
    ctl.release()
    assert idle(ctl)
    ctl.press()
    timers.timers[0].callback()  # the first recording's cap, arriving late
    assert ctl.state is State.RECORDING
    timers.timers[1].fire()  # the current recording's own cap still works
    assert idle(ctl) and len(out.typed) == 2


class RaisingOutput:
    def emit(self, text):
        try:
            raise ValueError(f"inner {text}")
        except ValueError:
            raise RuntimeError(f"could not type {text!r}")  # chained: both carry the text


def test_transcript_never_reaches_the_log_even_in_an_error(timers, caplog, tmp_path):
    """Canary (review F7): an output backend that raises with the text in its message."""
    import logging

    from transcription_flow import logsetup

    log_file = logsetup.setup(tmp_path / "logs", level=logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    errors = []
    ctl = Controller(FakeRecorder(), FakeTranscriber(["CANARY-TEXT"]), RaisingOutput(),
                     on_error=errors.append, timer=timers)
    ctl.start()
    try:
        ctl.press()
        ctl.release()
        assert idle(ctl) and errors
    finally:
        ctl.shutdown(timeout=5)
    flush_logs()
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)  # it was logged
    assert "CANARY" not in caplog.text
    assert "CANARY" not in log_file.read_text("utf-8")
    assert not any("CANARY" in e for e in errors)


# -- M2: pause, output swap, keep-warm toggle --------------------------------

def test_press_while_paused_is_ignored(parts):
    ctl, rec, eng, out, ui, errors = parts
    ctl.set_paused(True)
    ctl.press()
    ctl.release()
    assert rec.calls == [] and ui == [] and ctl.paused
    ctl.set_paused(False)
    ctl.press()
    assert ctl.state is State.RECORDING


def test_pausing_cancels_a_recording(parts):
    ctl, rec, eng, out, ui, errors = parts
    ctl.press()
    ctl.set_paused(True)
    ctl.release()
    assert rec.calls == ["start", "cancel"] and ui == ["recording", "hidden"]
    assert eng.calls == 0


def test_set_output_is_used_for_the_next_clip(parts):
    ctl, rec, eng, out, ui, errors = parts
    other = FakeOutput()
    ctl.set_output(other)
    ctl.press()
    ctl.release()
    assert idle(ctl)
    assert other.typed == ["hello"] and out.typed == []


def test_keep_warm_on_opens_the_mic_when_idle(parts):
    ctl, rec, *_ = parts
    ctl.set_keep_warm(True)
    assert rec.keep_warm is True and rec.calls == ["open"]
    ctl.set_keep_warm(False)
    assert rec.keep_warm is False and rec.calls == ["open", "close"]


def test_keep_warm_change_while_recording_waits(parts):
    ctl, rec, *_ = parts
    ctl.press()
    ctl.set_keep_warm(False)
    assert rec.keep_warm is False and rec.calls == ["start"]  # not closed mid-clip


def test_keep_warm_open_failure_is_reported(parts):
    ctl, rec, eng, out, ui, errors = parts
    rec.start_error = NoMicrophoneError("no mic")
    ctl.set_keep_warm(True)
    assert errors == ["no mic"] and ctl.state is State.IDLE
