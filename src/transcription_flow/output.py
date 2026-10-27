"""Text output into the focused window.

``type`` (the default) sends the text as key presses and never touches the
clipboard (review M3): with Windows clipboard history or cloud sync on, a
copy would store or sync every dictation. ``paste`` is an opt-in for apps
that drop typed keys (review M8): it saves the clipboard, puts the text on
it marked "not for history or cloud", presses Ctrl+V, then puts the old
contents back.

Exceptions raised here must not carry the text (the controller logs only the
type, but keep it that way at the source too).
"""

import logging
import time

from .clipboard import ClipboardError

log = logging.getLogger(__name__)

OUTPUT_MODES = {"type": "Type the text", "paste": "Paste, then restore the clipboard"}
DEFAULT_OUTPUT = "type"
# Time the app gets to read the clipboard after Ctrl+V before the user's
# contents go back. Apps under load (Teams, Word, Remote Desktop) can read it
# late and would then paste the old contents (review-m2 F7), so this is
# generous; config.json can change it (paste_settle_seconds).
PASTE_SETTLE_SECONDS = 0.75
VK_V = 0x56  # by key code, so Ctrl+V works on any keyboard layout


class OutputError(Exception):
    """Typing or pasting failed; the message never holds the text."""


class PynputKeys:
    """Typing and Ctrl+V through pynput's SendInput controller."""

    def __init__(self, keyboard_module=None):
        self._kb = keyboard_module
        self._controller = None

    def _ctl(self):
        if self._controller is None:
            if self._kb is None:
                from pynput import keyboard

                self._kb = keyboard
            self._controller = self._kb.Controller()
        return self._controller

    def type(self, text: str) -> None:
        try:
            self._ctl().type(text)
        except Exception as err:  # InvalidCharacterException carries a character
            raise OutputError(f"typing failed ({type(err).__name__})") from None

    def paste(self) -> None:
        ctl, kb = self._ctl(), self._kb
        v = kb.KeyCode.from_vk(VK_V)
        with ctl.pressed(kb.Key.ctrl):
            ctl.press(v)
            ctl.release(v)


class TypeOutput:
    def __init__(self, keys=None):
        self._keys = keys or PynputKeys()

    def emit(self, text: str) -> None:
        self._keys.type(text)


class PasteOutput:
    def __init__(self, clipboard=None, keys=None, sleep=time.sleep,
                 settle=PASTE_SETTLE_SECONDS):
        if clipboard is None:
            from .clipboard import WindowsClipboard

            clipboard = WindowsClipboard()
        self._clipboard = clipboard
        self._keys = keys or PynputKeys()
        self._sleep = sleep
        self._settle = settle

    def emit(self, text: str) -> None:
        clip = self._clipboard
        saved = clip.save()
        ours = None  # the sequence number once our write changed the clipboard
        try:
            ours = clip.set_text(text)
            self._keys.paste()
            self._sleep(self._settle)
        except ClipboardError as err:  # only set_text raises it
            # err.seq says if set_text changed the clipboard part way. A busy
            # clipboard was never touched, and another app may have written
            # to it meanwhile (review-m2 R2).
            ours = err.seq
            raise
        finally:
            # Put theirs back only while the clipboard is as we left it.
            if ours is not None:
                if clip.sequence() == ours:
                    clip.restore(saved)
                else:  # someone copied something meanwhile: theirs wins
                    log.info("clipboard changed during the paste; not restored")


def make_output(mode: str, settle: float = PASTE_SETTLE_SECONDS):
    return PasteOutput(settle=settle) if mode == "paste" else TypeOutput()
