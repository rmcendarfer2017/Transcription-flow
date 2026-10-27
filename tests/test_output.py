"""Type and paste output; the Windows clipboard on windows-latest only."""

import sys

import pytest

from transcription_flow import clipboard as clip_mod
from transcription_flow.output import (OutputError, PasteOutput, PynputKeys, TypeOutput,
                                       make_output)


class FakeKeys:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    def type(self, text):
        self.log.append(("type", text))

    def paste(self):
        self.log.append("ctrl+v")
        if self.fail:
            raise RuntimeError("SendInput failed")


class FakeClipboard:
    def __init__(self, log, contents=(("text", b"theirs"),)):
        self.log = log
        self.contents = list(contents)
        self.seq = 7
        self.change_during_paste = False

    def save(self):
        self.log.append("save")
        return list(self.contents)

    def set_text(self, text):
        self.log.append(("set", text))
        self.contents = [("ours", text)]
        self.seq += 1
        return self.seq

    def sequence(self):
        if self.change_during_paste:
            self.seq += 1
        return self.seq

    def restore(self, items):
        self.log.append(("restore", items))
        self.contents = list(items)


def paste_parts(**kw):
    log = []
    clip = FakeClipboard(log)
    out = PasteOutput(clipboard=clip, keys=FakeKeys(log, **kw), sleep=lambda s: log.append(
        ("sleep", s)), settle=0.4)
    return out, clip, log


def test_type_mode_types_and_never_touches_the_clipboard():
    log = []
    TypeOutput(keys=FakeKeys(log)).emit("Hello world.")
    assert log == [("type", "Hello world.")]


def test_paste_saves_pastes_waits_then_restores():
    out, clip, log = paste_parts()
    out.emit("Hello world.")
    assert log == ["save", ("set", "Hello world."), "ctrl+v", ("sleep", 0.4),
                   ("restore", [("text", b"theirs")])]
    assert clip.contents == [("text", b"theirs")]


def test_paste_does_not_overwrite_a_copy_made_meanwhile():
    out, clip, log = paste_parts()
    clip.change_during_paste = True
    out.emit("Hello")
    assert not any(isinstance(e, tuple) and e[0] == "restore" for e in log)


def test_paste_restores_even_when_ctrl_v_fails():
    out, clip, log = paste_parts(fail=True)
    with pytest.raises(RuntimeError):
        out.emit("Hello")
    assert log[-1] == ("restore", [("text", b"theirs")])


def test_paste_with_an_empty_clipboard_leaves_it_empty():
    out, clip, log = paste_parts()
    clip.contents = []
    out.emit("Hello")
    assert clip.contents == []  # the dictation does not linger


def test_make_output_picks_the_mode(monkeypatch):
    assert isinstance(make_output("type"), TypeOutput)
    monkeypatch.setattr(clip_mod, "WindowsClipboard", lambda: FakeClipboard([]))
    assert isinstance(make_output("paste"), PasteOutput)


# -- pynput keys ------------------------------------------------------------

class FakeController:
    def __init__(self, log, error=None):
        self.log, self.error = log, error

    def type(self, text):
        if self.error:
            raise self.error
        self.log.append(("type", text))

    def pressed(self, key):
        log = self.log

        class Ctx:
            def __enter__(self):
                log.append(("down", key))

            def __exit__(self, *exc):
                log.append(("up", key))
        return Ctx()

    def press(self, key):
        self.log.append(("press", key))

    def release(self, key):
        self.log.append(("release", key))


class FakeKeyCode:
    @staticmethod
    def from_vk(vk):
        return f"vk{vk:#x}"


class FakeKb:
    def __init__(self, log, error=None):
        self.log, self.error = log, error
        self.KeyCode = FakeKeyCode
        self.Key = type("Key", (), {"ctrl": "ctrl"})

    def Controller(self):  # noqa: N802
        return FakeController(self.log, self.error)


def test_ctrl_v_is_sent_by_key_code_so_any_layout_pastes():
    log = []
    PynputKeys(keyboard_module=FakeKb(log)).paste()
    assert log == [("down", "ctrl"), ("press", "vk0x56"), ("release", "vk0x56"), ("up", "ctrl")]


def test_typing_error_carries_no_text():
    class InvalidCharacterException(Exception):
        pass

    keys = PynputKeys(keyboard_module=FakeKb([], InvalidCharacterException(3, "S")))
    with pytest.raises(OutputError) as info:
        keys.type("SECRET-DICTATION")
    assert "SECRET" not in str(info.value) and "S'" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


# -- the real clipboard (Windows CI) ------------------------------------------

def test_handle_formats_are_skipped():
    assert clip_mod._skip(2) and clip_mod._skip(14) and clip_mod._skip(0x0300)
    assert not clip_mod._skip(clip_mod.CF_UNICODETEXT) and not clip_mod._skip(0xC123)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard")
def test_windows_clipboard_round_trip():
    clip = clip_mod.WindowsClipboard()
    clip.set_text("before é")
    saved = clip.save()
    ours = clip.set_text("dictated text")
    assert clip.get_text() == "dictated text" and clip.sequence() == ours
    clip.restore(saved)
    assert clip.get_text() == "before é"
    clip.restore([])
    assert clip.get_text() is None
