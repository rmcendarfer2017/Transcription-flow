"""The running app: wires the hotkey, controller, tray and settings together.

Threads: Tk owns the main thread (the overlay's loop). The hotkey dispatcher
and the transcriber worker call the controller; the tray's thread only
calls ``ui_call(fn)``, which runs ``fn`` on the Tk thread. Everything below
marked "Tk thread" may touch Tk and the config.
"""

import dataclasses
import logging
import os
import sys
import threading

from . import config as config_mod
from .hotkey import HOTKEYS, MISSED_RELEASE
from .model_service import Cancelled
from .models import ModelError, size_label
from .output import make_output

log = logging.getLogger(__name__)

MISSED_RELEASE_TOAST = ("Recording cancelled: the key release was missed, so nothing was "
                        "typed. Hold the key again to dictate.")
UNCHECKED_NOTICE = ("Filter Keys has key repeat off, so the app can't tell if a release of "
                    "{key} is missed in an admin window. Recordings that reach 5 minutes are "
                    "dropped, not typed. Right Ctrl doesn't have this limit.")


NO_MODEL_NOTICE = ("No speech model yet. Finish the setup window, or open Settings and "
                   "press Save to download one.")
STILL_DOWNLOADING = "The {name} model is still downloading ({percent}%). Try again when it's ready."


def open_folder(path) -> None:
    """Our own folders (logs, models) and ms-settings: pages."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - our own folder, or a Windows Settings page
    else:
        log.info("open this yourself: %s", path)


TRAY_STEP = 5  # percent between tray status updates during a download


def _spawn(fn) -> None:
    threading.Thread(target=fn, name="model", daemon=True).start()


class _Job:
    """One download-and-load; a newer choice cancels it."""

    def __init__(self, name, language, downloading):
        self.name, self.language = name, language
        self.downloading = downloading
        self.percent = 0
        self.shown = 0  # the percent the tray last showed
        self.cancel = threading.Event()

    @property
    def target(self):
        return self.name, self.language

    @property
    def fetching(self) -> bool:
        """Still downloading (then it loads)."""
        return self.downloading and self.percent < 100


class App:
    def __init__(self, config, config_path, controller, hotkey, tray_factory, autostart,
                 ui_call, stop_ui, settings_factory=None, output_factory=make_output,
                 launch_command=None, log_dir=None, opener=open_folder, device="cpu",
                 models=None, model=None, language=None, wizard_factory=None, spawn=_spawn,
                 mic_list=None, meter_factory=None):
        """``model``/``language``: what the controller's engine runs now
        (None: no model is installed yet). ``models`` is a ModelService."""
        self.config = config
        self._config_path = config_path
        self._controller = controller
        self._hotkey = hotkey
        self._tray = tray_factory(self.tray_actions())
        self._stop_ui = stop_ui
        self._autostart = autostart
        self._ui_call = ui_call
        self._settings_factory = settings_factory
        self._output_factory = output_factory
        self._launch_command = launch_command
        self._log_dir = log_dir
        self._opener = opener
        self._device = device
        self._models = models
        self._model, self._language = model, language
        self._wizard_factory = wizard_factory
        self._spawn = spawn
        self.mic_list = mic_list or (lambda: [])
        self.meter_factory = meter_factory
        self._job = None
        self._settings = None
        self._wizard = None
        self._error = None
        self._unchecked_noticed = False
        self.quitting = False

    # -- status ----------------------------------------------------------
    def status(self) -> str:
        if self._controller.paused:
            return "Paused"
        if self._error:
            return f"Error: {self._error}"
        job = self._job
        if job is not None:
            if job.fetching:
                return f"Downloading {job.name} {job.percent}%"
            return f"Loading {job.name}"
        if self._model is None:
            return "No speech model yet"
        return f"Ready ({self._device.upper()})"

    def _refresh(self) -> None:
        self._tray.set_status(self.status(), paused=self._controller.paused)

    def start(self) -> None:
        """Tk thread. Raises HotkeyUnavailable if there is no global hotkey here.
        With no model installed the setup window opens, at every start until
        one is (plan 3.8); a chosen model that isn't installed gives way to
        the one that is, with a toast (review-m2 F6)."""
        self._hotkey.start(self.config.hotkey)
        self._tray.start()
        self._refresh()
        if self._model is None:
            self.open_wizard()
        elif self._model != self.config.model:
            if self._models.failed_to_load(self.config.model):  # QA B2
                why, again = "could not be loaded", " again"
            else:
                why, again = "isn't downloaded yet", ""
            self._tray.notify(f"{self.config.model} {why}; using {self._model}. Open Settings "
                              f"and press Save to download it{again}.")
        else:
            self._tray.notify(f"Ready. Hold {self.hotkey_label()} to dictate.")

    @property
    def model(self):
        """The model dictation uses now, or None."""
        return self._model

    @property
    def models(self):
        """The ModelService."""
        return self._models

    @property
    def job(self):
        """The download or load in progress, or None (Tk thread)."""
        return self._job

    def hotkey_label(self) -> str:
        return HOTKEYS[self.config.hotkey].label

    def report_error(self, message: str) -> None:
        """Any thread: a problem the user can fix (mic blocked, etc.). The
        caller has logged it."""
        self._ui_call(lambda: self._show_error(message))

    def notify(self, message: str) -> None:
        """Any thread: a toast that isn't an error (the status line stays)."""
        self._ui_call(lambda: self._tray.notify(message))

    def _show_error(self, message: str) -> None:
        self._error = message
        self._refresh()
        self._tray.notify(message)

    def on_state(self, state: str) -> None:
        """Any thread: the controller's overlay state. A recording that
        starts means the microphone works again, so the error line goes."""
        if state == "recording" and self._error:
            self._ui_call(self._clear_error)

    def _clear_error(self) -> None:
        self._error = None
        self._refresh()

    def press(self, unchecked: bool) -> None:
        """Hotkey thread: start a recording. The first hold whose release
        can't be checked gets a one-time notice (review-m2 R3)."""
        if self._model is None:  # nothing to transcribe with yet
            job = self._job
            notice = (STILL_DOWNLOADING.format(name=job.name, percent=job.percent)
                      if job is not None and job.fetching else NO_MODEL_NOTICE)
            self._ui_call(lambda: self._tray.notify(notice))
            return
        if unchecked and not self._unchecked_noticed:
            self._unchecked_noticed = True
            self._ui_call(lambda: self._tray.notify(
                UNCHECKED_NOTICE.format(key=self.hotkey_label())))
        self._controller.press(unchecked)

    def cancel_recording(self, reason: str) -> None:
        """Hotkey thread: drop the recording. A missed key release is never
        silent: the user gets a toast (review-m2 F3)."""
        if self._controller.cancel(reason) and reason == MISSED_RELEASE:
            self._ui_call(lambda: self._tray.notify(MISSED_RELEASE_TOAST))

    # -- tray actions (tray thread: hand everything to Tk) ---------------
    def tray_actions(self):
        from .ui.tray import TrayActions

        return TrayActions(toggle_pause=lambda: self._ui_call(self.toggle_pause),
                           open_settings=lambda: self._ui_call(self.open_settings),
                           open_logs=lambda: self._ui_call(self.open_logs),
                           quit=lambda: self._ui_call(self.quit))

    # -- Tk thread -------------------------------------------------------
    def toggle_pause(self) -> None:
        paused = not self._controller.paused
        self._controller.set_paused(paused)
        self._hotkey.set_paused(paused)  # suppressed keys work again while paused
        self._error = None
        self._refresh()

    def open_logs(self) -> None:
        if self._log_dir is not None:
            self._opener(self._log_dir)

    def open_settings(self) -> None:
        """With no model yet, Settings is the setup window (it can download one)."""
        if self._model is None:
            self.open_wizard()
            return
        if self._settings is not None:
            self._settings.lift()
            return
        from .ui.settings import Form

        form = Form.from_config(self.config, self._autostart.is_enabled())
        self._settings = self._settings_factory(form, self._autostart.available,
                                                self.apply_settings, self._settings_closed)

    def _settings_closed(self) -> None:
        self._settings = None

    def open_wizard(self) -> None:
        if self._wizard is not None:
            self._wizard.lift()
            return
        if self._settings is not None:
            self._settings.close()
        self._wizard = self._wizard_factory(self, self._wizard_closed)

    def _wizard_closed(self) -> None:
        self._wizard = None

    def apply_settings(self, form) -> None:
        """Settings' Save: also fetches the chosen model if it isn't the one
        running (the start-up toast tells the user to press Save for that)."""
        new = form.to_config(self.config)
        self.update_config(new, form.start_with_windows)
        self.use_model(new.model, new.language)

    def update_config(self, new, start_with_windows=None) -> None:
        """Apply each change live, then save. Settings and the setup window
        both come here; ``start_with_windows`` None leaves autostart alone.
        The model is switched only by use_model, so picking a mic in the
        setup window never starts a download."""
        old = self.config
        if new.hotkey != old.hotkey and not self._set_hotkey(new.hotkey):
            new = dataclasses.replace(new, hotkey=old.hotkey)
            self._set_hotkey(old.hotkey)
        if new.output != old.output:
            self._controller.set_output(self._output_factory(new.output))
        if new.keep_mic_warm != old.keep_mic_warm:
            self._controller.set_keep_warm(new.keep_mic_warm)
        if new.mic != old.mic:
            self._controller.set_microphone(new.mic or None)
        if start_with_windows is not None:
            self._apply_autostart(start_with_windows)
        self.config = new
        try:
            config_mod.save(new, self._config_path)
        except OSError as err:
            log.warning("settings not saved (%s)", type(err).__name__)
            self.report_error("The settings could not be saved.")
            return
        log.info("settings saved")
        self._error = None
        self._refresh()

    # -- models (Tk thread, except _run_job) ------------------------------
    def use_model(self, name: str, language: str) -> None:
        """Switch dictation to ``name``: download it if needed (progress in
        the tray and the setup window), load it off the Tk thread, then swap
        it in; the current model keeps working until then. A newer choice
        cancels an older one; a download cut short resumes next time."""
        target = (name, language)
        if self._job is not None:
            if self._job.target == target:
                return
            self._cancel_job()
        if target == (self._model, self._language):
            self._changed()  # a cancelled download's progress goes (QA B1)
            return
        job = self._job = _Job(name, language, self._models.needs_download(name))
        if job.downloading:
            still = (f"{self._model} works until it's ready." if self._model
                     else "Dictation starts when it's ready.")
            self._tray.notify(f"Downloading the {name} model "
                              f"({size_label(self._models.size(name))}). {still}")
        self._changed()
        self._spawn(lambda: self._run_job(job))

    def retry_model(self) -> None:
        """Try the configured model again after a failure."""
        self.use_model(self.config.model, self.config.language)

    def cancel_download(self) -> None:
        if self._job is not None:
            self._cancel_job()
            self._changed()

    def _cancel_job(self) -> None:
        self._job.cancel.set()
        log.info("model %s: cancelled", self._job.name)
        self._job = None

    def _run_job(self, job) -> None:
        """Worker thread."""
        try:
            if job.downloading:
                self._models.download(job.name, lambda done, total: self._progress(
                    job, done, total), job.cancel)
            engine = self._models.load(*job.target)
        except Cancelled:
            return
        except Exception as err:
            if isinstance(err, ModelError):
                message = str(err)
                log.warning("model %s: %s", job.name, err)
            else:
                message = f"The {job.name} model could not be loaded ({type(err).__name__})."
                log.warning("model %s could not be loaded", job.name, exc_info=True)
            self._ui_call(lambda: self._job_failed(job, message))
            return
        self._ui_call(lambda: self._job_done(job, engine))

    def _progress(self, job, done: int, total: int) -> None:
        """Worker thread: hand whole-percent steps to the Tk thread."""
        percent = done * 100 // total if total else 100
        if percent != job.percent:
            job.percent = percent
            self._ui_call(lambda: self._job_progress(job))

    def _job_progress(self, job) -> None:
        if job is self._job:
            # Each refresh rebuilds the tray menu: only every TRAY_STEP percent.
            if job.percent - job.shown >= TRAY_STEP or job.percent == 100:
                job.shown = job.percent
                self._refresh()
            self._tell_wizard()

    def _job_done(self, job, engine) -> None:
        if job is not self._job:  # cancelled or replaced meanwhile
            return
        self._job = None
        self._controller.set_transcriber(engine)
        self._model, self._language = job.target
        log.info("now using %s", job.name)
        self._tray.notify(f"Ready with {job.name}. Hold {self.hotkey_label()} to dictate.")
        self._changed()

    def _job_failed(self, job, message: str) -> None:
        if job is not self._job:
            return
        self._job = None
        still = f" Still using {self._model}." if self._model else ""
        self._show_error(f"{message}{still}")
        self._tell_wizard(error=message)

    def _changed(self) -> None:
        """The model state moved on: the tray status and the setup window follow."""
        self._refresh()
        self._tell_wizard()

    def _tell_wizard(self, error=None) -> None:
        if self._wizard is not None:
            self._wizard.model_changed(error)

    def open_models_folder(self) -> None:
        folder = self._models.folder
        folder.mkdir(parents=True, exist_ok=True)
        self._opener(folder)

    def open_mic_privacy(self) -> None:
        self._opener("ms-settings:privacy-microphone")

    def _set_hotkey(self, key_id: str) -> bool:
        try:
            self._hotkey.set_key(key_id)
            return True
        except Exception as err:
            log.warning("hotkey change failed (%s)", type(err).__name__)
            self.report_error("The hotkey could not be set.")
            return False

    def _apply_autostart(self, on: bool) -> None:
        if not self._autostart.available:
            return
        try:
            if on:  # always rewrite, so a moved install fixes itself
                self._autostart.enable(self._launch_command())
            elif self._autostart.is_enabled():
                self._autostart.disable()
        except OSError as err:
            log.warning("autostart change failed (%s)", type(err).__name__)
            self.report_error("Start with Windows could not be changed.")

    def quit(self) -> None:
        self.quitting = True
        if self._job is not None:
            self._cancel_job()
        if self._wizard is not None:
            self._wizard.close()
        if self._settings is not None:
            self._settings.close()
        self._hotkey.stop()
        self._tray.stop()
        self._stop_ui()
