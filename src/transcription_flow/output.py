"""Text output: types the text into the focused window.

The clipboard is never touched (review M3): with Windows clipboard history or
cloud sync on, a copy would store or sync every dictation. A paste mode that
saves and restores the clipboard comes in M2 as an opt-in setting.
"""

import logging

log = logging.getLogger(__name__)


class KeyboardTyper:
    """Types with the Windows-only ``keyboard`` library (pynput in M2)."""

    def __init__(self, keyboard_module=None):
        self._kb = keyboard_module

    def emit(self, text: str) -> None:
        if self._kb is None:
            import keyboard

            self._kb = keyboard
        self._kb.write(text)
