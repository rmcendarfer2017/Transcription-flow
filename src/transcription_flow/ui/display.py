"""DPI awareness and the screen's work area (review L3).

Without DPI awareness Windows stretches the pill as a bitmap at 125-200%
scaling, so it looks blurry. We declare the process system-DPI aware (what
Python's IDLE does for Tk) and scale our own drawing by the DPI instead.
The work area is the screen minus the taskbar, wherever the taskbar is.
"""

import ctypes
import sys

PROCESS_SYSTEM_DPI_AWARE = 1
SPI_GETWORKAREA = 0x0030
PILL_MARGIN = 24  # pixels above the bottom of the work area, at 100%


def enable_dpi_awareness(windll=None) -> str:
    """Call before the first window is created. Returns what worked."""
    if windll is None:
        if sys.platform != "win32":
            return "not windows"
        windll = ctypes.windll
    try:
        windll.shcore.SetProcessDpiAwareness(PROCESS_SYSTEM_DPI_AWARE)  # Windows 8.1+
        return "system"
    except (AttributeError, OSError):
        pass
    try:
        windll.user32.SetProcessDPIAware()  # Vista+
        return "legacy"
    except (AttributeError, OSError):
        return "unaware"


def work_area(windll=None):
    """(left, top, right, bottom) of the primary screen without the taskbar,
    or None when unknown (then the whole screen is used)."""
    if windll is None:
        if sys.platform != "win32":
            return None
        windll = ctypes.windll
    from ctypes import wintypes

    rect = wintypes.RECT()
    try:
        ok = windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    except (AttributeError, OSError):
        return None
    if not ok or rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def pill_position(size, screen, area=None, scale=1.0):
    """Top-left corner for a window of ``size`` (w, h): centred, just above
    the bottom of the work area (or of the ``screen`` (w, h) without one)."""
    width, height = size
    left, top, right, bottom = area or (0, 0, screen[0], screen[1])
    x = left + (right - left - width) // 2
    y = bottom - height - round(PILL_MARGIN * scale)
    return x, max(y, top)
