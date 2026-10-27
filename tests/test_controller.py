import threading

import pytest

from helpers import flush_logs, tone, wait_for
from transcription_flow import controller as controller_mod
from transcription_flow.controller import Controller, State
from transcription_flow.output import OutputWarning
from transcription_flow.recorder import NoMicrophoneError, SilentMicrophoneError


class FakeRecorder:
    def __init__(self):
        self.clips = []  # audio returned by successive stop() calls
        self.start_error = None
        self.stop_error = None
        self.calls = []
        self.device = None
        self.is_open = False

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
    assert ctl.cancel("another key was pressed") is True
    assert ctl.state is State.IDLE
    ctl.release()
    assert rec.calls == ["start", "cancel"]
    assert ui == ["recording", "hidden"]
    assert eng.calls == 0 and out.typed == []


def test_other_key_when_idle_does_nothing(parts):
    ctl, rec, eng, out, ui, errors = parts
    assert ctl.cancel("another key was pressed") is False
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


def test_an_unchecked_hold_is_dropped_at_the_cap_not_typed(parts, timers):
    """review-m2 R3: with no way to see a missed release (Filter Keys repeat
    off, a suppressed key), 5 minutes of audio must not be typed into
    whatever has focus after the lock screen."""
    ctl, rec, eng, out, ui, errors = parts
    ctl.press(unchecked=True)
    timers.timers[-1].fire()
    assert ctl.state is State.IDLE and rec.calls == ["start", "cancel"]
    assert ui == ["recording", "hidden"] and errors == [controller_mod.CAP_DROPPED]
    ctl.release()  # the late key-up does nothing
    ctl.press()  # a checked hold after it types at the cap as before
    timers.timers[-1].fire()
    assert idle(ctl) and out.typed == ["hello"] and eng.calls == 1


def test_an_unchecked_hold_released_in_time_is_typed(parts, timers):
    ctl, rec, eng, out, ui, errors = parts
    ctl.press(unchecked=True)
    ctl.release()
    assert idle(ctl) and out.typed == ["hello"] and errors == []


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


def test_an_output_warning_is_reported_as_itself_not_as_a_failure(parts):
    """review-m2 N2: the text was pasted; only the clipboard restore failed."""
    ctl, rec, eng, out, ui, errors = parts

    def emit(text):
        out.typed.append(text)
        raise OutputWarning("The text was pasted, but part of your clipboard ...")

    out.emit = emit
    ctl.press()
    ctl.release()
    assert idle(ctl)
    assert out.typed == ["hello"]
    assert errors == ["The text was pasted, but part of your clipboard ..."]


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


def test_a_new_mic_reopens_a_warm_stream_on_it(parts):
    ctl, rec, *_ = parts
    rec.is_open = True
    ctl.set_microphone("Headset mic")
    assert rec.device == "Headset mic" and rec.calls == ["close", "open"]


def test_a_new_mic_while_recording_or_cold_waits_for_the_next_open(parts):
    ctl, rec, *_ = parts
    ctl.set_microphone("Headset mic")
    assert rec.device == "Headset mic" and rec.calls == []  # cold: opens on the next press
    rec.is_open = True
    ctl.press()
    ctl.set_microphone(None)
    assert rec.device is None and rec.calls == ["start"]  # not swapped mid-clip


def test_a_new_mic_during_a_warm_recording_reopens_when_it_ends(parts):
    """review-m3 F6: the change isn't lost until a restart."""
    ctl, rec, *_ = parts
    rec.is_open = True  # keep-warm stream
    ctl.press()
    ctl.set_microphone("Headset mic")
    assert rec.calls == ["start"]  # not swapped mid-clip
    ctl.release()
    assert idle(ctl)
    assert rec.calls == ["start", "stop", "close", "open"]
    ctl.press()
    ctl.release()
    assert idle(ctl)
    assert rec.calls[4:] == ["start", "stop"]  # once only


def test_a_new_mic_during_a_transcription_reopens_when_it_ends(parts):
    ctl, rec, eng, *_ = parts
    rec.is_open = True
    eng.gate.clear()
    ctl.press()
    ctl.release()
    assert ctl.state is State.TRANSCRIBING
    ctl.set_microphone("Headset mic")
    assert rec.calls == ["start", "stop"]
    eng.gate.set()
    assert idle(ctl)
    assert wait_for(lambda: rec.calls == ["start", "stop", "close", "open"])


def test_a_new_mic_during_a_cancelled_warm_recording_reopens(parts):
    ctl, rec, *_ = parts
    rec.is_open = True
    ctl.press()
    ctl.set_microphone("Headset mic")
    ctl.cancel("another key was pressed")
    assert rec.calls == ["start", "cancel", "close", "open"]


def test_a_new_mic_during_a_cold_recording_needs_no_reopen(parts):
    ctl, rec, *_ = parts
    ctl.press()
    ctl.set_microphone("Headset mic")
    ctl.release()
    assert idle(ctl)
    assert rec.calls == ["start", "stop"]  # the next press opens the new mic


def test_a_new_mic_that_fails_to_open_is_reported(parts):
    ctl, rec, eng, out, ui, errors = parts
    rec.is_open = True
    rec.start_error = NoMicrophoneError("no mic")
    ctl.set_microphone("Headset mic")
    assert errors == ["no mic"]


def test_a_new_transcriber_takes_the_next_clip(parts):
    ctl, rec, eng, out, ui, errors = parts
    newer = FakeTranscriber(["from the new model"])
    ctl.set_transcriber(newer)
    ctl.press()
    ctl.release()
    assert idle(ctl)
    assert out.typed == ["from the new model"] and eng.calls == 0 and newer.calls == 1


def test_an_output_warning_goes_to_the_notice_channel_when_there_is_one(timers):
    notices, errors = [], []

    class Warns:
        def emit(self, text):
            raise OutputWarning("pasted, clipboard partly lost")

    ctl = Controller(FakeRecorder(), FakeTranscriber(["hi"]), Warns(), on_error=errors.append,
                     on_notice=notices.append, timer=timers)
    ctl.start()
    try:
        ctl.press()
        ctl.release()
        assert idle(ctl)
        assert notices == ["pasted, clipboard partly lost"] and errors == []
    finally:
        ctl.shutdown(timeout=5)


def test_a_dropped_short_phrase_is_a_notice_not_an_error(timers):
    """QA B4: nothing typed, and the user hears why; the overlay hides."""
    from transcription_flow.hallucination import SHORT_PHRASE_HINT, ShortPhraseDropped

    notices, errors, ui = [], [], []
    eng, out = FakeTranscriber(), FakeOutput()
    eng.error = ShortPhraseDropped()
    ctl = Controller(FakeRecorder(), eng, out, ui=ui.append, on_error=errors.append,
                     on_notice=notices.append, timer=timers)
    ctl.start()
    try:
        ctl.press()
        ctl.release()
        assert idle(ctl)
        assert notices == [SHORT_PHRASE_HINT] and errors == [] and out.typed == []
        assert ui[-1] == "hidden"
    finally:
        ctl.shutdown(timeout=5)


# -- N2 end to end: Controller + PasteOutput (adopted from QA M3) -------------------

SECRET = "tomato-soup-secret-phrase"


def paste_run(timers, caplog, fail_paste=False, fail_restore=False, fail_set=None):
    from test_output import FakeClipboard, FakeKeys

    from transcription_flow.output import PasteOutput

    caplog.set_level("DEBUG")
    log, errors, notices = [], [], []
    clip = FakeClipboard(log)
    clip.fail_restore, clip.fail_set = fail_restore, fail_set
    out = PasteOutput(clipboard=clip, keys=FakeKeys(log, fail=fail_paste), sleep=lambda s: None,
                      settle=0)
    ctl = Controller(FakeRecorder(), FakeTranscriber([SECRET]), out, on_error=errors.append,
                     on_notice=notices.append, timer=timers)
    ctl.start()
    try:
        ctl.press()
        ctl.release()
        assert idle(ctl)
    finally:
        ctl.shutdown(timeout=5)
    flush_logs()
    assert SECRET not in caplog.text and SECRET not in " ".join(errors + notices)
    return errors, notices


def test_paste_ok_but_restore_failed_is_a_notice(timers, caplog):
    errors, notices = paste_run(timers, caplog, fail_restore=True)
    assert errors == [] and len(notices) == 1 and "pasted" in notices[0]


def test_paste_failed_and_restore_failed_reports_the_paste_error(timers, caplog):
    errors, notices = paste_run(timers, caplog, fail_paste=True, fail_restore=True)
    assert notices == [] and errors == [
        "Dictation failed: RuntimeError. See the log for details."]


def test_set_text_failed_part_way_and_restore_failed_reports_the_clipboard(timers, caplog):
    errors, notices = paste_run(timers, caplog, fail_set="part way", fail_restore=True)
    assert notices == [] and len(errors) == 1 and "ClipboardError" in errors[0]


def test_paste_with_nothing_failing_reports_nothing(timers, caplog):
    assert paste_run(timers, caplog) == ([], [])
