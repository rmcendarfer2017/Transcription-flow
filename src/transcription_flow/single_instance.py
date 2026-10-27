"""Single-instance guard (review H5): autostart plus a manual launch must not
give two processes that each type every dictation.

Windows: a named mutex (packaging/installer.iss uses the same name as its
AppMutex). Elsewhere: an exclusive flock on a file in the data folder.

A second copy started by hand asks the running one to show its window
(review-m3 deviation 3): under pythonw it would otherwise exit with nothing
on screen. Windows: a named auto-reset event the first copy waits on.
Elsewhere (dev and tests only): a FIFO in the data folder.
"""

import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

MUTEX_NAME = "Local\\TranscriptionFlow.SingleInstance"
SHOW_EVENT_NAME = "Local\\TranscriptionFlow.ShowWindow"
SHOW_FIFO = "show.fifo"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
INFINITE = 0xFFFFFFFF
ASFW_ANY = 0xFFFFFFFF  # (DWORD)-1


class AlreadyRunning(Exception):
    pass


class _WindowsMutex:
    def __init__(self, name: str):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32 = kernel32
        ctypes.set_last_error(0)
        self._handle = kernel32.CreateMutexW(None, False, name)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self.release()
            raise AlreadyRunning()

    def release(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class _FileLock:
    def __init__(self, path: Path):
        import fcntl

        path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            raise AlreadyRunning() from None

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


def acquire(lock_dir: Path, name: str = MUTEX_NAME):
    """Return a guard with .release(); raise AlreadyRunning if another copy holds it."""
    if sys.platform == "win32":
        return _WindowsMutex(name)
    return _FileLock(Path(lock_dir) / "instance.lock")


# -- second launch: "show your window" -----------------------------------


def _kernel32():
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateEventW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenEventW.restype = wintypes.HANDLE
    k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.SetEvent.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def _allow_foreground() -> None:
    """The copy the user just started may hand its foreground right on, so
    the running copy's window comes to the front instead of flashing."""
    try:
        import ctypes

        ctypes.WinDLL("user32").AllowSetForegroundWindow(ASFW_ANY)
    except (OSError, AttributeError):
        pass


class _WinEvent:
    def __init__(self, name: str, kernel32=None):
        self._k = kernel32 or _kernel32()
        self._handle = self._k.CreateEventW(None, False, False, name)
        if not self._handle:
            raise OSError("CreateEventW failed")

    def wait(self) -> None:
        self._k.WaitForSingleObject(self._handle, INFINITE)

    def wake(self) -> None:
        self._k.SetEvent(self._handle)

    def close(self) -> None:
        if self._handle:
            self._k.CloseHandle(self._handle)
            self._handle = None


class _Fifo:
    """Opened read-write, so reads block until a writer sends a byte (and
    never see end-of-file), and a writer's non-blocking open finds a reader
    from the moment this exists."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)  # left by a copy that crashed; we hold the lock
        os.mkfifo(path, 0o600)
        self._path = path
        self._fd = os.open(path, os.O_RDWR)

    def wait(self) -> None:
        os.read(self._fd, 512)

    def wake(self) -> None:
        os.write(self._fd, b"!")

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            self._path.unlink(missing_ok=True)


def request_show(lock_dir: Path, name: str = SHOW_EVENT_NAME, kernel32=None) -> bool:
    """A second copy: ask the running one to show its window. True if a
    running copy is listening (it then opens Settings or the setup window)."""
    if sys.platform == "win32" or kernel32 is not None:
        k = kernel32 or _kernel32()
        handle = k.OpenEventW(EVENT_MODIFY_STATE, False, name)
        if not handle:
            return False
        try:
            if kernel32 is None:
                _allow_foreground()
            return bool(k.SetEvent(handle))
        finally:
            k.CloseHandle(handle)
    try:
        fd = os.open(Path(lock_dir) / SHOW_FIFO, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:  # no FIFO, or nobody reading it
        return False
    try:
        os.write(fd, b"!")
        return True
    finally:
        os.close(fd)


class ShowListener:
    """The running copy: waits on a thread (no polling) for request_show and
    calls the handler given to connect(). A request that comes before
    connect() (the app is still loading its model) runs at connect()."""

    def __init__(self, lock_dir: Path, name: str = SHOW_EVENT_NAME, kernel32=None):
        if sys.platform == "win32" or kernel32 is not None:
            self._channel = _WinEvent(name, kernel32)
        else:
            self._channel = _Fifo(Path(lock_dir) / SHOW_FIFO)
        self._lock = threading.Lock()
        self._handler = None
        self._pending = False
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="show-listener", daemon=True)
        self._thread.start()

    def connect(self, handler) -> None:
        with self._lock:
            self._handler = handler
            pending, self._pending = self._pending, False
        if pending:
            self._call(handler)

    def _run(self) -> None:
        while True:
            self._channel.wait()
            if self._closing:
                return
            with self._lock:
                handler = self._handler
                self._pending = handler is None
            if handler is not None:
                self._call(handler)

    @staticmethod
    def _call(handler) -> None:
        log.info("another copy was started: showing the window")
        try:
            handler()
        except Exception:  # keep listening
            log.warning("could not show the window", exc_info=True)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._channel.wake()
        self._thread.join(timeout=2)
        self._channel.close()
