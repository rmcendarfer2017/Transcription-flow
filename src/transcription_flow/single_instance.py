"""Single-instance guard (review H5): autostart plus a manual launch must not
give two processes that each type every dictation.

Windows: a named mutex (the installer's AppMutex will use the same name).
Elsewhere: an exclusive flock on a file in the data folder.
"""

import os
import sys
from pathlib import Path

MUTEX_NAME = "Local\\TranscriptionFlow.SingleInstance"
ERROR_ALREADY_EXISTS = 183


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
