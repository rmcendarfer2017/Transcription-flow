"""Microphone capture: the stream opens on hotkey press and closes on release.

Opening on press keeps the Windows "microphone in use" icon off and keeps
Bluetooth headsets in high-quality mode while idle. ``keep_warm=True`` keeps
the stream open instead and adds a short pre-roll so the first syllable is
not lost.
"""

import collections
import logging
import threading
import time
import weakref

import numpy as np

log = logging.getLogger(__name__)

TARGET_RATE = 16000
NUM_LEVELS = 6
LEVEL_GAIN = 12.0  # RMS to a 0..1 meter level: normal speech fills most of it
# Windows' stand-ins for "the default device"; we offer our own default entry.
MAPPER_PREFIXES = ("Microsoft Sound Mapper", "Primary Sound Capture Driver")


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


# PortAudio builds its device list (and picks the Windows default) once,
# when it starts. Restarting it is the only way to see a microphone that
# was plugged in or out since (review-m3 F3), and a restart would break any
# open stream, so it happens only while none of ours is open. The lock
# keeps a restart from running between another thread's lookup and open.
_PA_LOCK = threading.RLock()
_open_streams = weakref.WeakKeyDictionary()  # the sounddevice module -> streams we have open


def _rescan(sd) -> None:
    """Restart PortAudio so its device list is current. _PA_LOCK held."""
    if _open_streams.get(sd) or not hasattr(sd, "_terminate"):
        return
    try:
        if getattr(sd, "_initialized", 1):
            sd._terminate()
        sd._initialize()
    except Exception as exc:  # PortAudioError: keep the old list
        log.warning("could not refresh the device list: %s", type(exc).__name__)


def _sounddevice(sd):
    if sd is None:
        import sounddevice

        sd = sounddevice
    return sd


def _inputs(sd) -> list:
    """(index, name) of each input device on the default host API, by
    name once. On Windows every device is listed once per host API (MME,
    DirectSound, WASAPI, ...), so without the filter each mic shows up 3-4 times."""
    try:
        devices = list(sd.query_devices())
    except Exception as exc:  # PortAudioError: no audio system at all
        log.warning("could not list input devices: %s", type(exc).__name__)
        return []
    try:
        hostapi = sd.default.hostapi
    except Exception:  # an old sounddevice, or no default host API
        hostapi = None
    found, seen = [], set()
    for i, d in enumerate(devices):
        name = d.get("name") or ""
        if (int(d.get("max_input_channels", 0)) < 1 or name in seen or not name
                or name.startswith(MAPPER_PREFIXES)
                or (hostapi is not None and d.get("hostapi") != hostapi)):
            continue
        seen.add(name)
        found.append((d.get("index", i), name))
    return found


def input_devices(sd=None) -> list:
    """Names for the microphone dropdown (the Windows default is offered
    separately), as plugged in now."""
    sd = _sounddevice(sd)
    with _PA_LOCK:
        _rescan(sd)
        return [name for _i, name in _inputs(sd)]


def find_input(sd, name):
    """The device index for ``name``, or None (the Windows default). A
    microphone that isn't plugged in falls back to the default."""
    if not name:
        return None
    for index, found in _inputs(sd):
        if found == name:
            return index
    log.warning("the chosen microphone is not connected; using the Windows default")
    return None


def input_rate(sd, device) -> int:
    """The device's default sample rate; NoMicrophoneError if it has no input."""
    try:
        info = sd.query_devices(device, "input")
    except Exception as exc:  # PortAudioError or ValueError: no input device
        log.warning("no input device: %s", type(exc).__name__)
        raise NoMicrophoneError(NO_MIC_MESSAGE) from None
    if not info or int(info.get("max_input_channels", 0)) < 1:
        raise NoMicrophoneError(NO_MIC_MESSAGE)
    return int(info.get("default_samplerate") or TARGET_RATE)


def open_input(sd, device, callback):
    """Start a mono float32 input stream at 16 kHz, else at the device's own
    rate; returns (stream, rate). Raises NoMicrophoneError or MicrophoneOpenError."""
    default_rate = input_rate(sd, device)
    last = None
    for rate in [TARGET_RATE] + ([default_rate] if default_rate != TARGET_RATE else []):
        try:
            stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                                    device=device, callback=callback)
            stream.start()
        except Exception as exc:  # PortAudioError: rate or format rejected
            last = exc
            log.info("input stream at %d Hz failed: %s", rate, type(exc).__name__)
            continue
        log.info("microphone open at %d Hz", rate)
        _open_streams[sd] = _open_streams.get(sd, 0) + 1
        return stream, rate
    log.warning("could not open microphone: %r", last)
    raise MicrophoneOpenError(OPEN_FAILED_MESSAGE)


def check_input(sd, name) -> None:
    """Raise NoMicrophoneError if ``name`` (None: the default) and the
    Windows default both have no input."""
    with _PA_LOCK:
        _rescan(sd)
        input_rate(sd, find_input(sd, name))


def open_named(sd, name, callback):
    """open_input for a microphone by name (None: the Windows default), on
    a fresh device list. A named one that fails to open (unplugged since
    the list was read, or busy) is tried once more as the default."""
    with _PA_LOCK:
        _rescan(sd)
        index = find_input(sd, name)
        try:
            return open_input(sd, index, callback)
        except MicrophoneError:
            if index is None:
                raise
            log.warning("the chosen microphone could not be opened; using the Windows default")
            return open_input(sd, None, callback)


def _mono(indata) -> np.ndarray:
    if indata.shape[1] == 1:
        return indata[:, 0].copy()
    return indata.mean(axis=1, dtype=np.float32)


def _level(chunk) -> float:
    return min(float(np.sqrt(np.mean(np.square(chunk)))) * LEVEL_GAIN, 1.0) if len(chunk) else 0.0


def _close_stream(sd, stream) -> None:
    with _PA_LOCK:
        _open_streams[sd] = max(_open_streams.get(sd, 0) - 1, 0)
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            log.warning("closing microphone failed: %r", exc)


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
        """``device``: a name from input_devices(), or None for the Windows
        default. It is looked up at each open, since indexes change when
        devices come and go."""
        self._sd = sd
        self.device = device
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
        self._sd = _sounddevice(self._sd)
        return self._sd

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    def check(self) -> None:
        """Raise NoMicrophoneError if there is no input device."""
        check_input(self.sd, self.device)

    def open(self) -> None:
        if self._stream is not None:
            return
        self._stream, self._rate = open_named(self.sd, self.device, self._callback)
        self._preroll.clear()
        self._preroll_samples = 0

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            _close_stream(self.sd, stream)

    def close(self) -> None:
        with self._lock:
            self._armed = False
        self._close()

    # -- capture ---------------------------------------------------------
    def _callback(self, indata, frames, time_info, status):
        # Runs on the audio thread. The controller's cap timer bounds the length.
        chunk = _mono(indata)
        level = _level(chunk) if self._armed else 0.0  # unlocked read: at worst one is skipped
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


class LevelMeter:
    """The setup and settings windows' level test. It opens its own short
    stream, so dictation's open-on-press stream is never touched.

    ``status()``: LISTENING until the user is heard (HEARD), QUIET when the
    mic sends sound too faint after a while, and BLOCKED when it has sent
    only exact zeros for BLOCKED_AFTER seconds: what Windows' microphone
    privacy switch gives desktop apps.
    """

    LISTENING, HEARD, QUIET, BLOCKED = "listening", "heard", "quiet", "blocked"
    HEARD_LEVEL = 0.15
    BLOCKED_AFTER = 2.0
    QUIET_AFTER = 4.0

    def __init__(self, sd=None, device=None, clock=time.monotonic):
        self._sd = sd
        self.device = device
        self._clock = clock
        self._lock = threading.Lock()
        self._stream = None
        self._reset()

    def _reset(self) -> None:
        self._level = self._peak = 0.0
        self._sound = False
        self._started = self._clock()

    def start(self) -> None:
        """Raises NoMicrophoneError or MicrophoneOpenError."""
        self.stop()
        sd = self._sd = _sounddevice(self._sd)
        with self._lock:
            self._reset()
        self._stream, _rate = open_named(sd, self.device, self._callback)

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            _close_stream(self._sd, stream)

    def _callback(self, indata, frames, time_info, status):
        chunk = _mono(indata)
        level = _level(chunk)
        with self._lock:
            self._level = level
            self._peak = max(self._peak, level)
            self._sound = self._sound or bool(np.any(chunk))

    def level(self) -> float:
        with self._lock:
            return self._level

    def status(self) -> str:
        with self._lock:
            peak, sound = self._peak, self._sound
        elapsed = self._clock() - self._started
        if peak >= self.HEARD_LEVEL:
            return self.HEARD
        if not sound:
            return self.BLOCKED if elapsed >= self.BLOCKED_AFTER else self.LISTENING
        return self.QUIET if elapsed >= self.QUIET_AFTER else self.LISTENING
