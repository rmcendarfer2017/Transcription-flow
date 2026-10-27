"""Microphone capture: the stream opens on hotkey press and closes on release.

Opening on press keeps the Windows "microphone in use" icon off and keeps
Bluetooth headsets in high-quality mode while idle. ``keep_warm=True`` keeps
the stream open instead and adds a short pre-roll so the first syllable is
not lost.
"""

import collections
import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

TARGET_RATE = 16000
NUM_LEVELS = 6


class MicrophoneError(Exception):
    """A microphone problem the user can fix; ``str(err)`` is the message."""


class NoMicrophoneError(MicrophoneError):
    pass


class MicrophoneOpenError(MicrophoneError):
    pass


class SilentMicrophoneError(MicrophoneError):
    pass


NO_MIC_MESSAGE = "No microphone found. Plug one in or pick one in Windows sound settings."
OPEN_FAILED_MESSAGE = "The microphone could not be opened. Another app may be using it."
SILENT_MESSAGE = (
    "The microphone sent only silence. Windows may be blocking it: "
    "Settings > Privacy & security > Microphone > Let desktop apps access your microphone."
)


def resample(audio: np.ndarray, rate: int, target: int = TARGET_RATE) -> np.ndarray:
    """Linear-interpolation resample of mono float32 audio."""
    if rate == target or len(audio) == 0:
        return audio.astype(np.float32, copy=True)
    n = int(round(len(audio) * target / rate))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    x_new = np.linspace(0, len(audio) - 1, n)
    return np.interp(x_new, np.arange(len(audio)), audio).astype(np.float32)


class Recorder:
    def __init__(self, sd=None, device=None, keep_warm=False, preroll_seconds=0.3):
        self._sd = sd
        self._device = device
        self.keep_warm = keep_warm
        self._preroll_seconds = preroll_seconds
        self._lock = threading.Lock()
        self._stream = None
        self._rate = TARGET_RATE
        self._armed = False
        self._chunks = []
        self._preroll = collections.deque()
        self._preroll_samples = 0
        self._levels = collections.deque([0.0] * NUM_LEVELS, maxlen=NUM_LEVELS)

    # -- stream ----------------------------------------------------------
    @property
    def sd(self):
        if self._sd is None:
            import sounddevice

            self._sd = sounddevice
        return self._sd

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    def check(self) -> None:
        """Raise NoMicrophoneError if there is no input device."""
        self._default_rate()

    def _default_rate(self) -> int:
        try:
            info = self.sd.query_devices(self._device, "input")
        except Exception as exc:  # PortAudioError or ValueError: no input device
            log.warning("no input device: %s", type(exc).__name__)
            raise NoMicrophoneError(NO_MIC_MESSAGE) from None
        if not info or int(info.get("max_input_channels", 0)) < 1:
            raise NoMicrophoneError(NO_MIC_MESSAGE)
        return int(info.get("default_samplerate") or TARGET_RATE)

    def open(self) -> None:
        if self._stream is not None:
            return
        default_rate = self._default_rate()
        rates = [TARGET_RATE] + ([default_rate] if default_rate != TARGET_RATE else [])
        last = None
        for rate in rates:
            try:
                stream = self.sd.InputStream(
                    samplerate=rate, channels=1, dtype="float32",
                    device=self._device, callback=self._callback,
                )
                stream.start()
            except Exception as exc:  # PortAudioError: rate or format rejected
                last = exc
                log.info("input stream at %d Hz failed: %s", rate, type(exc).__name__)
                continue
            self._rate = rate
            self._stream = stream
            self._preroll.clear()
            self._preroll_samples = 0
            log.info("microphone open at %d Hz", rate)
            return
        log.warning("could not open microphone: %r", last)
        raise MicrophoneOpenError(OPEN_FAILED_MESSAGE)

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            log.warning("closing microphone failed: %r", exc)

    def close(self) -> None:
        with self._lock:
            self._armed = False
        self._close()

    # -- capture ---------------------------------------------------------
    def _callback(self, indata, frames, time_info, status):
        # Runs on the audio thread. The controller's cap timer bounds the length.
        if indata.shape[1] == 1:
            chunk = indata[:, 0].copy()
        else:
            chunk = indata.mean(axis=1, dtype=np.float32)
        level = min(float(np.sqrt(np.mean(np.square(chunk)))) * 12.0, 1.0) if len(chunk) else 0.0
        with self._lock:
            if self._armed:
                self._chunks.append(chunk)
                self._levels.append(level)
            elif self.keep_warm:
                self._preroll.append(chunk)
                self._preroll_samples += len(chunk)
                limit = int(self._preroll_seconds * self._rate)
                while self._preroll and self._preroll_samples - len(self._preroll[0]) >= limit:
                    self._preroll_samples -= len(self._preroll.popleft())

    def start(self) -> None:
        """Open the stream (if needed) and start keeping audio."""
        self.open()
        with self._lock:
            self._chunks = list(self._preroll) if self.keep_warm else []
            self._preroll.clear()
            self._preroll_samples = 0
            self._levels.extend([0.0] * NUM_LEVELS)
            self._armed = True

    def _take(self) -> np.ndarray:
        with self._lock:
            self._armed = False
            chunks, self._chunks = self._chunks, []
            rate = self._rate
        if not self.keep_warm:
            self._close()
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        return resample(audio, rate)

    def stop(self) -> np.ndarray:
        """Stop keeping audio; returns a fresh 16 kHz mono float32 array."""
        audio = self._take()
        if len(audio) and not np.any(audio):
            raise SilentMicrophoneError(SILENT_MESSAGE)
        return audio

    def cancel(self) -> None:
        self._take()

    def levels(self) -> list:
        with self._lock:
            return list(self._levels)
