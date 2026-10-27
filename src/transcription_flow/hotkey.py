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
a key is held Windows sends auto-repeat key-downs (at least every second with
the slowest keyboard settings), so a held hotkey with no event for
``REPEAT_GAP`` seconds is treated as released and the recording is cancelled,
never typed into whatever has focus now. GetAsyncKeyState can't do this job:
a key our hook suppresses never reaches the async key state.
"""

import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

DOWN, UP, RESET, STOP = "down", "up", "reset", "stop"
REPEAT_GAP = 1.5  # seconds without a key-down while held = key-up missed
POLL_SECONDS = 0.25

# Low-level hook messages and flags (winuser.h).
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_LOWER_IL_INJECTED, LLKHF_INJECTED = 0x02, 0x10

ALTGR_NOTE = ("On many European keyboard layouts Right Alt is AltGr, which types "
              "characters such as @ and €. A quick tap can also open an app's menu bar.")


@dataclass(frozen=True)
class HotkeySpec:
    label: str
    vk: int
    # True for keys with no other job: the hold never reaches apps (Caps Lock
    # would toggle, F12 would open developer tools). Modifiers pass through so
    # shortcuts such as Right Ctrl+C keep working (they cancel the recording).
    suppress: bool
    note: str = ""


# The Pause key is left out on purpose: it sends its key-up together with
# the key-down, so it can't be held.
HOTKEYS = {
    "right_ctrl": HotkeySpec("Right Ctrl", 0xA3, False),
    "right_alt": HotkeySpec("Right Alt", 0xA5, False, ALTGR_NOTE),
    "right_shift": HotkeySpec("Right Shift", 0xA1, False),
    "caps_lock": HotkeySpec("Caps Lock", 0x14, True),
    "scroll_lock": HotkeySpec("Scroll Lock", 0x91, True),
    **{f"f{n}": HotkeySpec(f"F{n}", 0x70 + n - 1, True) for n in range(8, 13)},
}
DEFAULT_HOTKEY = "right_ctrl"


class HotkeyUnavailable(Exception):
    """The global hotkey can't run here; ``str(err)`` is the message."""


class HotkeyTracker:
    def __init__(self, on_press, on_release, on_cancel, clock=time.monotonic,
                 repeat_gap=REPEAT_GAP):
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel
        self._clock = clock
        self._repeat_gap = repeat_gap
        self._last_down = 0.0
        self.held = False

    def key_down(self, is_hotkey: bool) -> None:
        if is_hotkey:
            self._last_down = self._clock()
            if not self.held:  # auto-repeat keeps coming while held
                self.held = True
                self._on_press()
        elif self.held:
            self._on_cancel("another key was pressed")

    def key_up(self, is_hotkey: bool) -> None:
        if is_hotkey and self.held:
            self.held = False
            self._on_release()

    def check(self) -> None:
        """Call when no key event came for a while."""
        if self.held and self._clock() - self._last_down > self._repeat_gap:
            self.held = False
            self._on_cancel("the key release was missed")

    def reset(self) -> None:
        if self.held:
            self.held = False
            self._on_cancel("the hotkey changed")


class Hotkey:
    """Owns the event queue, the dispatcher thread and the hook."""

    def __init__(self, on_press, on_release, on_cancel, hook=None, clock=time.monotonic,
                 poll=POLL_SECONDS, repeat_gap=REPEAT_GAP):
        self._hook = hook or PynputHook()
        self._events = queue.SimpleQueue()
        self._tracker = HotkeyTracker(on_press, on_release, on_cancel, clock, repeat_gap)
        self._poll = poll
        self._handle = None
        self._thread = None
        self.key_id = None

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
        self._handle = self._hook.install(spec, self._events.put)
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

    def install(self, spec: HotkeySpec, put):
        """Start a hook for ``spec``; returns a handle with ``stop()``."""
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
                suppress = is_hotkey and spec.suppress
            except Exception:  # an exception here would stop the listener
                return False
            if suppress:
                listener.suppress_event()  # raises pynput's own exception
            return False  # we don't use pynput's on_press/on_release

        listener = self._kb.Listener(win32_event_filter=event_filter)
        listener.start()
        listener.wait()
        return listener
