"""The Windows clipboard, for paste mode only (ctypes, no extra packages).

``save()`` copies every memory-backed format (text, HTML, RTF, images as DIB,
file lists, app formats), so ``restore()`` can put the user's clipboard back
as it was. GDI handle formats (CF_BITMAP and friends) are skipped: Windows
makes them again from the DIB.

Our text and the restored copy carry the formats that tell Windows not to
keep them in clipboard history or sync them to the cloud clipboard.
"""

import ctypes
import time

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Handles that are not global memory: GDI objects, owner-display, private.
_HANDLE_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
_PRIVATE_RANGE = range(0x0200, 0x0400)  # CF_PRIVATEFIRST .. CF_GDIOBJLAST
_PRIVACY_FORMATS = (
    ("ExcludeClipboardContentFromMonitorProcessing", b"\0\0\0\0"),
    ("CanIncludeInClipboardHistory", b"\0\0\0\0"),  # DWORD 0
    ("CanUploadToCloudClipboard", b"\0\0\0\0"),  # DWORD 0
)
OPEN_TRIES, OPEN_WAIT = 20, 0.01
HWND_MESSAGE = -3


class ClipboardError(Exception):
    pass


def _skip(fmt: int) -> bool:
    return fmt in _HANDLE_FORMATS or fmt in _PRIVATE_RANGE


class WindowsClipboard:
    def __init__(self):
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, res, args in [
            ("OpenClipboard", wintypes.BOOL, [wintypes.HWND]),
            ("CloseClipboard", wintypes.BOOL, []),
            ("EmptyClipboard", wintypes.BOOL, []),
            ("EnumClipboardFormats", wintypes.UINT, [wintypes.UINT]),
            ("GetClipboardData", wintypes.HANDLE, [wintypes.UINT]),
            ("SetClipboardData", wintypes.HANDLE, [wintypes.UINT, wintypes.HANDLE]),
            ("GetClipboardSequenceNumber", wintypes.DWORD, []),
            ("RegisterClipboardFormatW", wintypes.UINT, [wintypes.LPCWSTR]),
            ("CreateWindowExW", wintypes.HWND,
             [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
              wintypes.HINSTANCE, wintypes.LPVOID]),
            ("DestroyWindow", wintypes.BOOL, [wintypes.HWND]),
        ]:
            fn = getattr(user32, name)
            fn.restype, fn.argtypes = res, args
        for name, res, args in [
            ("GlobalAlloc", wintypes.HGLOBAL, [wintypes.UINT, ctypes.c_size_t]),
            ("GlobalFree", wintypes.HGLOBAL, [wintypes.HGLOBAL]),
            ("GlobalLock", wintypes.LPVOID, [wintypes.HGLOBAL]),
            ("GlobalUnlock", wintypes.BOOL, [wintypes.HGLOBAL]),
            ("GlobalSize", ctypes.c_size_t, [wintypes.HGLOBAL]),
        ]:
            fn = getattr(kernel32, name)
            fn.restype, fn.argtypes = res, args
        self._u, self._k = user32, kernel32
        self._hwnd = None
        self._privacy = [(user32.RegisterClipboardFormatW(n), d) for n, d in _PRIVACY_FORMATS]

    # -- helpers ---------------------------------------------------------
    def _open(self) -> None:
        # SetClipboardData fails after EmptyClipboard when the clipboard was
        # opened without a window, so each operation gets a message-only one.
        self._hwnd = self._u.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0,
                                             HWND_MESSAGE, None, None, None)
        for _ in range(OPEN_TRIES):  # another app may hold it for a moment
            if self._u.OpenClipboard(self._hwnd):
                return
            time.sleep(OPEN_WAIT)
        self._destroy_window()
        raise ClipboardError("the clipboard is busy")

    def _close(self) -> None:
        self._u.CloseClipboard()
        self._destroy_window()

    def _destroy_window(self) -> None:
        if self._hwnd:
            self._u.DestroyWindow(self._hwnd)
            self._hwnd = None

    def _put(self, fmt: int, data: bytes) -> None:
        k = self._k
        handle = k.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
        if not handle:
            raise ClipboardError("out of memory")
        ptr = k.GlobalLock(handle)
        if not ptr:
            k.GlobalFree(handle)
            raise ClipboardError("out of memory")
        ctypes.memmove(ptr, data, len(data))
        k.GlobalUnlock(handle)
        if not self._u.SetClipboardData(fmt, handle):  # on success Windows owns it
            k.GlobalFree(handle)

    def _write(self, items) -> None:
        self._open()
        try:
            if not self._u.EmptyClipboard():
                raise ClipboardError("the clipboard could not be cleared")
            for fmt, data in list(items) + [p for p in self._privacy if p[0]]:
                self._put(fmt, data)
        finally:
            self._close()

    # -- the interface PasteOutput uses ------------------------------------
    def sequence(self) -> int:
        return self._u.GetClipboardSequenceNumber()

    def save(self) -> list:
        u, k = self._u, self._k
        items = []
        self._open()
        try:
            fmt = u.EnumClipboardFormats(0)
            while fmt:
                handle = None if _skip(fmt) else u.GetClipboardData(fmt)
                size = k.GlobalSize(handle) if handle else 0
                ptr = k.GlobalLock(handle) if size else None
                if ptr:
                    try:
                        items.append((fmt, ctypes.string_at(ptr, size)))
                    finally:
                        k.GlobalUnlock(handle)
                fmt = u.EnumClipboardFormats(fmt)
        finally:
            self._close()
        return items

    def set_text(self, text: str) -> int:
        """Put ``text`` on the clipboard; returns the sequence number after it."""
        self._write([(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))])
        return self.sequence()

    def restore(self, items) -> None:
        """Put saved contents back (an empty list empties the clipboard)."""
        privacy = {fmt for fmt, _ in self._privacy}
        self._write([(fmt, data) for fmt, data in items if fmt not in privacy])

    def get_text(self):
        """The clipboard's text or None (tests and diagnostics)."""
        self._open()
        try:
            handle = self._u.GetClipboardData(CF_UNICODETEXT)
            ptr = self._k.GlobalLock(handle) if handle else None
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                self._k.GlobalUnlock(handle)
        finally:
            self._close()
