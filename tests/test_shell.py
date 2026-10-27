"""The Windows shell with fakes: tray, display helpers, settings form, App."""

import dataclasses
import sys

import pytest

from transcription_flow import config
from transcription_flow.app import App
from transcription_flow.config import Config
from transcription_flow.hotkey import HotkeyUnavailable
from transcription_flow.ui import display
from transcription_flow.ui.settings import Form, hotkey_choices, model_choices
from transcription_flow.ui.tray import Tray, TrayActions, make_icon

# -- display --------------------------------------------------------------------


def test_pill_sits_centred_above_the_bottom_of_the_work_area():
    assert display.pill_position((75, 27), (1920, 1080), (0, 0, 1920, 1032)) == (922, 981)


def test_pill_follows_a_taskbar_on_the_left_and_the_scale():
    x, y = display.pill_position((150, 54), (2880, 1620), (100, 0, 2880, 1620), scale=2.0)
    assert x == 100 + (2780 - 150) // 2 and y == 1620 - 54 - 48


def test_pill_without_a_work_area_uses_the_screen():
    assert display.pill_position((75, 27), (1280, 720)) == (602, 669)


def test_pill_never_goes_above_the_top():
    assert display.pill_position((75, 500), (800, 400), (0, 10, 800, 400))[1] == 10


class Fn:
    def __init__(self, log, name, error=None, result=1):
        self.log, self.name, self.error, self.result = log, name, error, result

    def __call__(self, *args):
        self.log.append((self.name, args))
        if self.error:
            raise self.error
        return self.result


class Lib:
    def __init__(self, **fns):
        self.__dict__.update(fns)


def test_dpi_awareness_prefers_shcore_system_aware():
    log = []
    windll = Lib(shcore=Lib(SetProcessDpiAwareness=Fn(log, "shcore")),
                 user32=Lib(SetProcessDPIAware=Fn(log, "user32")))
    assert display.enable_dpi_awareness(windll) == "system"
    assert log == [("shcore", (1,))]


def test_dpi_awareness_falls_back_to_the_legacy_call():
    log = []
    windll = Lib(shcore=Lib(SetProcessDpiAwareness=Fn(log, "shcore", OSError("no shcore"))),
                 user32=Lib(SetProcessDPIAware=Fn(log, "user32")))
    assert display.enable_dpi_awareness(windll) == "legacy"
    assert [name for name, _ in log] == ["shcore", "user32"]


def test_dpi_awareness_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert display.enable_dpi_awareness() == "not windows"
    assert display.work_area() is None


def test_work_area_reads_the_rect():
    def spi(action, param, rect_ref, flags):
        assert action == display.SPI_GETWORKAREA
        rect = rect_ref._obj
        rect.left, rect.top, rect.right, rect.bottom = 0, 0, 1920, 1032
        return 1

    assert display.work_area(Lib(user32=Lib(SystemParametersInfoW=spi))) == (0, 0, 1920, 1032)


def test_work_area_failure_gives_none():
    windll = Lib(user32=Lib(SystemParametersInfoW=lambda *a: 0))
    assert display.work_area(windll) is None


# -- tray -----------------------------------------------------------------------

class FakeMenuItem:
    def __init__(self, text, action, checked=None, enabled=True, default=False):
        self.text, self.action, self.checked, self.enabled, self.default = (
            text, action, checked, enabled, default)


class FakeMenu:
    SEPARATOR = "----"

    def __init__(self, *items):
        self.items = items


class FakeIcon:
    HAS_NOTIFICATION = True

    def __init__(self, name, icon, title, menu):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self.visible = False
        self.notes = []
        self.stopped = False

    def run_detached(self, setup):
        setup(self)

    def notify(self, message, title):
        self.notes.append((title, message))

    def stop(self):
        self.stopped = True


class FakePystray:
    Menu, MenuItem, Icon = FakeMenu, FakeMenuItem, FakeIcon


@pytest.fixture
def tray():
    calls = []
    actions = TrayActions(*(lambda n=n: calls.append(n) for n in
                            ("pause", "settings", "logs", "quit")))
    t = Tray(actions, pystray_module=FakePystray())
    t.start()
    return t, calls


def labels(menu):
    return [i if i == FakeMenu.SEPARATOR else i.text for i in menu.items]


def test_tray_menu_order_and_status_line(tray):
    t, calls = tray
    icon = t._icon
    assert icon.visible and icon.title == "Transcription Flow: Starting"
    assert labels(icon.menu) == ["Transcription Flow: Starting", "----", "Pause dictation",
                                 "Settings…", "Open log folder", "----", "Quit"]
    status = icon.menu.items[0]
    assert status.enabled is False and status.action is None


def test_tray_items_call_their_actions_and_settings_is_the_default(tray):
    t, calls = tray
    items = [i for i in t._icon.menu.items if i != FakeMenu.SEPARATOR and i.action]
    for item in items:
        item.action(t._icon, item)
    assert calls == ["pause", "settings", "logs", "quit"]
    assert [i.text for i in items if i.default] == ["Settings…"]


def test_tray_status_change_updates_tooltip_menu_and_pause_check(tray):
    t, calls = tray
    t.set_status("Paused", paused=True)
    icon = t._icon
    assert icon.title == "Transcription Flow: Paused"
    assert icon.menu.items[0].text == "Transcription Flow: Paused"
    pause = icon.menu.items[2]
    assert pause.checked(pause) is True


def test_tray_tooltip_is_cut_to_the_windows_limit(tray):
    t, _ = tray
    t.set_status("Error: " + "x" * 300)
    assert len(t._icon.title) == 127


def test_tray_notify_and_stop(tray):
    t, _ = tray
    t.notify("Ready. Hold Right Ctrl to dictate.")
    icon = t._icon
    assert icon.notes == [("Transcription Flow", "Ready. Hold Right Ctrl to dictate.")]
    t.stop()
    assert icon.stopped and t._icon is None
    t.notify("after stop")  # no crash


def test_icon_images():
    pytest.importorskip("PIL")
    on, off = make_icon(), make_icon(paused=True)
    assert on.size == (64, 64) and on.mode == "RGBA"
    assert on.getpixel((0, 0))[3] == 0  # transparent corner
    assert on.getpixel((32, 32)) == (255, 255, 255, 255)  # the middle bar
    assert off.getpixel((32, 32)) == (140, 140, 140, 255)


# -- settings form ----------------------------------------------------------------

def test_form_round_trip():
    c = Config(hotkey="f9", model="base.en", language="en", output="paste", keep_mic_warm=True)
    form = Form.from_config(c, start_with_windows=True)
    assert form.start_with_windows and form.to_config() == c


def test_form_fixes_a_language_the_model_cannot_use():
    form = Form("f9", "base.en", "de", "type", False, False)
    assert form.to_config().language == "en"


def test_model_choices_are_sized_and_mark_the_default():
    labels_ = model_choices()
    assert list(labels_) == ["tiny.en", "base.en", "small.en"]  # smallest first
    assert labels_["small.en"].endswith("MB, recommended)")
    assert "recommended" not in labels_["tiny.en"]


def test_hotkey_choices_are_the_hotkey_table():
    choices = hotkey_choices()
    assert choices["right_ctrl"] == "Right Ctrl" and len(set(choices.values())) == len(choices)


# -- App ----------------------------------------------------------------------------

class FakeController:
    def __init__(self):
        self.paused = False
        self.calls = []

    def set_paused(self, paused):
        self.paused = paused
        self.calls.append(("paused", paused))

    def set_output(self, output):
        self.calls.append(("output", output))

    def set_keep_warm(self, on):
        self.calls.append(("keep_warm", on))


class FakeHotkey:
    def __init__(self):
        self.calls = []
        self.fail_for = set()

    def start(self, key_id):
        if "start" in self.fail_for:
            raise HotkeyUnavailable("no")
        self.calls.append(("start", key_id))

    def set_key(self, key_id):
        if key_id in self.fail_for:
            raise OSError("hook failed")
        self.calls.append(("set_key", key_id))

    def stop(self):
        self.calls.append("stop")


class FakeTray:
    def __init__(self, actions):
        self.actions = actions
        self.statuses = []
        self.notes = []
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def set_status(self, status, paused=None):
        self.statuses.append((status, paused))

    def notify(self, message):
        self.notes.append(message)

    def stop(self):
        self.stopped = True


class FakeRunKey:
    def __init__(self, available=True, enabled=False):
        self.available, self.enabled = available, enabled
        self.calls = []

    def is_enabled(self):
        return self.enabled

    def enable(self, command):
        self.calls.append(("enable", command))
        self.enabled = True

    def disable(self):
        self.calls.append("disable")
        self.enabled = False


class FakeSettings:
    def __init__(self, form, available, on_save, on_close):
        self.form, self.available, self.on_save, self.on_close = form, available, on_save, on_close
        self.lifted = 0

    def lift(self):
        self.lifted += 1

    def close(self):
        self.on_close()


@pytest.fixture
def shell(tmp_path):
    parts = {"ctl": FakeController(), "hk": FakeHotkey(), "run": FakeRunKey(), "ui": [],
             "stopped": [], "opened": [], "windows": [], "path": tmp_path / "config.json"}

    def settings_factory(*args):
        window = FakeSettings(*args)
        parts["windows"].append(window)
        return window

    a = App(Config(), parts["path"], parts["ctl"], parts["hk"], FakeTray, parts["run"],
            ui_call=lambda fn: (parts["ui"].append(fn), fn()), stop_ui=lambda: parts[
                "stopped"].append(True), settings_factory=settings_factory,
            output_factory=lambda mode: f"output:{mode}", launch_command=lambda: '"tf.exe" --tray',
            log_dir=tmp_path / "logs", opener=parts["opened"].append, device="cpu")
    parts["tray"] = a._tray
    return a, parts


def test_start_installs_the_hotkey_starts_the_tray_and_says_ready(shell):
    a, p = shell
    a.start()
    assert p["hk"].calls == [("start", "right_ctrl")] and p["tray"].started
    assert p["tray"].statuses[-1] == ("Ready (CPU)", False)
    assert p["tray"].notes == ["Ready. Hold Right Ctrl to dictate."]


def test_start_without_a_hotkey_raises_before_the_tray(shell):
    a, p = shell
    p["hk"].fail_for.add("start")
    with pytest.raises(HotkeyUnavailable):
        a.start()
    assert not p["tray"].started


def test_tray_actions_run_on_the_ui_thread(shell):
    a, p = shell
    actions = p["tray"].actions
    actions.toggle_pause()
    actions.open_logs()
    assert len(p["ui"]) == 2  # both went through ui_call
    assert p["ctl"].paused and p["tray"].statuses[-1] == ("Paused", True)
    assert p["opened"] == [a._log_dir]


def test_pause_toggles_back_to_ready(shell):
    a, p = shell
    a.toggle_pause()
    a.toggle_pause()
    assert not p["ctl"].paused and p["tray"].statuses[-1] == ("Ready (CPU)", False)


def test_errors_show_in_the_status_and_a_toast_until_the_next_good_action(shell):
    a, p = shell
    a.report_error("The microphone sent only silence.")
    assert p["tray"].statuses[-1][0] == "Error: The microphone sent only silence."
    assert p["tray"].notes == ["The microphone sent only silence."]
    a.toggle_pause()
    a.toggle_pause()
    assert p["tray"].statuses[-1][0] == "Ready (CPU)"


def test_a_new_recording_clears_the_error_line(shell):
    a, p = shell
    a.report_error("No microphone found.")
    a.on_state("transcribing")
    assert p["tray"].statuses[-1][0] == "Error: No microphone found."
    a.on_state("recording")
    assert p["tray"].statuses[-1][0] == "Ready (CPU)"
    calls = len(p["ui"])
    a.on_state("recording")  # no error: nothing to do
    assert len(p["ui"]) == calls


def test_settings_open_once_and_get_the_current_values(shell):
    a, p = shell
    p["run"].enabled = True
    a.open_settings()
    a.open_settings()
    assert len(p["windows"]) == 1 and p["windows"][0].lifted == 1
    w = p["windows"][0]
    assert w.form == Form.from_config(Config(), True) and w.available is True
    w.close()
    a.open_settings()
    assert len(p["windows"]) == 2


def save(a, p, **changes):
    a.open_settings()
    window = p["windows"][-1]
    form = dataclasses.replace(window.form, **changes)
    window.close()
    window.on_save(form)


def test_saving_unchanged_settings_changes_nothing_live(shell):
    a, p = shell
    save(a, p)
    assert p["hk"].calls == [] and p["ctl"].calls == [] and p["run"].calls == []
    assert config.load(p["path"]) == Config()


def test_saving_applies_each_change_live_and_writes_the_file(shell):
    a, p = shell
    save(a, p, hotkey="f9", output="paste", keep_mic_warm=True, start_with_windows=True)
    assert p["hk"].calls == [("set_key", "f9")]
    assert p["ctl"].calls == [("output", "output:paste"), ("keep_warm", True)]
    assert p["run"].calls == [("enable", '"tf.exe" --tray')]
    assert config.load(p["path"]) == Config(hotkey="f9", output="paste", keep_mic_warm=True)
    assert a.config.hotkey == "f9"


def test_a_model_change_is_saved_for_the_next_start_only(shell):
    a, p = shell
    save(a, p, model="base.en")
    assert config.load(p["path"]).model == "base.en"
    assert p["ctl"].calls == [] and p["hk"].calls == []


def test_autostart_off_deletes_the_value_and_on_rewrites_it(shell):
    a, p = shell
    p["run"].enabled = True
    save(a, p, start_with_windows=True)  # rewritten, so a moved install fixes itself
    save(a, p, start_with_windows=False)
    assert p["run"].calls == [("enable", '"tf.exe" --tray'), "disable"]


def test_autostart_unavailable_is_left_alone(shell):
    a, p = shell
    p["run"].available = False
    save(a, p, start_with_windows=True)
    assert p["run"].calls == []


def test_a_hotkey_that_fails_keeps_the_old_one(shell):
    a, p = shell
    p["hk"].fail_for.add("f9")
    save(a, p, hotkey="f9", output="paste")
    assert p["hk"].calls == [("set_key", "right_ctrl")]  # put back
    assert config.load(p["path"]).hotkey == "right_ctrl"
    assert config.load(p["path"]).output == "paste"  # the other change still applies
    assert "The hotkey could not be set." in p["tray"].notes


def test_a_settings_file_that_cannot_be_written_is_reported(shell, tmp_path):
    a, p = shell
    a._config_path = tmp_path / "not-a-dir"
    a._config_path.write_text("x")
    a._config_path = a._config_path / "config.json"  # parent is a file
    save(a, p, output="paste")
    assert "The settings could not be saved." in p["tray"].notes


def test_quit_stops_hotkey_tray_settings_and_the_ui(shell):
    a, p = shell
    a.start()
    a.open_settings()
    a.quit()
    assert a.quitting and p["hk"].calls[-1] == "stop" and p["tray"].stopped
    assert p["stopped"] == [True] and a._settings is None
