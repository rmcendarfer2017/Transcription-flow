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

from .recorder import TARGET_RATE, MicrophoneError

log = logging.getLogger(__name__)

MIN_SECONDS = 0.3
MAX_SECONDS = 300.0

# Overlay states sent to ui().
RECORDING = "recording"
TRANSCRIBING = "transcribing"
HIDDEN = "hidden"


class State(enum.Enum):
    IDLE = "idle"
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
        self._cap_timer = None
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
            if self._state is State.RECORDING:
                self._end_recording()
                self._recorder.cancel()
                self._set(State.IDLE, HIDDEN)
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
            if self._state is not State.IDLE:
                return
            try:
                self._recorder.start()
            except MicrophoneError as err:
                self._error(err)
                return
            self._cap_timer = self._timer(self._max_seconds, self._cap_hit)
            self._set(State.RECORDING, RECORDING)

    def release(self) -> None:
        with self._lock:
            if self._state is State.RECORDING:
                self._finish("release")

    def other_key(self) -> None:
        """Another key went down while the hotkey was held: a shortcut, not dictation."""
        with self._lock:
            if self._state is State.RECORDING:
                self._end_recording()
                self._recorder.cancel()
                log.info("recording cancelled: another key was pressed")
                self._set(State.IDLE, HIDDEN)

    def _cap_hit(self) -> None:
        with self._lock:
            if self._state is State.RECORDING:
                log.info("recording reached the %.0f s cap", self._max_seconds)
                self._finish("cap")

    # -- internals (lock held) -------------------------------------------
    def _set(self, state: State, overlay: str) -> None:
        self._state = state
        self._ui(overlay)

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
            self._set(State.IDLE, HIDDEN)
            self._error(err)
            return
        seconds = len(audio) / TARGET_RATE
        if seconds < self._min_seconds:
            log.info("clip too short (%.2f s), ignored", seconds)
            self._set(State.IDLE, HIDDEN)
            return
        log.info("clip of %.2f s queued (%s)", seconds, reason)
        self._set(State.TRANSCRIBING, TRANSCRIBING)
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
                    self._output.emit(text)
                else:
                    log.info("no speech detected")
            except Exception as err:
                log.exception("transcription or typing failed")
                self._on_error(f"Dictation failed: {type(err).__name__}. See the log for details.")
            finally:
                with self._lock:
                    self._set(State.IDLE, HIDDEN)
