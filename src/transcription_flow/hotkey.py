"""Hold-to-talk hotkey.

``HotkeyTracker`` is the platform-free logic: it turns raw key down/up events
into press, release and "another key while held" (a shortcut such as
Right Ctrl+C, which cancels the recording). ``KeyboardHotkey`` feeds it from
the Windows-only ``keyboard`` library; M2 swaps that for pynput.
"""

import logging

log = logging.getLogger(__name__)

DEFAULT_HOTKEY = "right ctrl"


class HotkeyTracker:
    def __init__(self, on_press, on_release, on_other_key):
        self._on_press = on_press
        self._on_release = on_release
        self._on_other_key = on_other_key
        self.held = False

    def key_down(self, is_hotkey: bool) -> None:
        if is_hotkey:
            if not self.held:  # ignore auto-repeat
                self.held = True
                self._on_press()
        elif self.held:
            self._on_other_key()

    def key_up(self, is_hotkey: bool) -> None:
        if is_hotkey and self.held:
            self.held = False
            self._on_release()


class KeyboardHotkey:
    def __init__(self, hotkey: str = DEFAULT_HOTKEY, keyboard_module=None):
        self.hotkey = hotkey
        self._kb = keyboard_module
        self._hook = None

    def start(self, on_press, on_release, on_other_key) -> None:
        if self._kb is None:
            import keyboard

            self._kb = keyboard
        codes = set(self._kb.key_to_scan_codes(self.hotkey))
        tracker = HotkeyTracker(on_press, on_release, on_other_key)

        def handle(event):
            is_hotkey = event.scan_code in codes
            if event.event_type == "down":
                tracker.key_down(is_hotkey)
            elif event.event_type == "up":
                tracker.key_up(is_hotkey)

        self._hook = self._kb.hook(handle)

    def stop(self) -> None:
        if self._hook is not None:
            self._kb.unhook(self._hook)
            self._hook = None
