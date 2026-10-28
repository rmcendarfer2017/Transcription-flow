"""Test fakes and helpers (no real audio devices, hotkeys or network)."""

import inspect
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import transcription_flow

SRC_DIR = Path(transcription_flow.__file__).resolve().parent.parent


def child_env(drop=(), **extra):
    """Environment for a child Python that imports the package from src/.
    PYTHONPATH is always replaced (src/ plus any PYTHONPATH passed in extra)."""
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(extra)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(SRC_DIR), extra.get("PYTHONPATH")) if p)
    return env


class FakeMenuItem:
    """pystray.MenuItem with the checks pystray 0.19.5 makes in __init__
    (_base.py MenuItem._assert_action and _assert_callable): an action may
    take at most two positional parameters, defaults counted, and checked
    must be None or callable. The M4 build crashed at start on the first."""

    def __init__(self, text, action, checked=None, enabled=True, default=False):
        if action is not None and hasattr(action, "__code__"):
            argcount = action.__code__.co_argcount - (1 if inspect.ismethod(action) else 0)
            if argcount > 2:
                raise ValueError(action)
        if checked is not None and not callable(checked):
            raise ValueError(checked)
        self.text, self.action, self.checked, self.enabled, self.default = (
            text, action, checked, enabled, default)


class FakeMenu:
    SEPARATOR = FakeMenuItem("----", None)  # pystray's is a MenuItem too

    def __init__(self, *items):
        self.items = items

    def __iter__(self):
        return iter(self.items)


class PortAudioError(Exception):
    pass


class FakeStream:
    def __init__(self, sd, samplerate, channels, dtype, device, callback):
        self.sd = sd
        self.device = device
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

    def __init__(self, device_info=None, accepted_rates=None, default_rate=16000, devices=(),
                 hostapi=0):
        self.device_info = device_info if device_info is not None else {
            "name": "Fake mic", "max_input_channels": 1, "default_samplerate": default_rate}
        self.accepted_rates = accepted_rates
        self.streams = []
        self.devices = [dict(d, index=i) for i, d in enumerate(devices)]  # query_devices()
        self.default = SimpleNamespace(hostapi=hostapi)

    def query_devices(self, device=None, kind=None):
        if self.device_info is False:
            raise PortAudioError("Error querying device -1")
        if device is None and kind is None:
            return self.devices
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


def flush_logs():
    for handler in logging.getLogger().handlers:
        handler.flush()


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()
