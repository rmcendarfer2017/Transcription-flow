"""The tray icon (pystray): a status line, Pause, Settings, Open log folder, Quit.

pystray runs its own message-loop thread, so its menu callbacks run there.
They only call the ``actions`` they were given, and ``App`` hands Tk work
to the Tk thread itself (review L2).
"""

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

APP_NAME = "Transcription Flow"
ICON_SIZE = 64


@dataclass
class TrayActions:
    toggle_pause: object
    open_settings: object
    open_logs: object
    quit: object


def make_icon(paused: bool = False, size: int = ICON_SIZE):
    """The pill with a sound wave, drawn with Pillow (no binary asset yet;
    M4 adds the .ico). Grey bars when dictation is paused."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    top, bottom = size * 0.25, size * 0.75
    draw.rounded_rectangle((0, top, size - 1, bottom), radius=(bottom - top) / 2,
                           fill=(0, 0, 0, 255))
    bar = (140, 140, 140, 255) if paused else (255, 255, 255, 255)
    heights = (0.25, 0.6, 1.0, 0.6, 0.25)
    gap = size * 0.6 / (len(heights) - 1)
    width = max(2, size // 16)
    half_max = (bottom - top) * 0.35
    for i, h in enumerate(heights):
        x = size * 0.2 + i * gap
        draw.line((x, size / 2 - half_max * h, x, size / 2 + half_max * h), fill=bar, width=width)
    return image


class Tray:
    def __init__(self, actions: TrayActions, pystray_module=None):
        self._actions = actions
        self._pystray = pystray_module
        self._icon = None
        self.status = "Starting"
        self.paused = False

    def menu_items(self):
        """(label, callback or None, checked) in menu order; None = separator."""
        a = self._actions
        return [
            (f"{APP_NAME}: {self.status}", None, None),
            None,
            ("Pause dictation", a.toggle_pause, self.paused),
            ("Settings…", a.open_settings, None),
            ("Open log folder", a.open_logs, None),
            None,
            ("Quit", a.quit, None),
        ]

    def _menu(self):
        ps = self._pystray
        items = []
        for entry in self.menu_items():
            if entry is None:
                items.append(ps.Menu.SEPARATOR)
                continue
            label, callback, checked = entry
            if callback is None:
                items.append(ps.MenuItem(label, None, enabled=False))
            else:
                items.append(ps.MenuItem(
                    label, lambda icon, item, cb=callback: cb(),
                    checked=None if checked is None else (lambda item, c=checked: c),
                    default=callback is self._actions.open_settings))
        return ps.Menu(*items)

    def start(self) -> None:
        if self._pystray is None:
            import pystray

            self._pystray = pystray
        self._icon = self._pystray.Icon("TranscriptionFlow", make_icon(self.paused),
                                        f"{APP_NAME}: {self.status}", self._menu())

        def setup(icon):
            icon.visible = True

        self._icon.run_detached(setup)

    def set_status(self, status: str, paused: bool = None) -> None:
        """Any thread. Rebuilds the menu, so call it only when the status changes."""
        changed_icon = paused is not None and paused != self.paused
        self.status = status
        if paused is not None:
            self.paused = paused
        icon = self._icon
        if icon is None:
            return
        icon.title = f"{APP_NAME}: {status}"[:127]  # the tooltip's limit
        if changed_icon:
            icon.icon = make_icon(self.paused)
        icon.menu = self._menu()  # the setter calls update_menu()

    def notify(self, message: str) -> None:
        icon = self._icon
        if icon is None or not getattr(icon, "HAS_NOTIFICATION", False):
            return
        try:
            icon.notify(message, APP_NAME)
        except Exception as err:  # a toast is nice to have, never fatal
            log.info("tray notification failed (%s)", type(err).__name__)

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None
