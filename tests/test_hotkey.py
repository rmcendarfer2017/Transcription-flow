"""The pynput hotkey: hook filter, dispatcher thread, missed key-up."""

import ctypes
import threading

import pytest

from helpers import wait_for
from transcription_flow import hotkey as hk
from transcription_flow.hotkey import (DOWN, HOTKEYS, MISSED_RELEASE, UP, Hotkey,
                                       HotkeyTracker, HotkeyUnavailable, PynputHook,
                                       WindowsKeyRepeat)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def tracked():
    log, clock = [], Clock()
    tracker = HotkeyTracker(lambda: log.append("press"), lambda: log.append("release"),
                            lambda reason: log.append(f"cancel: {reason}"), clock=clock,
                            repeat_gap=lambda: 1.5)
    return tracker, log, clock


# -- the key table ------------------------------------------------------------

def test_default_hotkey_is_right_ctrl_and_every_key_has_a_distinct_code():
    assert HOTKEYS[hk.DEFAULT_HOTKEY].label == "Right Ctrl"
    assert HOTKEYS["right_ctrl"].vk == 0xA3  # VK_RCONTROL, not the shared VK_CONTROL
    assert len({spec.vk for spec in HOTKEYS.values()}) == len(HOTKEYS)
    assert [HOTKEYS[f"f{n}"].vk for n in range(8, 13)] == [0x77, 0x78, 0x79, 0x7A, 0x7B]


def test_pause_is_not_offered_because_it_cannot_be_held():
    assert not any("pause" in key or spec.vk == 0x13 for key, spec in HOTKEYS.items())


def test_right_ctrl_passes_through_and_single_purpose_keys_are_suppressed():
    assert not HOTKEYS["right_ctrl"].suppress
    assert all(HOTKEYS[k].suppress for k in ("caps_lock", "scroll_lock", "f8", "f12"))


def test_right_alt_and_right_shift_are_not_offered():
    """review-m2 F4: Right Alt opens the menu bar on release (and is AltGr on
    many layouts); an 8 s Right Shift hold opens the Filter Keys dialog."""
    vks = {spec.vk for spec in HOTKEYS.values()}
    assert not {"right_alt", "right_shift"} & set(HOTKEYS)
    assert not {0xA4, 0xA5, 0x12, 0xA0, 0xA1, 0x10} & vks  # no Alt or Shift at all


# -- missed key-up ------------------------------------------------------------

def test_auto_repeat_keeps_a_long_hold_alive(tracked):
    tracker, log, clock = tracked
    tracker.key_down(True)
    for _ in range(40):  # 40 s of holding, a repeat every second (the slowest setting)
        clock.now += 1.0
        tracker.key_down(True)
        tracker.check()
    tracker.key_up(True)
    assert log == ["press", "release"]


def test_no_repeat_for_longer_than_the_gap_cancels(tracked):
    tracker, log, clock = tracked
    tracker.key_down(True)
    clock.now += 1.5
    tracker.check()
    assert log == ["press"]  # exactly the gap: still held
    clock.now += 0.01
    tracker.check()
    assert log == ["press", "cancel: the key release was missed"]
    assert not tracker.held


def test_after_a_missed_release_the_late_key_up_does_nothing_and_a_new_press_works(tracked):
    tracker, log, clock = tracked
    tracker.key_down(True)
    clock.now += 5
    tracker.check()
    tracker.key_up(True)  # the release arrives after all
    tracker.key_down(True)
    assert log == ["press", "cancel: the key release was missed", "press"]


def test_the_gap_is_read_at_each_press():
    log, clock, gaps = [], Clock(), [3.0, 1.5]
    tracker = HotkeyTracker(lambda: log.append("press"), lambda: log.append("release"),
                            lambda reason: log.append(f"cancel: {reason}"), clock=clock,
                            repeat_gap=lambda: gaps.pop(0))
    tracker.key_down(True)
    clock.now += 2.9  # inside the first press's 3 s gap
    tracker.check()
    tracker.key_up(True)
    tracker.key_down(True)  # the user changed the setting: 1.5 s now
    clock.now += 1.6
    tracker.check()
    assert log == ["press", "release", "press", f"cancel: {MISSED_RELEASE}"]


def no_repeat_tracker(still_down):
    log, clock = [], Clock()
    tracker = HotkeyTracker(lambda: log.append("press"), lambda: log.append("release"),
                            lambda reason: log.append(f"cancel: {reason}"), clock=clock,
                            repeat_gap=lambda: None, still_down=still_down)
    tracker.key_down(True)
    clock.now += 60  # no repeats at all with Filter Keys repeat off
    tracker.check()
    return log


def test_without_repeat_a_key_still_down_is_kept():
    assert no_repeat_tracker(lambda: True) == ["press"]


def test_without_repeat_a_key_seen_up_is_a_missed_release():
    assert no_repeat_tracker(lambda: False) == ["press", f"cancel: {MISSED_RELEASE}"]


def test_without_repeat_and_no_key_state_nothing_is_cancelled():
    assert no_repeat_tracker(lambda: None) == ["press"]


def test_check_when_not_held_does_nothing(tracked):
    tracker, log, clock = tracked
    clock.now += 100
    tracker.check()
    assert log == []


def test_reset_cancels_only_while_held(tracked):
    tracker, log, clock = tracked
    tracker.reset()
    tracker.key_down(True)
    tracker.reset()
    assert log == ["press", "cancel: the hotkey changed"] and not tracker.held


# -- the dispatcher thread ----------------------------------------------------

class FakeHandle:
    def __init__(self, spec, put):
        self.spec, self.put, self.stopped = spec, put, False

    def stop(self):
        self.stopped = True


class FakeHook:
    def __init__(self, fail=False):
        self.handles = []
        self.fail = fail

    def install(self, spec, put, paused):
        if self.fail:
            raise HotkeyUnavailable("no hook here")
        handle = FakeHandle(spec, put)
        handle.paused = paused
        self.handles.append(handle)
        return handle


class FakeRepeat:
    def __init__(self, gap=0.2, down=None):
        self.gap_value, self.down, self.asked = gap, down, []

    def gap(self):
        return self.gap_value

    def is_down(self, vk):
        self.asked.append(vk)
        return self.down


@pytest.fixture
def running():
    log, hook = [], FakeHook()
    events = threading.Event()

    def note(name):
        def f(*args):
            log.append(name if not args else f"{name}: {args[0]}")
            events.set()
        return f

    key = Hotkey(note("press"), note("release"), note("cancel"), hook=hook, poll=0.01,
                 repeat=FakeRepeat(0.2))
    key.start("right_ctrl")
    yield key, hook, log
    key.stop()


def test_hook_events_reach_the_callbacks_on_the_dispatcher_thread(running):
    key, hook, log = running
    seen = []
    key._tracker._on_press = lambda: seen.append(threading.current_thread().name)
    put = hook.handles[-1].put
    put((DOWN, True))
    put((DOWN, False))
    put((UP, True))
    assert wait_for(lambda: len(seen) == 1 and len(log) == 2)
    assert seen == ["hotkey"]
    assert log == ["cancel: another key was pressed", "release"]


def test_dispatcher_cancels_a_hold_with_no_repeats(running):
    key, hook, log = running
    hook.handles[-1].put((DOWN, True))
    assert wait_for(lambda: "cancel: the key release was missed" in log, timeout=3)
    assert log == ["press", "cancel: the key release was missed"]


def test_dispatcher_without_repeat_asks_the_key_state_of_right_ctrl():
    log, hook, repeat = [], FakeHook(), FakeRepeat(gap=None, down=False)
    key = Hotkey(lambda: log.append("press"), lambda: None, log.append, hook=hook, poll=0.01,
                 repeat=repeat)
    key.start("right_ctrl")
    try:
        hook.handles[-1].put((DOWN, True))
        assert wait_for(lambda: MISSED_RELEASE in log, timeout=3)
        assert log == ["press", MISSED_RELEASE] and repeat.asked[0] == 0xA3
    finally:
        key.stop()


def test_a_suppressed_key_never_asks_the_key_state():
    """A key our hook swallows never reaches GetAsyncKeyState: it would read up."""
    hook, repeat = FakeHook(), FakeRepeat(gap=None, down=False)
    key = Hotkey(lambda: None, lambda: None, lambda r: None, hook=hook, repeat=repeat)
    key.set_key("caps_lock")
    assert key._still_down() is None and repeat.asked == []
    key.set_key("right_ctrl")
    assert key._still_down() is False and repeat.asked == [0xA3]


def test_set_paused_reaches_the_hook(running):
    key, hook, log = running
    paused = hook.handles[-1].paused
    key.set_paused(True)
    assert paused.is_set()
    key.set_key("f9")  # a new hook gets the same flag
    assert hook.handles[-1].paused is paused and paused.is_set()
    key.set_paused(False)
    assert not paused.is_set()


def test_a_failing_callback_does_not_kill_the_dispatcher(running):
    key, hook, log = running
    key._tracker._on_press = lambda: 1 / 0
    put = hook.handles[-1].put
    put((DOWN, True))
    put((UP, True))
    assert wait_for(lambda: log == ["release"])


def test_set_key_stops_the_old_hook_and_resets_a_held_key(running):
    key, hook, log = running
    first = hook.handles[-1]
    first.put((DOWN, True))
    assert wait_for(lambda: log == ["press"])
    key.set_key("f9")
    assert first.stopped and hook.handles[-1].spec is HOTKEYS["f9"] and key.key_id == "f9"
    assert wait_for(lambda: log == ["press", "cancel: the hotkey changed"])


def test_stop_ends_the_thread_and_the_hook(running):
    key, hook, log = running
    thread = key._thread
    key.stop()
    assert not thread.is_alive() and hook.handles[-1].stopped


def test_start_fails_cleanly_without_a_hook():
    key = Hotkey(lambda: None, lambda: None, lambda r: None, hook=FakeHook(fail=True))
    with pytest.raises(HotkeyUnavailable):
        key.start("right_ctrl")
    assert key._thread is None


# -- the pynput hook filter -----------------------------------------------------

class Suppressed(Exception):
    pass


class FakeListener:
    def __init__(self, win32_event_filter):
        self.filter = win32_event_filter
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def wait(self):
        assert self.started

    def stop(self):
        self.stopped = True

    def suppress_event(self):
        raise Suppressed()


class FakePynput:
    def __init__(self):
        self.listeners = []

    def Listener(self, win32_event_filter):  # noqa: N802
        listener = FakeListener(win32_event_filter)
        self.listeners.append(listener)
        return listener


class Data:
    def __init__(self, vk, flags=0):
        self.vkCode, self.flags = vk, flags


def install(key_id, paused=None):
    kb, events = FakePynput(), []
    handle = PynputHook(keyboard_module=kb, platform="win32").install(
        HOTKEYS[key_id], events.append, paused or threading.Event())
    return handle, handle.filter, events


def test_filter_queues_down_and_up_and_lets_a_modifier_through():
    handle, f, events = install("right_ctrl")
    assert handle.started
    assert f(hk.WM_KEYDOWN, Data(0xA3)) is False  # False = not to pynput's callbacks
    assert f(hk.WM_KEYDOWN, Data(0x43)) is False  # C
    assert f(hk.WM_KEYUP, Data(0xA3)) is False
    assert events == [(DOWN, True), (DOWN, False), (UP, True)]


def test_filter_treats_left_ctrl_as_another_key():
    handle, f, events = install("right_ctrl")
    f(hk.WM_KEYDOWN, Data(0xA2))  # VK_LCONTROL
    assert events == [(DOWN, False)]


def test_filter_handles_syskey_messages():
    handle, f, events = install("right_ctrl")  # Right Ctrl while Alt is down
    f(hk.WM_SYSKEYDOWN, Data(0xA3))
    f(hk.WM_SYSKEYUP, Data(0xA3))
    assert events == [(DOWN, True), (UP, True)]


def test_filter_suppresses_a_single_purpose_hotkey_after_queueing_it():
    handle, f, events = install("caps_lock")
    with pytest.raises(Suppressed):
        f(hk.WM_KEYDOWN, Data(0x14))
    with pytest.raises(Suppressed):
        f(hk.WM_KEYUP, Data(0x14))
    assert f(hk.WM_KEYDOWN, Data(0x41)) is False  # other keys still reach apps
    assert events == [(DOWN, True), (UP, True), (DOWN, False)]


def test_while_paused_a_suppressed_hotkey_reaches_apps():
    """review-m2 F5: a paused F12 or Caps Lock must work as itself."""
    paused = threading.Event()
    handle, f, events = install("caps_lock", paused)
    paused.set()
    assert f(hk.WM_KEYDOWN, Data(0x14)) is False  # let through
    assert f(hk.WM_KEYUP, Data(0x14)) is False
    paused.clear()
    with pytest.raises(Suppressed):  # resumed: swallowed again
        f(hk.WM_KEYDOWN, Data(0x14))
    assert events == [(DOWN, True), (UP, True), (DOWN, True)]


@pytest.mark.parametrize("flag", [hk.LLKHF_INJECTED, hk.LLKHF_LOWER_IL_INJECTED])
def test_filter_ignores_injected_keys(flag):
    handle, f, events = install("caps_lock")
    assert f(hk.WM_KEYDOWN, Data(0x14, flags=flag)) is False  # not suppressed either
    assert f(hk.WM_KEYDOWN, Data(0x41, flags=flag)) is False
    assert events == []


def test_filter_never_raises_on_odd_data():
    handle, f, events = install("right_ctrl")
    assert f(hk.WM_KEYDOWN, object()) is False  # no vkCode/flags
    assert events == []


def test_hook_needs_windows():
    with pytest.raises(HotkeyUnavailable, match="needs Windows"):
        PynputHook(keyboard_module=FakePynput(), platform="linux").install(
            HOTKEYS["right_ctrl"], lambda e: None, threading.Event())


# -- the repeat gap from the keyboard settings (review-m2 F3) ---------------------

class FakeUser32:
    """SystemParametersInfoW and GetAsyncKeyState over plain values."""

    def __init__(self, delay=1, speed=31, filter_keys=None, fail=(), keys=()):
        self.values = {hk.SPI_GETKEYBOARDDELAY: delay, hk.SPI_GETKEYBOARDSPEED: speed}
        self.filter_keys = filter_keys  # dict of FILTERKEYS fields, or None (off)
        self.fail, self.keys = set(fail), set(keys)

    def SystemParametersInfoW(self, action, param, ref, flags):  # noqa: N802
        if action in self.fail:
            return 0
        target = ref._obj
        if action == hk.SPI_GETFILTERKEYS:
            assert param == ctypes.sizeof(target) == target.cbSize
            for name, value in (self.filter_keys or {"dwFlags": 0}).items():
                setattr(target, name, value)
        else:
            target.value = self.values[action]
        return 1

    def GetAsyncKeyState(self, vk):  # noqa: N802
        return -32768 if vk in self.keys else 0  # high bit set: down


def gap(**kw):
    return WindowsKeyRepeat(FakeUser32(**kw)).gap()


def test_gap_math():
    assert hk.repeat_gap(0.25, 0.033) == 1.5  # fast settings: the floor
    assert hk.repeat_gap(2.0, 0.5) == 2.5  # the longer wait plus the margin
    assert hk.repeat_gap(0.3, 2.0) == 2.5


def test_gap_from_the_default_keyboard_settings_is_the_floor():
    assert gap(delay=1, speed=31) == hk.MIN_REPEAT_GAP


def test_gap_for_the_slowest_standard_settings_fits_the_floor():
    # 1 s delay, about 2.5 repeats a second: 1.0 + 0.5 margin
    assert gap(delay=3, speed=0) == pytest.approx(1.5)


def test_filter_keys_with_a_long_repeat_delay_widens_the_gap():
    fk = {"dwFlags": hk.FKF_FILTERKEYSON, "iDelayMSec": 2000, "iRepeatMSec": 1000}
    assert gap(filter_keys=fk) == pytest.approx(2.5)


def test_filter_keys_with_repeat_off_gives_no_gap():
    fk = {"dwFlags": hk.FKF_FILTERKEYSON, "iWaitMSec": 500, "iDelayMSec": 0, "iRepeatMSec": 0}
    assert gap(filter_keys=fk) is None


def test_filter_keys_set_but_off_uses_the_keyboard_settings():
    fk = {"dwFlags": 0, "iDelayMSec": 0, "iRepeatMSec": 0}  # values kept, feature off
    assert gap(filter_keys=fk, delay=3, speed=0) == pytest.approx(1.5)


def test_bounce_keys_uses_the_keyboard_settings():
    fk = {"dwFlags": hk.FKF_FILTERKEYSON, "iBounceMSec": 500}
    assert gap(filter_keys=fk) == hk.MIN_REPEAT_GAP


@pytest.mark.parametrize("fail", [hk.SPI_GETKEYBOARDDELAY, hk.SPI_GETKEYBOARDSPEED])
def test_unreadable_settings_give_the_floor(fail):
    assert gap(delay=3, speed=0, fail=[fail]) == hk.MIN_REPEAT_GAP


def test_no_windows_api_gives_the_floor_and_no_key_state(monkeypatch):
    monkeypatch.delattr(ctypes, "windll", raising=False)
    repeat = WindowsKeyRepeat()
    assert repeat.gap() == hk.MIN_REPEAT_GAP and repeat.is_down(0xA3) is None


def test_key_state_reads_the_high_bit():
    repeat = WindowsKeyRepeat(FakeUser32(keys=[0xA3]))
    assert repeat.is_down(0xA3) is True and repeat.is_down(0x14) is False
