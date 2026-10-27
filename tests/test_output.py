"""Type and paste output; the Windows clipboard on windows-latest only."""

import ctypes
import sys

import pytest

from transcription_flow import clipboard as clip_mod
from transcription_flow.output import (RESTORE_WARNING, OutputError, OutputWarning,
                                       PasteOutput, PynputKeys, TypeOutput, make_output)


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
        self.fail_set = None  # "untouched": fails before any change; "part way": after
        self.fail_restore = False

    def save(self):
        self.log.append("save")
        return list(self.contents)

    def set_text(self, text):
        self.log.append(("set", text))
        if self.fail_set == "untouched":
            raise clip_mod.ClipboardError("the clipboard is busy")
        if self.fail_set == "part way":
            self.contents = [("privacy", b"\0\0\0\0")]
            self.seq += 1
            err = clip_mod.ClipboardError("the clipboard did not take the data")
            err.seq = self.seq  # as WindowsClipboard._write reports a change it made
            raise err
        self.contents = [("ours", text)]
        self.seq += 1
        return self.seq

    def sequence(self):
        if self.change_during_paste:
            self.seq += 1
        return self.seq

    def restore(self, items):
        self.log.append(("restore", items))
        self.contents = list(items[1:] if self.fail_restore else items)
        if self.fail_restore:
            raise clip_mod.ClipboardError(f"1 of {len(items)} formats could not be put back")


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


def test_paste_restores_when_set_text_fails_part_way():
    """review-m2 F2: set_text is inside the try, so the user's copy comes back."""
    out, clip, log = paste_parts()
    clip.fail_set = "part way"
    with pytest.raises(clip_mod.ClipboardError):
        out.emit("Hello")
    assert "ctrl+v" not in log and log[-1] == ("restore", [("text", b"theirs")])
    assert clip.contents == [("text", b"theirs")]


def test_paste_after_a_part_way_failure_keeps_a_copy_made_meanwhile():
    out, clip, log = paste_parts()
    clip.fail_set = "part way"
    clip.change_during_paste = True  # another app wrote after our failed write
    with pytest.raises(clip_mod.ClipboardError):
        out.emit("Hello")
    assert not any(isinstance(e, tuple) and e[0] == "restore" for e in log)


def test_paste_leaves_an_untouched_clipboard_alone_when_set_text_fails():
    out, clip, log = paste_parts()
    clip.fail_set = "untouched"
    with pytest.raises(clip_mod.ClipboardError):
        out.emit("Hello")
    assert log == ["save", ("set", "Hello")]  # no Ctrl+V, no needless rewrite


def test_a_restore_error_after_a_good_paste_is_a_warning_not_a_failure(caplog):
    """review-m2 N2: the text went out, so the user must not dictate again."""
    out, clip, log = paste_parts()
    clip.contents = [("html", b"<b>theirs</b>"), ("text", b"theirs")]
    clip.fail_restore = True
    with pytest.raises(OutputWarning) as caught:
        out.emit("Hello")
    assert str(caught.value) == RESTORE_WARNING
    assert not isinstance(caught.value, clip_mod.ClipboardError)
    assert "ctrl+v" in log and clip.contents == [("text", b"theirs")]
    assert "clipboard not fully restored: 1 of 2" in caplog.text


def test_a_restore_error_never_hides_the_paste_error(caplog):
    out, clip, log = paste_parts(fail=True)
    clip.fail_restore = True
    with pytest.raises(RuntimeError, match="SendInput failed"):
        out.emit("Hello")
    assert "clipboard not fully restored" in caplog.text


def test_paste_with_an_empty_clipboard_leaves_it_empty():
    out, clip, log = paste_parts()
    clip.contents = []
    out.emit("Hello")
    assert clip.contents == []  # the dictation does not linger


def test_make_output_picks_the_mode(monkeypatch):
    assert isinstance(make_output("type"), TypeOutput)
    monkeypatch.setattr(clip_mod, "WindowsClipboard", lambda: FakeClipboard([]))
    assert isinstance(make_output("paste"), PasteOutput)
    assert make_output("paste", settle=2.0)._settle == 2.0


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


# -- WindowsClipboard over a fake clipboard API (any platform) ------------------------

class FakeKernel32:
    """Global memory as real ctypes buffers, so memmove/string_at work."""

    def __init__(self, fail_alloc=False):
        self.blocks, self.fail_alloc = {}, fail_alloc  # ours to free
        self.owned = {}  # the clipboard's (GetClipboardData), never ours to free

    def GlobalAlloc(self, flags, size):  # noqa: N802
        if self.fail_alloc:
            return 0
        buf = ctypes.create_string_buffer(size)
        self.blocks[ctypes.addressof(buf)] = buf
        return ctypes.addressof(buf)

    def GlobalLock(self, handle):  # noqa: N802
        return handle if handle in self.blocks or handle in self.owned else 0

    def GlobalUnlock(self, handle):  # noqa: N802
        return 1

    def GlobalSize(self, handle):  # noqa: N802
        return len({**self.owned, **self.blocks}[handle])

    def GlobalFree(self, handle):  # noqa: N802
        del self.blocks[handle]
        return 0


class FakeUser32:
    def __init__(self, kernel, refuse=(), register_fails=()):
        self.k, self.refuse, self.register_fails = kernel, set(refuse), set(register_fails)
        self.names, self.data, self.seq = {}, {}, 1
        self.opens, self.puts, self.is_open = 0, [], False

    def RegisterClipboardFormatW(self, name):  # noqa: N802
        if name in self.register_fails:
            return 0
        return self.names.setdefault(name, 0xC000 + len(self.names))

    def fmt(self, name):
        return self.names[name]

    def CreateWindowExW(self, *args):  # noqa: N802
        return 99

    def DestroyWindow(self, hwnd):  # noqa: N802
        return 1

    def OpenClipboard(self, hwnd):  # noqa: N802
        assert hwnd == 99 and not self.is_open
        self.opens += 1
        self.is_open = True
        return 1

    def CloseClipboard(self):  # noqa: N802
        self.is_open = False
        return 1

    def EmptyClipboard(self):  # noqa: N802
        assert self.is_open
        self.data, self.seq = {}, self.seq + 1
        return 1

    def SetClipboardData(self, fmt, handle):  # noqa: N802
        assert self.is_open
        self.puts.append(fmt)
        if fmt in self.refuse:
            return 0
        self.data[fmt] = ctypes.string_at(handle, self.k.GlobalSize(handle))
        del self.k.blocks[handle]  # Windows owns it now
        self.seq += 1
        return handle

    def EnumClipboardFormats(self, fmt):  # noqa: N802
        order = list(self.data)
        i = order.index(fmt) + 1 if fmt else 0
        return order[i] if i < len(order) else 0

    def GetClipboardData(self, fmt):  # noqa: N802
        buf = ctypes.create_string_buffer(self.data[fmt], len(self.data[fmt]))
        self.k.owned[ctypes.addressof(buf)] = buf
        return ctypes.addressof(buf)

    def GetClipboardSequenceNumber(self):  # noqa: N802
        return self.seq


PRIVACY = ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
           "CanUploadToCloudClipboard")
CF_HTML = 0xC0FF


def fake_clipboard(refuse_names=(), **kw):
    kernel = FakeKernel32(**{k: v for k, v in kw.items() if k == "fail_alloc"})
    user = FakeUser32(kernel, **{k: v for k, v in kw.items() if k == "register_fails"})
    clip = clip_mod.WindowsClipboard(user32=user, kernel32=kernel)
    user.refuse = {user.fmt(n) if isinstance(n, str) else n for n in refuse_names}
    return clip, user, kernel


def test_our_text_is_marked_not_for_history_or_cloud_before_it_goes_on():
    """review-m2 F2: the drop-the-markers mutant must die here, not only on Windows."""
    clip, user, kernel = fake_clipboard()
    ours = clip.set_text("dictated é")
    assert ours == user.seq and user.opens == 1  # one open: no window without markers
    for name in PRIVACY:
        assert user.data[user.fmt(name)] == b"\0\0\0\0"  # DWORD 0
    assert user.data[clip_mod.CF_UNICODETEXT] == "dictated é\0".encode("utf-16-le")
    assert user.puts[-1] == clip_mod.CF_UNICODETEXT  # the text goes last
    assert set(user.puts[:-1]) == {user.fmt(n) for n in PRIVACY}
    assert kernel.blocks == {} and not user.is_open


@pytest.mark.parametrize("refused", PRIVACY)
def test_a_marker_the_clipboard_refuses_stops_before_the_text(refused):
    clip, user, kernel = fake_clipboard(refuse_names=[refused])
    with pytest.raises(clip_mod.ClipboardError):
        clip.set_text("dictated")
    assert clip_mod.CF_UNICODETEXT not in user.data and clip_mod.CF_UNICODETEXT not in user.puts
    assert kernel.blocks == {} and not user.is_open  # handle freed, clipboard closed


def test_text_the_clipboard_refuses_raises_instead_of_pasting_nothing():
    clip, user, kernel = fake_clipboard(refuse_names=[clip_mod.CF_UNICODETEXT])
    with pytest.raises(clip_mod.ClipboardError, match="did not take"):
        clip.set_text("dictated")
    assert clip_mod.CF_UNICODETEXT not in user.data and kernel.blocks == {}


def test_out_of_memory_raises():
    clip, user, kernel = fake_clipboard(fail_alloc=True)
    with pytest.raises(clip_mod.ClipboardError, match="out of memory"):
        clip.set_text("dictated")
    assert user.data == {}


def test_no_text_without_the_markers_if_a_format_cannot_be_registered():
    clip, user, kernel = fake_clipboard(register_fails=["CanIncludeInClipboardHistory"])
    with pytest.raises(clip_mod.ClipboardError):
        clip.set_text("dictated")
    assert user.data == {} and user.opens == 0


def test_restore_puts_every_saved_format_back_as_it_was():
    """QA B5: no markers of ours on the user's own contents."""
    clip, user, kernel = fake_clipboard()
    theirs = {clip_mod.CF_UNICODETEXT: "theirs\0".encode("utf-16-le"),
              CF_HTML: b"Version:0.9\r\n<b>theirs</b>"}
    user.data = dict(theirs)
    saved = clip.save()
    clip.set_text("dictated")
    clip.restore(saved)
    assert user.data == theirs
    assert not {user.fmt(n) for n in PRIVACY} & set(user.data)
    assert kernel.blocks == {}


def test_restore_keeps_markers_the_user_had():
    clip, user, kernel = fake_clipboard()
    history = user.fmt("CanIncludeInClipboardHistory")
    user.data = {clip_mod.CF_UNICODETEXT: "pw\0".encode("utf-16-le"), history: b"\0\0\0\0"}
    saved = clip.save()
    clip.set_text("dictated")
    clip.restore(saved)
    assert user.data == dict(saved) and history in user.data


def test_restore_puts_back_every_format_it_can_when_one_is_refused():
    """review-m2 R1: a refused app format must not take the user's text with it."""
    clip, user, kernel = fake_clipboard()
    text = "theirs\0".encode("utf-16-le")
    user.data = {CF_HTML: b"<b>x</b>", clip_mod.CF_UNICODETEXT: text, 0xC0FE: b"app"}
    saved = clip.save()
    clip.set_text("dictated")
    user.refuse = {CF_HTML}
    with pytest.raises(clip_mod.ClipboardError, match=r"1 of 3 formats .*0xc0ff"):
        clip.restore(saved)
    assert user.data == {clip_mod.CF_UNICODETEXT: text, 0xC0FE: b"app"}
    assert user.puts[-3:] == [CF_HTML, clip_mod.CF_UNICODETEXT, 0xC0FE]  # one open, in order
    assert kernel.blocks == {} and not user.is_open


def test_set_text_reports_a_change_it_made_and_a_busy_clipboard_reports_none():
    clip, user, kernel = fake_clipboard(refuse_names=[clip_mod.CF_UNICODETEXT])
    with pytest.raises(clip_mod.ClipboardError) as info:
        clip.set_text("dictated")
    assert info.value.seq == user.seq  # emptied, markers on: changed by us
    clip, user, kernel = fake_clipboard()
    user.EmptyClipboard = lambda: 0
    with pytest.raises(clip_mod.ClipboardError, match="could not be cleared") as info:
        clip.set_text("dictated")
    assert info.value.seq is None


def test_a_busy_clipboard_keeps_another_apps_newer_copy(monkeypatch):
    """review-m2 R2: busy usually means another app is writing. Its copy, made
    while set_text waited, must not be overwritten with the older one."""
    monkeypatch.setattr(clip_mod, "OPEN_WAIT", 0)
    clip, user, kernel = fake_clipboard()
    user.data = {clip_mod.CF_UNICODETEXT: "old\0".encode("utf-16-le")}
    real_open, opens = user.OpenClipboard, []

    def busy_open(hwnd):
        opens.append(hwnd)
        if len(opens) == 1:
            return real_open(hwnd)  # save() gets in
        if len(opens) == 2:  # the other app's write lands while we wait
            user.data = {clip_mod.CF_UNICODETEXT: "new copy\0".encode("utf-16-le")}
            user.seq += 2
        return 0 if len(opens) <= 1 + clip_mod.OPEN_TRIES else real_open(hwnd)

    user.OpenClipboard = busy_open
    log = []
    out = PasteOutput(clipboard=clip, keys=FakeKeys(log), sleep=lambda s: None)
    with pytest.raises(clip_mod.ClipboardError, match="busy"):
        out.emit("dictated")
    assert log == [] and user.data == {clip_mod.CF_UNICODETEXT: "new copy\0".encode("utf-16-le")}


def test_paste_end_to_end_keeps_theirs_when_our_text_is_refused():
    clip, user, kernel = fake_clipboard(refuse_names=[clip_mod.CF_UNICODETEXT])
    theirs = {CF_HTML: b"<i>copied</i>"}
    user.data = dict(theirs)
    log = []
    out = PasteOutput(clipboard=clip, keys=FakeKeys(log), sleep=lambda s: None)
    with pytest.raises(clip_mod.ClipboardError):
        out.emit("dictated")
    assert log == [] and user.data == theirs  # no Ctrl+V; their copy is back


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard")
def test_windows_clipboard_round_trip():
    clip = clip_mod.WindowsClipboard()
    clip.restore([(clip_mod.CF_UNICODETEXT, "before é\0".encode("utf-16-le"))])
    saved = clip.save()
    ours = clip.set_text("dictated text")
    assert clip.get_text() == "dictated text" and clip.sequence() == ours
    markers = {fmt: data for fmt, data in clip.save() if fmt in dict(clip._privacy)}
    assert markers == dict(clip._privacy)  # each marker on the clipboard, DWORD 0
    clip.restore(saved)
    assert clip.get_text() == "before é"
    assert not set(dict(clip._privacy)) & {fmt for fmt, _ in clip.save()}  # QA B5
    clip.restore([])
    assert clip.get_text() is None
