"""The dictation state machine: Idle -> Recording -> Transcribing -> Idle.

One worker thread transcribes one clip at a time, from a copy of the audio
taken on release, so overlapping dictations can no longer race (review H4).
A press while a clip is still being transcribed is ignored. Every overlay
change goes through ``ui(state)``, which the UI thread drains from a queue.
"""

import enum
import logging
import queue
import threading
import traceback

from .recorder import TARGET_RATE, MicrophoneError

log = logging.getLogger(__name__)

MIN_SECONDS = 0.3
MAX_SECONDS = 300.0


class State(enum.Enum):
    """Each value is the overlay state sent to ui()."""

    IDLE = "hidden"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


def _thread_timer(seconds, callback):
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


class Controller:
    def __init__(self, recorder, transcriber, output, ui=None, on_error=None,
                 timer=_thread_timer, min_seconds=MIN_SECONDS, max_seconds=MAX_SECONDS):
        self._recorder = recorder
        self._transcriber = transcriber
        self._output = output
        self._ui = ui or (lambda state: None)
        self._on_error = on_error or (lambda message: None)
        self._timer = timer
        self._min_seconds = min_seconds
        self._max_seconds = max_seconds
        self._lock = threading.Lock()
        self._state = State.IDLE
        self._paused = False
        self._cap_timer = None
        self._recording_id = 0  # the cap timer's token (review F8)
        self._jobs = queue.Queue()
        self._worker = None

    @property
    def state(self) -> State:
        return self._state

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._worker is None:
            self._worker = threading.Thread(target=self._work, name="transcriber", daemon=True)
            self._worker.start()

    def shutdown(self, timeout=None) -> None:
        with self._lock:
            self._cancel()
        self._jobs.put(None)
        if self._worker is not None:
            self._worker.join(timeout)
            self._worker = None
        self._recorder.close()

    # -- hotkey events ---------------------------------------------------
    def press(self) -> None:
        with self._lock:
            if self._state is State.TRANSCRIBING:
                log.info("press ignored: still transcribing")
            if self._state is not State.IDLE or self._paused:
                return
            try:
                self._recorder.start()
            except MicrophoneError as err:
                self._error(err)
                return
            self._recording_id += 1
            rid = self._recording_id
            self._cap_timer = self._timer(self._max_seconds, lambda: self._cap_hit(rid))
            self._set(State.RECORDING)

    def release(self) -> None:
        with self._lock:
            if self._state is State.RECORDING:
                self._finish("release")

    def cancel(self, reason: str) -> bool:
        """Drop the recording: another key went down while the hotkey was held
        (a shortcut, not dictation), or the key-up was missed. True if a
        recording was dropped."""
        with self._lock:
            if self._cancel():
                log.info("recording cancelled: %s", reason)
                return True
            return False

    # -- settings (any thread) -------------------------------------------
    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            if paused and self._cancel():
                log.info("recording cancelled: dictation paused")

    def set_output(self, output) -> None:
        with self._lock:  # the worker reads it once per clip
            self._output = output

    def set_keep_warm(self, keep_warm: bool) -> None:
        """Keep the microphone open between dictations, or not. A change
        while recording takes effect when that recording ends."""
        with self._lock:
            self._recorder.keep_warm = keep_warm
            if self._state is not State.IDLE:
                return
            if keep_warm:
                try:
                    self._recorder.open()
                except MicrophoneError as err:
                    self._error(err)
            else:
                self._recorder.close()

    def _cap_hit(self, rid: int) -> None:
        # Timer.cancel() can't stop a callback already waiting for the lock,
        # so a stale cap must not end a newer recording.
        with self._lock:
            if self._state is State.RECORDING and rid == self._recording_id:
                log.info("recording reached the %.0f s cap", self._max_seconds)
                self._finish("cap")

    # -- internals (lock held) -------------------------------------------
    def _set(self, state: State) -> None:
        self._state = state
        self._ui(state.value)

    def _cancel(self) -> bool:
        if self._state is not State.RECORDING:
            return False
        self._end_recording()
        self._recorder.cancel()
        self._set(State.IDLE)
        return True

    def _end_recording(self) -> None:
        if self._cap_timer is not None:
            self._cap_timer.cancel()
            self._cap_timer = None

    def _error(self, err: Exception) -> None:
        log.warning("%s: %s", type(err).__name__, err)
        self._on_error(str(err))

    def _finish(self, reason: str) -> None:
        self._end_recording()
        try:
            audio = self._recorder.stop()
        except MicrophoneError as err:
            self._set(State.IDLE)
            self._error(err)
            return
        seconds = len(audio) / TARGET_RATE
        if seconds < self._min_seconds:
            log.info("clip too short (%.2f s), ignored", seconds)
            self._set(State.IDLE)
            return
        log.info("clip of %.2f s queued (%s)", seconds, reason)
        self._set(State.TRANSCRIBING)
        self._jobs.put(audio)

    # -- worker ----------------------------------------------------------
    def _work(self) -> None:
        while True:
            audio = self._jobs.get()
            if audio is None:
                return
            try:
                text = self._transcriber.transcribe(audio)
                if text:
                    log.info("transcribed %d characters", len(text))
                    with self._lock:
                        output = self._output
                    output.emit(text)
                else:
                    log.info("no speech detected")
            except Exception as err:
                # Type and frames only: an exception's message (or a chained
                # one's) may hold the transcript (review F7).
                log.error("transcription or typing failed (%s)\n%s", type(err).__name__,
                          "".join(traceback.format_tb(err.__traceback__)).rstrip())
                self._on_error(f"Dictation failed: {type(err).__name__}. See the log for details.")
            finally:
                with self._lock:
                    self._set(State.IDLE)
