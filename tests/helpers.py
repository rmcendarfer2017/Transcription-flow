"""Test fakes and helpers (no real audio devices, hotkeys or network)."""

import time

import numpy as np


class PortAudioError(Exception):
    pass


class FakeStream:
    def __init__(self, sd, samplerate, channels, dtype, device, callback):
        self.sd = sd
        self.samplerate = samplerate
        self.channels = channels
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self):
        if self.sd.accepted_rates is not None and self.samplerate not in self.sd.accepted_rates:
            raise PortAudioError(f"Invalid sample rate {self.samplerate}")
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def feed(self, samples, channels=1):
        data = np.asarray(samples, dtype=np.float32).reshape(-1, channels)
        self.callback(data, len(data), None, None)


class FakeSoundDevice:
    """Stand-in for the sounddevice module."""

    PortAudioError = PortAudioError

    def __init__(self, device_info=None, accepted_rates=None, default_rate=16000):
        self.device_info = device_info if device_info is not None else {
            "name": "Fake mic", "max_input_channels": 1, "default_samplerate": default_rate}
        self.accepted_rates = accepted_rates
        self.streams = []

    def query_devices(self, device=None, kind=None):
        if self.device_info is False:
            raise PortAudioError("Error querying device -1")
        return self.device_info

    def InputStream(self, samplerate, channels, dtype, device, callback):  # noqa: N802
        stream = FakeStream(self, samplerate, channels, dtype, device, callback)
        self.streams.append(stream)
        return stream

    @property
    def stream(self):
        return self.streams[-1]


class FakeTimer:
    def __init__(self, seconds, callback):
        self.seconds = seconds
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


class TimerFactory:
    def __init__(self):
        self.timers = []

    def __call__(self, seconds, callback):
        timer = FakeTimer(seconds, callback)
        self.timers.append(timer)
        return timer


def tone(seconds, rate=16000, amp=0.3):
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()
