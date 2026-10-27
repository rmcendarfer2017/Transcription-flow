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

from . import config as config_mod
from .output import make_output

log = logging.getLogger(__name__)


def open_folder(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - our own log folder, opened in Explorer
    else:
        log.info("open the log folder yourself: %s", path)


class App:
    def __init__(self, config, config_path, controller, hotkey, tray_factory, autostart,
                 ui_call, stop_ui, settings_factory=None, output_factory=make_output,
                 launch_command=None, log_dir=None, opener=open_folder, device="cpu"):
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
        self._settings = None
        self._error = None
        self.quitting = False

    # -- status ----------------------------------------------------------
    def status(self) -> str:
        if self._controller.paused:
            return "Paused"
        if self._error:
            return f"Error: {self._error}"
        return f"Ready ({self._device.upper()})"

    def _refresh(self) -> None:
        self._tray.set_status(self.status(), paused=self._controller.paused)

    def start(self) -> None:
        """Tk thread. Raises HotkeyUnavailable if there is no global hotkey here."""
        self._hotkey.start(self.config.hotkey)
        self._tray.start()
        self._refresh()
        self._tray.notify(f"Ready. Hold {self.hotkey_label()} to dictate.")

    def hotkey_label(self) -> str:
        from .hotkey import HOTKEYS

        return HOTKEYS[self.config.hotkey].label

    def report_error(self, message: str) -> None:
        """Any thread: a problem the user can fix (mic blocked, etc.)."""
        log.warning(message)

        def show():
            self._error = message
            self._refresh()
            self._tray.notify(message)

        self._ui_call(show)

    # -- tray actions (tray thread: hand everything to Tk) ---------------
    def tray_actions(self):
        from .ui.tray import TrayActions

        return TrayActions(toggle_pause=lambda: self._ui_call(self.toggle_pause),
                           open_settings=lambda: self._ui_call(self.open_settings),
                           open_logs=lambda: self._ui_call(self.open_logs),
                           quit=lambda: self._ui_call(self.quit))

    # -- Tk thread -------------------------------------------------------
    def toggle_pause(self) -> None:
        self._controller.set_paused(not self._controller.paused)
        self._error = None
        self._refresh()

    def open_logs(self) -> None:
        if self._log_dir is not None:
            self._opener(self._log_dir)

    def open_settings(self) -> None:
        if self._settings is not None:
            self._settings.lift()
            return
        from .ui.settings import Form

        form = Form.from_config(self.config, self._autostart.is_enabled())
        self._settings = self._settings_factory(form, self._autostart.available,
                                                self.apply_settings, self._settings_closed)

    def _settings_closed(self) -> None:
        self._settings = None

    def apply_settings(self, form) -> None:
        new = form.to_config()
        old = self.config
        if new.hotkey != old.hotkey and not self._set_hotkey(new.hotkey):
            new = dataclasses.replace(new, hotkey=old.hotkey)
            self._set_hotkey(old.hotkey)
        if new.output != old.output:
            self._controller.set_output(self._output_factory(new.output))
        if new.keep_mic_warm != old.keep_mic_warm:
            self._controller.set_keep_warm(new.keep_mic_warm)
        self._apply_autostart(form.start_with_windows)
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
        if self._settings is not None:
            self._settings.close()
        self._hotkey.stop()
        self._tray.stop()
        self._stop_ui()
