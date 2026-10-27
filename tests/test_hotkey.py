"""The pynput hotkey: hook filter, dispatcher thread, missed key-up."""

import threading

import pytest

from helpers import wait_for
from transcription_flow import hotkey as hk
from transcription_flow.hotkey import (DOWN, HOTKEYS, UP, Hotkey, HotkeyTracker,
                                       HotkeyUnavailable, PynputHook)


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
                            repeat_gap=1.5)
    return tracker, log, clock


# -- the key table ------------------------------------------------------------

def test_default_hotkey_is_right_ctrl_and_every_key_has_a_distinct_code():
    assert HOTKEYS[hk.DEFAULT_HOTKEY].label == "Right Ctrl"
    assert HOTKEYS["right_ctrl"].vk == 0xA3  # VK_RCONTROL, not the shared VK_CONTROL
    assert len({spec.vk for spec in HOTKEYS.values()}) == len(HOTKEYS)
    assert [HOTKEYS[f"f{n}"].vk for n in range(8, 13)] == [0x77, 0x78, 0x79, 0x7A, 0x7B]


def test_pause_is_not_offered_because_it_cannot_be_held():
    assert not any("pause" in key or spec.vk == 0x13 for key, spec in HOTKEYS.items())


def test_modifiers_pass_through_and_single_purpose_keys_are_suppressed():
    assert not any(HOTKEYS[k].suppress for k in ("right_ctrl", "right_alt", "right_shift"))
    assert all(HOTKEYS[k].suppress for k in ("caps_lock", "scroll_lock", "f8", "f12"))


def test_right_alt_carries_the_altgr_warning():
    assert "AltGr" in HOTKEYS["right_alt"].note
    assert all(not spec.note for key, spec in HOTKEYS.items() if key != "right_alt")


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

    def install(self, spec, put):
        if self.fail:
            raise HotkeyUnavailable("no hook here")
        handle = FakeHandle(spec, put)
        self.handles.append(handle)
        return handle


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
                 repeat_gap=0.2)
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


def install(key_id):
    kb, events = FakePynput(), []
    handle = PynputHook(keyboard_module=kb, platform="win32").install(HOTKEYS[key_id],
                                                                       events.append)
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


def test_filter_handles_syskey_messages_for_right_alt():
    handle, f, events = install("right_alt")
    f(hk.WM_SYSKEYDOWN, Data(0xA5))
    f(hk.WM_SYSKEYUP, Data(0xA5))
    assert events == [(DOWN, True), (UP, True)]


def test_filter_suppresses_a_single_purpose_hotkey_after_queueing_it():
    handle, f, events = install("caps_lock")
    with pytest.raises(Suppressed):
        f(hk.WM_KEYDOWN, Data(0x14))
    with pytest.raises(Suppressed):
        f(hk.WM_KEYUP, Data(0x14))
    assert f(hk.WM_KEYDOWN, Data(0x41)) is False  # other keys still reach apps
    assert events == [(DOWN, True), (UP, True), (DOWN, False)]


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
            HOTKEYS["right_ctrl"], lambda e: None)
