"""Hold-to-talk hotkey.

Three layers, so the Windows hook does almost nothing:

1. ``PynputHook`` installs a low-level keyboard hook (pynput). Its filter only
   puts ``(kind, is_hotkey)`` on a queue and, for keys whose only job is to be
   our hotkey, suppresses them. Windows silently removes a low-level hook that
   stalls past LowLevelHooksTimeout, so the filter never opens the microphone,
   takes a lock or logs (review-m1, M2 notes).
2. ``Hotkey`` drains that queue on its own thread and feeds
3. ``HotkeyTracker``, the platform-free logic: press, release, and cancel when
   another key goes down while the hotkey is held (a shortcut such as
   Right Ctrl+C) or when the key-up was missed.

Missed key-up: when focus moves to an elevated window, the UAC prompt or the
lock screen, our hook stops seeing keys, so the release never arrives. While
a key is held Windows sends auto-repeat key-downs, so a held hotkey with no
event for longer than the repeat gap is treated as released and the
recording is cancelled (with a toast), never typed into whatever has focus
now. The gap comes from the user's keyboard delay and Filter Keys settings,
read at each press (``WindowsKeyRepeat``, review-m2 F3). GetAsyncKeyState
can't do this job for a suppressed key (it never reaches the async key
state); it is the fallback only when keys don't repeat at all and the hotkey
is a modifier we let through.
"""

import ctypes
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

DOWN, UP, RESET, STOP = "down", "up", "reset", "stop"
MIN_REPEAT_GAP = 1.5  # seconds: the floor, fits Windows' slowest standard repeat
REPEAT_MARGIN = 0.5  # seconds on top of the longest wait between two key-downs
POLL_SECONDS = 0.25
MISSED_RELEASE = "the key release was missed"

# Low-level hook messages and flags (winuser.h).
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_LOWER_IL_INJECTED, LLKHF_INJECTED = 0x02, 0x10

# SystemParametersInfoW actions and the FILTERKEYS flag (winuser.h).
SPI_GETKEYBOARDSPEED, SPI_GETKEYBOARDDELAY, SPI_GETFILTERKEYS = 0x000A, 0x0016, 0x0032
FKF_FILTERKEYSON = 0x01


@dataclass(frozen=True)
class HotkeySpec:
    label: str
    vk: int
    # True for keys with no other job: the hold never reaches apps (Caps Lock
    # would toggle, F12 would open developer tools) unless dictation is
    # paused. Right Ctrl passes through so shortcuts such as Right Ctrl+C keep
    # working (they cancel the recording).
    suppress: bool


# Left out on purpose (review-m2 F4): Pause sends its key-up together with
# the key-down, so it can't be held. Right Alt opens an app's menu bar when
# it is released with no key in between (and is AltGr on many European
# layouts). Holding Right Shift for 8 seconds opens the Filter Keys dialog.
HOTKEYS = {
    "right_ctrl": HotkeySpec("Right Ctrl", 0xA3, False),
    "caps_lock": HotkeySpec("Caps Lock", 0x14, True),
    "scroll_lock": HotkeySpec("Scroll Lock", 0x91, True),
    **{f"f{n}": HotkeySpec(f"F{n}", 0x70 + n - 1, True) for n in range(8, 13)},
}
DEFAULT_HOTKEY = "right_ctrl"


class HotkeyUnavailable(Exception):
    """The global hotkey can't run here; ``str(err)`` is the message."""


def repeat_gap(delay: float, period: float) -> float:
    """The longest a held key may go without a key-down: the longer of the
    first wait (delay) and the wait between repeats (period), plus a margin
    for a busy PC, and never below the floor."""
    return max(MIN_REPEAT_GAP, max(delay, period) + REPEAT_MARGIN)


class _FilterKeys(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint),
                ("iWaitMSec", ctypes.c_uint), ("iDelayMSec", ctypes.c_uint),
                ("iRepeatMSec", ctypes.c_uint), ("iBounceMSec", ctypes.c_uint)]


class WindowsKeyRepeat:
    """The user's auto-repeat settings, read when a press starts.

    ``gap()`` is the repeat gap in seconds, or None when Filter Keys has
    turned key repeat off. ``is_down(vk)`` is GetAsyncKeyState (None when
    unknown). Anything that can't be read gives the floor, never a crash.
    """

    def __init__(self, user32=None):
        self._user32 = user32

    def _api(self):
        if self._user32 is None:
            self._user32 = ctypes.windll.user32  # AttributeError off Windows
        return self._user32

    def gap(self):
        try:
            spi = self._api().SystemParametersInfoW
            fk = _FilterKeys(cbSize=ctypes.sizeof(_FilterKeys))
            if (spi(SPI_GETFILTERKEYS, ctypes.sizeof(fk), ctypes.byref(fk), 0)
                    and fk.dwFlags & FKF_FILTERKEYSON and not fk.iBounceMSec):
                if not (fk.iDelayMSec and fk.iRepeatMSec):
                    return None  # Filter Keys with repeat off
                return repeat_gap(fk.iDelayMSec / 1000, fk.iRepeatMSec / 1000)
            delay, speed = ctypes.c_uint(), ctypes.c_uint()
            if not (spi(SPI_GETKEYBOARDDELAY, 0, ctypes.byref(delay), 0)
                    and spi(SPI_GETKEYBOARDSPEED, 0, ctypes.byref(speed), 0)):
                return MIN_REPEAT_GAP
            # Delay 0-3 = 250-1000 ms; speed 0-31 = about 2.5-30 repeats a second.
            return repeat_gap((min(delay.value, 3) + 1) * 0.25,
                              1 / (2.5 + min(speed.value, 31) * 27.5 / 31))
        except (AttributeError, OSError):
            return MIN_REPEAT_GAP

    def is_down(self, vk: int):
        try:
            return bool(self._api().GetAsyncKeyState(vk) & 0x8000)
        except (AttributeError, OSError):
            return None


class HotkeyTracker:
    """``repeat_gap()`` is read at each press (seconds, or None when keys
    don't repeat); ``still_down()`` answers True, False or None (unknown)
    and is asked only when there is no gap."""

    def __init__(self, on_press, on_release, on_cancel, clock=time.monotonic,
                 repeat_gap=lambda: MIN_REPEAT_GAP, still_down=lambda: None):
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel
        self._clock = clock
        self._repeat_gap = repeat_gap
        self._still_down = still_down
        self._gap = MIN_REPEAT_GAP
        self._last_down = 0.0
        self.held = False

    def key_down(self, is_hotkey: bool) -> None:
        if is_hotkey:
            self._last_down = self._clock()
            if not self.held:  # auto-repeat keeps coming while held
                self.held = True
                self._gap = self._repeat_gap()
                if self._gap is None:
                    log.info("keys don't auto-repeat here (Filter Keys)")
                self._on_press()
        elif self.held:
            self._on_cancel("another key was pressed")

    def key_up(self, is_hotkey: bool) -> None:
        if is_hotkey and self.held:
            self.held = False
            self._on_release()

    def check(self) -> None:
        """Call when no key event came for a while."""
        if not self.held:
            return
        if self._gap is None:
            missed = self._still_down() is False
        else:
            missed = self._clock() - self._last_down > self._gap
        if missed:
            self.held = False
            self._on_cancel(MISSED_RELEASE)

    def reset(self) -> None:
        if self.held:
            self.held = False
            self._on_cancel("the hotkey changed")


class Hotkey:
    """Owns the event queue, the dispatcher thread and the hook."""

    def __init__(self, on_press, on_release, on_cancel, hook=None, clock=time.monotonic,
                 poll=POLL_SECONDS, repeat=None):
        self._hook = hook or PynputHook()
        self._repeat = repeat or WindowsKeyRepeat()
        self._events = queue.SimpleQueue()
        self._tracker = HotkeyTracker(on_press, on_release, on_cancel, clock,
                                      self._repeat.gap, self._still_down)
        self._poll = poll
        self._handle = None
        self._thread = None
        # Read by the hook filter (an attribute read, safe inside the hook):
        # while set, suppressed hotkeys reach apps again (review-m2 F5).
        self._paused = threading.Event()
        self.key_id = None

    def _still_down(self):
        spec = HOTKEYS[self.key_id]
        # A key our hook suppresses never reaches the async key state.
        return None if spec.suppress else self._repeat.is_down(spec.vk)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def start(self, key_id: str) -> None:
        self.set_key(key_id)  # raises HotkeyUnavailable before a thread exists
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()

    def set_key(self, key_id: str) -> None:
        spec = HOTKEYS[key_id]
        if self._handle is not None:
            self._handle.stop()
            self._handle = None
        self._events.put((RESET, False))
        self._handle = self._hook.install(spec, self._events.put, self._paused)
        self.key_id = key_id

    def stop(self, timeout=2.0) -> None:
        if self._handle is not None:
            self._handle.stop()
            self._handle = None
        if self._thread is not None:
            self._events.put((STOP, False))
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        tracker = self._tracker
        while True:
            try:
                kind, is_hotkey = self._events.get(timeout=self._poll if tracker.held else None)
            except queue.Empty:
                kind, is_hotkey = None, False
            if kind == STOP:
                return
            try:
                if kind == DOWN:
                    tracker.key_down(is_hotkey)
                elif kind == UP:
                    tracker.key_up(is_hotkey)
                elif kind == RESET:
                    tracker.reset()
                else:
                    tracker.check()
            except Exception:  # keep the hotkey alive; no transcript can be in here
                log.exception("hotkey handler failed")


class PynputHook:
    """The Windows low-level keyboard hook, through pynput."""

    def __init__(self, keyboard_module=None, platform=sys.platform):
        self._kb = keyboard_module
        self._platform = platform

    def install(self, spec: HotkeySpec, put, paused: threading.Event):
        """Start a hook for ``spec``; returns a handle with ``stop()``. While
        ``paused`` is set, nothing is suppressed."""
        if self._platform != "win32":
            raise HotkeyUnavailable("The global hotkey needs Windows.")
        if self._kb is None:
            try:
                from pynput import keyboard
            except ImportError as err:
                raise HotkeyUnavailable("The global hotkey needs the pynput package.") from err
            self._kb = keyboard
        listener = None

        def event_filter(msg, data):
            # Runs inside the hook: queue the event, nothing else.
            try:
                if data.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
                    return False  # our own typing, or another app's
                is_hotkey = data.vkCode == spec.vk
                if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    put((DOWN, is_hotkey))
                elif msg in (WM_KEYUP, WM_SYSKEYUP):
                    put((UP, is_hotkey))
                suppress = is_hotkey and spec.suppress and not paused.is_set()
            except Exception:  # an exception here would stop the listener
                return False
            if suppress:
                listener.suppress_event()  # raises pynput's own exception
            return False  # we don't use pynput's on_press/on_release

        listener = self._kb.Listener(win32_event_filter=event_filter)
        listener.start()
        listener.wait()
        return listener
