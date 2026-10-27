"""The Windows shell with fakes: tray, display helpers, settings form, App."""

import dataclasses
import sys

import pytest

from transcription_flow import app as app_mod
from transcription_flow import config, models
from transcription_flow.app import App
from transcription_flow.model_service import Cancelled
from transcription_flow.config import Config
from transcription_flow.hotkey import MISSED_RELEASE, HotkeyUnavailable
from transcription_flow.ui import display
from transcription_flow.ui.settings import (MODEL_NOTE, Form, hotkey_choices, model_choices,
                                            model_note)
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


def test_dpi_awareness_access_denied_means_it_was_set_already():
    log = []
    windll = Lib(shcore=Lib(SetProcessDpiAwareness=Fn(log, "shcore", result=-2147024891)),
                 user32=Lib(SetProcessDPIAware=Fn(log, "user32")))
    assert display.enable_dpi_awareness(windll) == "already set (HRESULT 0x80070005)"
    assert [name for name, _ in log] == ["shcore"]


def test_dpi_awareness_failed_hresult_is_reported_and_falls_back():
    """review-m2 F10: an HRESULT failure no longer reads as "system"."""
    log = []
    windll = Lib(shcore=Lib(SetProcessDpiAwareness=Fn(log, "shcore", result=-2147467259)),
                 user32=Lib(SetProcessDPIAware=Fn(log, "user32", result=0)))
    assert display.enable_dpi_awareness(windll) == "unaware after HRESULT 0x80004005"
    assert [name for name, _ in log] == ["shcore", "user32"]


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


def test_form_keeps_the_file_only_settings_of_its_base():
    base = Config(paste_settle_seconds=2.0)
    form = Form("f9", "base.en", "en", "paste", False, False)
    assert form.to_config(base) == Config(hotkey="f9", model="base.en", output="paste",
                                          paste_settle_seconds=2.0)
    assert form.to_config().paste_settle_seconds == Config().paste_settle_seconds


def test_form_fixes_a_language_the_model_cannot_use():
    form = Form("f9", "base.en", "de", "type", False, False)
    assert form.to_config().language == "en"


def test_model_choices_are_sized_and_mark_the_default(tmp_path):
    from transcription_flow.model_service import ModelService

    labels_ = model_choices(ModelService(tmp_path))
    assert list(labels_) == ["tiny.en", "base.en", "small.en"]  # smallest first
    assert labels_["small.en"].endswith("MB, recommended)")
    assert "recommended" not in labels_["tiny.en"]


def test_the_settings_note_warns_that_save_downloads_the_model_after_a_fallback(tmp_path):
    """review-m3 F5: base.en chosen, small.en running: any Save fetches base.en."""
    from transcription_flow.model_service import ModelService

    service = ModelService(tmp_path)
    assert model_note(Config(model="small.en"), "small.en", service) == MODEL_NOTE
    assert model_note(Config(model="small.en"), None, service) == MODEL_NOTE  # the setup window
    note = model_note(Config(model="base.en"), "small.en", service)
    assert note.startswith(MODEL_NOTE) and note.endswith(
        "base.en isn't ready, so dictation uses small.en for now. Save downloads base.en "
        f"({models.size_label(service.size('base.en'))}) even if you change only something "
        "else; pick small.en above to keep it instead.")


def test_hotkey_choices_are_the_hotkey_table():
    choices = hotkey_choices()
    assert choices["right_ctrl"] == "Right Ctrl" and len(set(choices.values())) == len(choices)


# -- App ----------------------------------------------------------------------------

class FakeController:
    def __init__(self):
        self.paused = False
        self.calls = []
        self.recording = True

    def press(self, unchecked=False):
        self.calls.append(("press", unchecked))

    def cancel(self, reason):
        self.calls.append(("cancel", reason))
        was, self.recording = self.recording, False
        return was

    def set_paused(self, paused):
        self.paused = paused
        self.calls.append(("paused", paused))

    def set_output(self, output):
        self.calls.append(("output", output))

    def set_keep_warm(self, on):
        self.calls.append(("keep_warm", on))

    def set_microphone(self, device):
        self.calls.append(("mic", device))

    def set_transcriber(self, engine):
        self.calls.append(("engine", engine))


class FakeHotkey:
    def __init__(self):
        self.calls = []
        self.paused = []
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

    def set_paused(self, paused):
        self.paused.append(paused)


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


SIZES = {"tiny.en": 77_000_000, "base.en": 145_000_000, "small.en": 484_000_000}


class FakeModels:
    """ModelService's interface; download() reports 50% then finishes or fails."""

    def __init__(self, folder):
        self.folder = folder
        self.have = {"small.en"}
        self.calls = []
        self.fail = self.load_fail = None
        self.before_finish = None  # runs at 50%, like a user acting mid-download

    def names(self):
        return list(SIZES)

    def size(self, name):
        return SIZES[name]

    def is_installed(self, name):
        return name in self.have

    def installed(self):
        return [n for n in SIZES if n in self.have]

    def needs_download(self, name):
        return name not in self.have

    def smaller_than(self, name):
        i = list(SIZES).index(name)
        return list(SIZES)[i - 1] if i else None

    def download(self, name, progress, cancel):
        self.calls.append(("download", name))
        progress(50, 100)
        progress(50, 100)  # the same percent again: no new UI work
        if self.before_finish:
            self.before_finish()
        if cancel.is_set():
            raise Cancelled()
        if self.fail:
            raise self.fail
        progress(100, 100)
        self.have.add(name)

    def load(self, name, language):
        self.calls.append(("load", name, language))
        if self.load_fail:
            raise self.load_fail
        return f"engine:{name}:{language}"


class FakeWizard:
    def __init__(self, app, on_close):
        self.app, self.on_close = app, on_close
        self.events, self.lifted, self.closed = [], 0, False

    def model_changed(self, error=None):
        self.events.append(error)

    def lift(self):
        self.lifted += 1

    def close(self):
        self.closed = True
        self.on_close()


@pytest.fixture
def shell(tmp_path):
    parts = {"ctl": FakeController(), "hk": FakeHotkey(), "run": FakeRunKey(), "ui": [],
             "stopped": [], "opened": [], "windows": [], "wizards": [], "jobs": [],
             "models": FakeModels(tmp_path / "models"), "path": tmp_path / "config.json"}

    def settings_factory(*args):
        window = FakeSettings(*args)
        parts["windows"].append(window)
        return window

    def wizard_factory(*args):
        window = FakeWizard(*args)
        parts["wizards"].append(window)
        return window

    a = App(Config(), parts["path"], parts["ctl"], parts["hk"], FakeTray, parts["run"],
            ui_call=lambda fn: (parts["ui"].append(fn), fn()), stop_ui=lambda: parts[
                "stopped"].append(True), settings_factory=settings_factory,
            output_factory=lambda mode: f"output:{mode}", launch_command=lambda: '"tf.exe" --tray',
            log_dir=tmp_path / "logs", opener=parts["opened"].append, device="cpu",
            models=parts["models"], model="small.en", language="en",
            wizard_factory=wizard_factory, spawn=parts["jobs"].append)
    parts["tray"] = a._tray
    return a, parts


def run_job(p):
    """Run the background job the app spawned, as its thread would."""
    p["jobs"].pop(0)()


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


def test_pause_also_stops_the_hotkey_suppressing_keys(shell):
    a, p = shell
    a.toggle_pause()
    assert p["hk"].paused == [True]
    a.toggle_pause()
    assert p["hk"].paused == [True, False]


def test_a_missed_key_release_cancels_with_a_toast(shell):
    a, p = shell
    a.cancel_recording(MISSED_RELEASE)
    assert p["ctl"].calls == [("cancel", MISSED_RELEASE)]
    assert len(p["ui"]) == 1  # the toast went through the Tk thread
    assert p["tray"].notes == [app_mod.MISSED_RELEASE_TOAST]


def test_an_unchecked_hold_gets_a_one_time_notice(shell):
    """review-m2 R3: Filter Keys repeat off plus a suppressed key."""
    a, p = shell
    a.config = dataclasses.replace(a.config, hotkey="f9")
    a.press(False)
    assert p["tray"].notes == [] and p["ui"] == []
    a.press(True)
    a.press(True)
    assert p["ctl"].calls == [("press", False), ("press", True), ("press", True)]
    assert len(p["ui"]) == 1  # through the Tk thread, once
    assert p["tray"].notes == [app_mod.UNCHECKED_NOTICE.format(key="F9")]
    assert "F9" in p["tray"].notes[0]


def test_a_missed_release_with_nothing_recording_is_silent(shell):
    a, p = shell
    p["ctl"].recording = False
    a.cancel_recording(MISSED_RELEASE)
    assert p["tray"].notes == [] and p["ui"] == []


def test_a_shortcut_cancel_is_silent(shell):
    a, p = shell
    a.cancel_recording("another key was pressed")  # Right Ctrl+C: not a problem
    assert p["ctl"].calls == [("cancel", "another key was pressed")]
    assert p["tray"].notes == [] and p["ui"] == []


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


def test_saving_keeps_a_hand_edited_paste_settle(shell):
    a, p = shell
    a.config = Config(paste_settle_seconds=2.0)
    save(a, p, output="paste")
    assert config.load(p["path"]) == Config(output="paste", paste_settle_seconds=2.0)


def test_a_model_change_downloads_with_progress_then_switches_live(shell):
    """review-m2 F6: no longer saved for the next start only."""
    a, p = shell
    save(a, p, model="base.en")
    assert config.load(p["path"]).model == "base.en"
    assert p["tray"].notes[-1] == ("Downloading the base.en model (145 MB). small.en works "
                                   "until it's ready.")
    assert p["tray"].statuses[-1][0] == "Downloading base.en 0%"
    assert a.model == "small.en" and p["ctl"].calls == []  # the old one keeps working
    statuses = len(p["tray"].statuses)
    p["models"].before_finish = lambda: p["tray"].statuses.append(("at 50", None))
    run_job(p)
    assert p["tray"].statuses[statuses][0] == "Downloading base.en 50%"
    assert p["tray"].statuses[statuses + 1] == ("at 50", None)  # one refresh for two 50s
    assert p["models"].calls == [("download", "base.en"), ("load", "base.en", "en")]
    assert p["ctl"].calls == [("engine", "engine:base.en:en")]
    assert a.model == "base.en" and a.job is None
    assert p["tray"].statuses[-1][0] == "Ready (CPU)"
    assert p["tray"].notes[-1] == "Ready with base.en. Hold Right Ctrl to dictate."


def test_an_installed_model_is_loaded_without_a_download(shell):
    a, p = shell
    p["models"].have.add("tiny.en")
    save(a, p, model="tiny.en")
    assert p["tray"].statuses[-1][0] == "Loading tiny.en"
    assert not any("Downloading" in n for n in p["tray"].notes)
    run_job(p)
    assert p["models"].calls == [("load", "tiny.en", "en")] and a.model == "tiny.en"


def test_a_failed_download_keeps_the_old_model_and_says_so(shell):
    a, p = shell
    a.open_wizard()
    p["models"].fail = models.DownloadError("The download failed: offline.")
    save(a, p, model="base.en")
    run_job(p)
    assert a.model == "small.en" and a.job is None and p["ctl"].calls == []
    assert p["tray"].statuses[-1][0] == ("Error: The download failed: offline. Still using "
                                         "small.en.")
    assert p["wizards"][0].events[-1] == "The download failed: offline."
    p["models"].fail = None
    a.retry_model()  # the wizard's Retry
    run_job(p)
    assert a.model == "base.en"


def test_a_model_that_fails_to_load_names_the_error_type_only(shell, caplog):
    a, p = shell
    p["models"].have.add("tiny.en")
    p["models"].load_fail = RuntimeError("secret path C:/Users/someone")
    save(a, p, model="tiny.en")
    run_job(p)
    assert p["tray"].notes[-1] == ("The tiny.en model could not be loaded (RuntimeError). "
                                   "Still using small.en.")


def test_a_newer_choice_cancels_the_older_download_and_a_late_finish_is_ignored(shell):
    a, p = shell
    save(a, p, model="base.en")
    first = a.job
    p["models"].have.add("tiny.en")
    save(a, p, model="tiny.en")
    assert first.cancel.is_set() and a.job.name == "tiny.en"
    run_job(p)  # the base.en thread sees the cancel: nothing happens
    assert p["ctl"].calls == [] and a.model == "small.en"
    run_job(p)
    assert a.model == "tiny.en" and p["ctl"].calls == [("engine", "engine:tiny.en:en")]
    a._job_done(first, "late engine")  # a stale finish from the first job
    assert a.model == "tiny.en" and len(p["ctl"].calls) == 1


def test_saving_the_same_choice_again_does_not_restart_the_download(shell):
    a, p = shell
    save(a, p, model="base.en")
    job = a.job
    save(a, p, model="base.en")
    assert a.job is job and len(p["jobs"]) == 1 and not job.cancel.is_set()


def test_saving_with_no_model_change_starts_no_job(shell):
    """review-m3 R8: a Save that keeps the running model doesn't reload it."""
    a, p = shell
    save(a, p, hotkey="f9")
    assert a.job is None and p["jobs"] == [] and p["models"].calls == []


def test_switching_back_to_the_running_model_mid_download_clears_the_progress(shell):
    """QA B1: the tray and the setup window drop the cancelled download."""
    a, p = shell
    a.open_wizard()
    save(a, p, model="base.en")
    assert p["tray"].statuses[-1][0] == "Downloading base.en 0%"
    events = len(p["wizards"][0].events)
    save(a, p, model="small.en")  # changed their mind: back to the running model
    assert a.job is None and len(p["jobs"]) == 1
    assert p["tray"].statuses[-1][0] == "Ready (CPU)"
    assert len(p["wizards"][0].events) == events + 1


def test_a_replaced_job_that_fails_late_leaves_the_new_one_alone(shell):
    """review-m3 R6: the old job's late failure must not clear the new job,
    or the new job's success would then be ignored."""
    a, p = shell
    save(a, p, model="base.en")
    first = a.job
    p["models"].have.add("tiny.en")
    save(a, p, model="tiny.en")
    second = a.job
    a._job_failed(first, "The download failed: offline.")
    assert a.job is second and p["tray"].statuses[-1][0] == "Loading tiny.en"
    assert not any(n.startswith("The download failed") for n in p["tray"].notes)
    run_job(p)  # base.en: cancelled
    run_job(p)
    assert a.model == "tiny.en" and a.job is None


def test_cancel_download_stops_it_quietly(shell):
    a, p = shell
    a.open_wizard()
    save(a, p, model="base.en")
    p["models"].before_finish = a.cancel_download
    run_job(p)
    assert a.job is None and a.model == "small.en" and p["ctl"].calls == []
    assert p["tray"].statuses[-1][0] == "Ready (CPU)"
    assert not any(n.startswith("Error") for n, _ in p["tray"].statuses)


def test_a_language_change_reloads_the_same_model(shell):
    a, p = shell
    a.use_model("small.en", "de")  # as if multilingual
    run_job(p)
    assert p["models"].calls == [("load", "small.en", "de")]


def test_a_setup_window_change_never_starts_a_model_download(first_run):
    """Picking a mic or a key on the first run must not fetch ~500 MB."""
    a, p = first_run
    a.update_config(dataclasses.replace(a.config, mic="Headset mic"))
    a.update_config(dataclasses.replace(a.config, hotkey="f9"))
    assert a.job is None and p["jobs"] == [] and p["models"].calls == []


def test_settings_save_fetches_the_configured_model_the_start_fell_back_from(shell):
    """The start-up toast says: open Settings and press Save to download it."""
    a, p = shell
    a.config = Config(model="base.en")
    save(a, p)
    assert a.job is not None and a.job.name == "base.en"


def test_a_notice_is_a_toast_not_an_error(shell):
    a, p = shell
    a.start()
    a.notify("The text was pasted, but part of your clipboard could not be put back.")
    assert p["tray"].notes[-1].startswith("The text was pasted")
    assert p["tray"].statuses[-1][0] == "Ready (CPU)"


def test_download_progress_updates_the_tray_every_few_percent(shell):
    a, p = shell
    save(a, p, model="base.en")
    job, before = a.job, len(p["tray"].statuses)
    for percent in range(1, 11):
        a._progress(job, percent, 100)
    assert [s for s, _ in p["tray"].statuses[before:]] == ["Downloading base.en 5%",
                                                           "Downloading base.en 10%"]


def test_a_jump_past_a_multiple_of_five_still_updates_the_tray(shell):
    a, p = shell
    save(a, p, model="base.en")
    job, before = a.job, len(p["tray"].statuses)
    for percent in (3, 13, 17, 23):
        a._progress(job, percent, 100)
    assert [s for s, _ in p["tray"].statuses[before:]] == ["Downloading base.en 13%",
                                                           "Downloading base.en 23%"]


def test_a_mic_change_goes_to_the_controller(shell):
    a, p = shell
    save(a, p, mic="Headset mic")
    save(a, p, mic="")
    assert p["ctl"].calls == [("mic", "Headset mic"), ("mic", None)]
    assert config.load(p["path"]).mic == ""


# -- no model yet (first run) --------------------------------------------------

@pytest.fixture
def first_run(shell):
    a, p = shell
    a._model = a._language = None
    p["models"].have.clear()
    return a, p


def test_first_start_opens_the_setup_window(first_run):
    a, p = first_run
    a.start()
    assert len(p["wizards"]) == 1 and p["wizards"][0].app is a
    assert p["tray"].statuses[-1][0] == "No speech model yet"
    assert p["tray"].notes == []  # no "Ready" toast with nothing to dictate with


def test_a_start_with_a_fallback_model_says_which_one(shell):
    a, p = shell
    a.config = Config(model="base.en")
    a.start()
    assert p["tray"].notes == ["base.en isn't downloaded yet; using small.en. Open Settings "
                               "and press Save to download it."]
    assert p["wizards"] == []


def test_settings_with_no_model_is_the_setup_window_once(first_run):
    a, p = first_run
    a.open_settings()
    a.open_settings()
    assert len(p["wizards"]) == 1 and p["wizards"][0].lifted == 1 and p["windows"] == []
    p["wizards"][0].close()
    a.open_settings()
    assert len(p["wizards"]) == 2


def test_a_press_with_no_model_explains_and_records_nothing(first_run):
    a, p = first_run
    a.press(False)
    assert p["ctl"].calls == [] and p["tray"].notes == [app_mod.NO_MODEL_NOTICE]
    a.use_model("base.en", "en")
    p["models"].before_finish = lambda: a.press(False)
    run_job(p)
    assert app_mod.STILL_DOWNLOADING.format(name="base.en", percent=50) in p["tray"].notes
    assert ("press", False) not in p["ctl"].calls
    a.press(False)  # ready now
    assert p["ctl"].calls[-1] == ("press", False)


def test_the_first_download_says_dictation_starts_when_ready(first_run):
    a, p = first_run
    a.use_model("tiny.en", "en")
    assert p["tray"].notes[-1] == ("Downloading the tiny.en model (77 MB). Dictation starts "
                                   "when it's ready.")


def test_the_setup_window_hears_every_model_step(first_run):
    a, p = first_run
    a.open_wizard()
    a.use_model("tiny.en", "en")
    run_job(p)
    events = p["wizards"][0].events
    assert events == [None, None, None, None]  # started, 50%, 100%, ready
    assert a.model == "tiny.en"


def test_open_models_folder_and_mic_privacy(shell, tmp_path):
    a, p = shell
    a.open_models_folder()
    a.open_mic_privacy()
    assert p["opened"] == [tmp_path / "models", "ms-settings:privacy-microphone"]
    assert (tmp_path / "models").is_dir()


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


def test_quit_cancels_a_download_and_closes_the_setup_window(shell):
    a, p = shell
    a.open_wizard()
    save(a, p, model="base.en")
    job = a.job
    a.quit()
    assert job.cancel.is_set() and a.job is None and p["wizards"][0].closed


def test_quit_stops_hotkey_tray_settings_and_the_ui(shell):
    a, p = shell
    a.start()
    a.open_settings()
    a.quit()
    assert a.quitting and p["hk"].calls[-1] == "stop" and p["tray"].stopped
    assert p["stopped"] == [True] and a._settings is None
