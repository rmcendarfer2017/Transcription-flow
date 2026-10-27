import subprocess
import sys
import textwrap
import uuid

import pytest

from helpers import child_env
from transcription_flow.single_instance import AlreadyRunning, acquire


@pytest.fixture
def name():
    return f"Local\\TranscriptionFlow.Test.{uuid.uuid4().hex}"


def test_second_acquire_is_refused(tmp_path, name):
    first = acquire(tmp_path, name)
    try:
        with pytest.raises(AlreadyRunning):
            acquire(tmp_path, name)
    finally:
        first.release()


def test_release_lets_the_next_copy_start(tmp_path, name):
    acquire(tmp_path, name).release()
    acquire(tmp_path, name).release()


def test_another_process_is_refused_while_held(tmp_path, name):
    held = acquire(tmp_path, name)
    code = textwrap.dedent(f"""
        import sys
        from transcription_flow.single_instance import acquire, AlreadyRunning
        try:
            acquire({str(tmp_path)!r}, {name!r})
        except AlreadyRunning:
            sys.exit(3)
        sys.exit(0)
    """)
    run = lambda: subprocess.run([sys.executable, "-c", code], timeout=30,  # noqa: E731
                                 env=child_env()).returncode
    try:
        assert run() == 3
    finally:
        held.release()
    assert run() == 0


# -- second launch: "show your window" (review-m3 deviation 3) -------------

from helpers import wait_for  # noqa: E402
from transcription_flow import single_instance  # noqa: E402
from transcription_flow.single_instance import ShowListener, request_show  # noqa: E402


@pytest.fixture
def listener(tmp_path, name):
    made = []

    def make(**kw):
        made.append(ShowListener(tmp_path, name, **kw))
        return made[-1]

    yield make
    for one in made:
        one.close()


def test_a_show_request_with_nobody_listening_is_not_delivered(tmp_path, name):
    assert request_show(tmp_path, name) is False


def test_a_show_request_reaches_the_connected_handler(tmp_path, name, listener):
    shown = []
    listener().connect(lambda: shown.append(True))
    assert request_show(tmp_path, name) is True
    assert wait_for(lambda: shown == [True])
    assert request_show(tmp_path, name) is True  # and again: it keeps listening
    assert wait_for(lambda: shown == [True, True])


def test_a_request_before_connect_runs_at_connect(tmp_path, name, listener):
    """The app is still loading its model when the second copy starts."""
    show = listener()
    assert request_show(tmp_path, name) is True
    assert wait_for(lambda: show._pending)
    shown = []
    show.connect(lambda: shown.append(True))
    assert shown == [True] and not show._pending


def test_a_failing_handler_keeps_the_listener_alive(tmp_path, name, listener):
    calls = []

    def handler():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("Tk is busy")

    listener().connect(handler)
    request_show(tmp_path, name)
    assert wait_for(lambda: len(calls) == 1)
    request_show(tmp_path, name)
    assert wait_for(lambda: len(calls) == 2)


def test_close_stops_the_thread_and_later_requests_are_not_delivered(tmp_path, name):
    show = ShowListener(tmp_path, name)
    shown = []
    show.connect(lambda: shown.append(True))
    show.close()
    assert not show._thread.is_alive()
    assert request_show(tmp_path, name) is False
    assert shown == []  # closing is not a show request
    if sys.platform != "win32":
        assert not (tmp_path / single_instance.SHOW_FIFO).exists()


def test_a_request_from_another_process_is_delivered(tmp_path, name, listener):
    shown = []
    listener().connect(lambda: shown.append(True))
    code = textwrap.dedent(f"""
        import sys
        from transcription_flow.single_instance import request_show
        sys.exit(0 if request_show({str(tmp_path)!r}, {name!r}) else 4)
    """)
    assert subprocess.run([sys.executable, "-c", code], timeout=30,
                          env=child_env()).returncode == 0
    assert wait_for(lambda: shown == [True])


@pytest.mark.skipif(sys.platform == "win32", reason="the FIFO is the non-Windows channel")
def test_a_stale_fifo_from_a_crashed_copy_is_replaced(tmp_path, name, listener):
    import os

    os.mkfifo(tmp_path / single_instance.SHOW_FIFO)
    assert request_show(tmp_path, name) is False  # nobody reads the stale one
    shown = []
    listener().connect(lambda: shown.append(True))
    assert request_show(tmp_path, name) is True
    assert wait_for(lambda: shown == [True])


class FakeKernel32:
    """Named auto-reset events, as Windows has them, for the Win32 path on Linux."""

    def __init__(self):
        import threading

        self.events, self.handles, self.closed = {}, {}, []
        self._threading = threading
        self._next = 100

    def _handle_for(self, name):
        self._next += 4
        self.handles[self._next] = name
        return self._next

    def CreateEventW(self, attrs, manual_reset, initial, name):  # noqa: N802
        assert manual_reset is False and initial is False  # auto-reset, starts unset
        self.events.setdefault(name, self._threading.Semaphore(0))
        return self._handle_for(name)

    def OpenEventW(self, access, inherit, name):  # noqa: N802
        assert access == single_instance.EVENT_MODIFY_STATE
        return self._handle_for(name) if name in self.events else 0

    def SetEvent(self, handle):  # noqa: N802
        self.events[self.handles[handle]].release()
        return 1

    def WaitForSingleObject(self, handle, ms):  # noqa: N802
        assert ms == single_instance.INFINITE
        self.events[self.handles[handle]].acquire()
        return 0

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


def test_the_windows_channel_is_a_named_event(tmp_path, name, listener):
    k = FakeKernel32()
    assert request_show(tmp_path, name, kernel32=k) is False  # OpenEventW found none
    shown = []
    show = listener(kernel32=k)
    show.connect(lambda: shown.append(True))
    assert request_show(tmp_path, name, kernel32=k) is True
    assert wait_for(lambda: shown == [True])
    opened = [h for h, n in k.handles.items() if n == name][-1]
    assert opened in k.closed  # the second copy closes its handle
    show.close()
    assert not show._thread.is_alive() and shown == [True]
    assert len(k.closed) == 2  # and the listener closes its own
