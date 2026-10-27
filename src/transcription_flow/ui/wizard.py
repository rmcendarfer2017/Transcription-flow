"""The first-run setup window (plan 3.8): microphone, model, hotkey, a try.

``WizardFlow`` holds the steps and every text the window shows, in plain
Python, so it is tested without a display. ``WizardWindow`` is the Tk view
(Tk thread only); the app reopens it at each start until a model is
installed, and the tray's Settings opens it while there is none.
"""

import dataclasses
import logging

from ..config import language_for
from ..models import size_label
from ..recorder import LevelMeter, MicrophoneError
from .settings import hotkey_choices, mic_choices, model_choices

log = logging.getLogger(__name__)

TITLE = "Set up Transcription Flow"
STEPS = ("welcome", "mic", "model", "hotkey", "try", "done")
PRIVACY_PATH = ("Settings > Privacy & security > Microphone > "
                "Let desktop apps access your microphone")
WELCOME = ("Hold a key, speak, let go: your words are typed where the cursor is. "
           "Everything runs on this PC; your voice never leaves it. "
           "Inspired by Wispr Flow.")
MIC_TEXT = {
    LevelMeter.LISTENING: "Say something. The bar should move.",
    LevelMeter.HEARD: "We can hear you.",
    LevelMeter.QUIET: ("We hear only faint sound. Speak up, move closer, or pick "
                       "another microphone."),
    LevelMeter.BLOCKED: f"Windows is blocking the microphone. Turn it on in {PRIVACY_PATH}.",
}


@dataclasses.dataclass
class ModelView:
    """What the model step shows."""

    text: str
    percent: int = None  # a progress bar while downloading
    busy: bool = False  # a download or load is running: offer Cancel
    failed: bool = False  # offer Retry, a smaller model and the model folder


class WizardFlow:
    """The steps and their texts. ``app`` is the running App."""

    def __init__(self, app):
        self.app = app
        self.index = 0
        self.error = None  # the last model failure, until the next try

    @property
    def step(self) -> str:
        return STEPS[self.index]

    def can_back(self) -> bool:
        return 0 < self.index < len(STEPS) - 1

    def can_next(self) -> bool:
        """The model step waits until a model is ready."""
        if self.step == "model":
            return self.app.model is not None and self.app.job is None
        return self.index < len(STEPS) - 1

    def next(self) -> None:
        if self.can_next():
            self.index += 1

    def back(self) -> None:
        if self.can_back():
            self.index -= 1

    # -- microphone -------------------------------------------------------
    def mic_choices(self) -> dict:
        return mic_choices(self.app.mic_list(), self.app.config.mic)

    def choose_mic(self, name: str) -> None:
        if name != self.app.config.mic:
            self.app.update_config(dataclasses.replace(self.app.config, mic=name))

    @staticmethod
    def mic_text(status: str) -> str:
        return MIC_TEXT[status]

    @staticmethod
    def mic_blocked(status: str) -> bool:
        return status == LevelMeter.BLOCKED

    # -- model ------------------------------------------------------------
    def model_choices(self) -> dict:
        return model_choices(set(self.app.models.installed()))

    def chosen_model(self) -> str:
        job = self.app.job
        return job.name if job is not None else self.app.config.model

    def choose_model(self, name: str) -> None:
        """Download (if needed) and switch to ``name``."""
        self.error = None
        config = self.app.config
        self.app.update_config(dataclasses.replace(
            config, model=name, language=language_for(name, config.language)))

    def retry(self) -> None:
        self.error = None
        self.app.retry_model()

    def smaller_model(self):
        return self.app.models.smaller_than(self.chosen_model())

    def use_smaller_model(self) -> None:
        smaller = self.smaller_model()
        if smaller is not None:
            self.choose_model(smaller)

    def model_view(self) -> ModelView:
        app, job = self.app, self.app.job
        if job is not None:
            if job.downloading and job.percent < 100:
                size = size_label(app.models.size(job.name))
                return ModelView(f"Downloading {job.name}: {job.percent}% of {size}. You can "
                                 "close this window; the download carries on.",
                                 percent=job.percent, busy=True)
            return ModelView(f"Getting {job.name} ready...", busy=True)
        if self.error:
            return ModelView(f"{self.error} Check the internet connection and try again, "
                             "or pick a smaller model.", failed=True)
        if app.model is not None:
            return ModelView(f"{app.model} is ready.")
        return ModelView("Pick a model and press Download. It is a one-time download.")

    # -- hotkey and try ---------------------------------------------------
    @staticmethod
    def hotkey_choices() -> dict:
        return hotkey_choices()

    def choose_hotkey(self, key: str) -> None:
        if key != self.app.config.hotkey:
            self.app.update_config(dataclasses.replace(self.app.config, hotkey=key))

    def try_text(self) -> str:
        return (f"Click in the box, hold {self.app.hotkey_label()}, say \"Hello, this is a "
                "test\", then let go. The words appear in the box.")

    def done_text(self) -> str:
        return (f"All set. Transcription Flow waits in the tray, next to the clock. Hold "
                f"{self.app.hotkey_label()} in any app to dictate. Settings and Quit are in "
                "the tray icon's menu.")


class WizardWindow:
    """The Tk view of a WizardFlow; ``model_changed`` is called by the app."""

    METER_MS = 100

    def __init__(self, parent, app, on_close=None):
        import tkinter as tk
        from tkinter import ttk

        self._tk, self._ttk = tk, ttk
        self.flow = WizardFlow(app)
        self._app = app
        self._on_close = on_close
        self._meter = None
        self._meter_job = None
        win = self.window = tk.Toplevel(parent)
        win.title(TITLE)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(win, padding=16)
        outer.grid(sticky="nsew")
        self._title = ttk.Label(outer, font=("Segoe UI", 13, "bold"))
        self._title.grid(row=0, column=0, sticky="w")
        self.body = ttk.Frame(outer, width=440)
        self.body.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        nav = ttk.Frame(outer)
        nav.grid(row=2, column=0, sticky="e", pady=(16, 0))
        self.back_button = ttk.Button(nav, text="Back", command=self.back)
        self.back_button.grid(row=0, column=0, padx=4)
        self.next_button = ttk.Button(nav, text="Next", command=self.next, default="active")
        self.next_button.grid(row=0, column=1)
        win.bind("<Escape>", lambda e: self.close())
        self.render()
        win.lift()
        win.focus_force()

    # -- navigation -------------------------------------------------------
    def next(self) -> None:
        if self.flow.step == "done":
            self.close()
            return
        self.flow.next()
        self.render()

    def back(self) -> None:
        self.flow.back()
        self.render()

    def render(self) -> None:
        self._stop_meter()
        for child in self.body.winfo_children():
            child.destroy()
        step = self.flow.step
        titles = {"welcome": "Welcome", "mic": "Your microphone", "model": "Speech model",
                  "hotkey": "Your dictation key", "try": "Try it", "done": "Done"}
        self._title.configure(text=titles[step])
        getattr(self, f"_render_{step}")()
        self._update_nav()

    def _update_nav(self) -> None:
        flow = self.flow
        self.back_button.state(["!disabled"] if flow.can_back() else ["disabled"])
        last = flow.step == "done"
        self.next_button.configure(text="Finish" if last else "Next")
        self.next_button.state(["!disabled"] if last or flow.can_next() else ["disabled"])

    def _text(self, text, row, **grid):
        label = self._ttk.Label(self.body, text=text, wraplength=440, justify="left")
        label.grid(row=row, column=0, columnspan=3, sticky="w", **grid)
        return label

    def _combo(self, row, choices: dict, value, on_pick):
        combo = self._ttk.Combobox(self.body, state="readonly", values=list(choices.values()),
                                   width=44)
        combo.set(choices.get(value, next(iter(choices.values()))))
        combo.grid(row=row, column=0, columnspan=3, sticky="w", pady=4)

        def picked(_event):
            label = combo.get()
            on_pick(next(key for key, text in choices.items() if text == label))

        combo.bind("<<ComboboxSelected>>", picked)
        return combo

    # -- steps ------------------------------------------------------------
    def _render_welcome(self) -> None:
        self._text(WELCOME, 0)
        self._text("Setup takes about two minutes: check your microphone, download a speech "
                   "model, pick a key and try it.", 1, pady=(8, 0))

    def _render_mic(self) -> None:
        choices = self.flow.mic_choices()
        self.mic_combo = self._combo(0, choices, self._app.config.mic, self._mic_picked)
        self.meter_bar = self._ttk.Progressbar(self.body, length=300, maximum=1.0)
        self.meter_bar.grid(row=1, column=0, columnspan=3, sticky="w", pady=8)
        self.mic_label = self._text("", 2)
        self.privacy_button = self._ttk.Button(self.body, text="Open microphone settings",
                                               command=self._app.open_mic_privacy)
        self._start_meter()

    def _mic_picked(self, name) -> None:
        self.flow.choose_mic(name)
        self._start_meter()

    def _start_meter(self) -> None:
        self._stop_meter()
        meter = self._app.meter_factory(self._app.config.mic or None)
        try:
            meter.start()
        except MicrophoneError as err:
            self.mic_label.configure(text=str(err))
            return
        self._meter = meter
        self._tick_meter()

    def _tick_meter(self) -> None:
        meter = self._meter
        if meter is None or self.window is None:
            return
        status = meter.status()
        self.meter_bar.configure(value=meter.level())
        self.mic_label.configure(text=self.flow.mic_text(status))
        if self.flow.mic_blocked(status):
            self.privacy_button.grid(row=3, column=0, sticky="w", pady=(8, 0))
        else:
            self.privacy_button.grid_remove()
        self._meter_job = self.window.after(self.METER_MS, self._tick_meter)

    def _stop_meter(self) -> None:
        if self._meter_job is not None and self.window is not None:
            self.window.after_cancel(self._meter_job)
        self._meter_job = None
        if self._meter is not None:
            self._meter.stop()
            self._meter = None

    def _render_model(self) -> None:
        flow, ttk = self.flow, self._ttk
        self.model_var = self._tk.StringVar(value=flow.chosen_model())
        choices = flow.model_choices()
        for row, (name, label) in enumerate(choices.items()):
            ttk.Radiobutton(self.body, text=label, value=name, variable=self.model_var).grid(
                row=row, column=0, columnspan=3, sticky="w")
        row = len(choices)
        self.download_button = ttk.Button(self.body, text="Download",
                                          command=lambda: flow.choose_model(self.model_var.get()))
        self.download_button.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.progress = ttk.Progressbar(self.body, length=300, maximum=100)
        self.model_label = self._text("", row + 2, pady=(4, 0))
        actions = ttk.Frame(self.body)
        actions.grid(row=row + 3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.cancel_button = ttk.Button(actions, text="Cancel download",
                                        command=self._app.cancel_download)
        self.retry_button = ttk.Button(actions, text="Retry", command=flow.retry)
        self.smaller_button = ttk.Button(actions, text="Use a smaller model",
                                         command=flow.use_smaller_model)
        self.folder_button = ttk.Button(actions, text="Open model folder",
                                        command=self._app.open_models_folder)
        self._show_model_state()

    def _show_model_state(self) -> None:
        view = self.flow.model_view()
        self.model_label.configure(text=view.text)
        if view.percent is None:
            self.progress.grid_remove()
        else:
            self.progress.grid(row=len(self.flow.model_choices()) + 1, column=0, columnspan=3,
                               sticky="w", pady=(8, 0))
            self.progress.configure(value=view.percent)
        self.download_button.state(["disabled"] if view.busy else ["!disabled"])
        for column, (button, shown) in enumerate([
                (self.cancel_button, view.busy), (self.retry_button, view.failed),
                (self.smaller_button, view.failed and self.flow.smaller_model() is not None),
                (self.folder_button, view.failed)]):
            if shown:
                button.grid(row=0, column=column, padx=(0, 6))
            else:
                button.grid_remove()

    def model_changed(self, error=None) -> None:
        """App, Tk thread: a download or load started, moved on, finished
        or failed (``error``); anything but a failure clears the last one."""
        self.flow.error = error
        if self.window is None:
            return
        if self.flow.step == "model":
            self._show_model_state()
        self._update_nav()

    def _render_hotkey(self) -> None:
        self._text("Hold this key to dictate, let go to type. Keys that do nothing else are "
                   "best; Right Ctrl still works for shortcuts.", 0)
        self.hotkey_combo = self._combo(1, self.flow.hotkey_choices(), self._app.config.hotkey,
                                        self._hotkey_picked)

    def _hotkey_picked(self, key) -> None:
        self.flow.choose_hotkey(key)
        self.hotkey_combo.set(self.flow.hotkey_choices()[self._app.config.hotkey])

    def _render_try(self) -> None:
        self._text(self.flow.try_text(), 0)
        self.try_box = self._tk.Text(self.body, width=52, height=4, wrap="word")
        self.try_box.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.try_box.focus_set()

    def _render_done(self) -> None:
        self._text(self.flow.done_text(), 0)

    # -- window -----------------------------------------------------------
    def lift(self) -> None:
        if self.window is not None:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()

    def close(self) -> None:
        self._stop_meter()
        if self.window is not None:
            self.window.destroy()
            self.window = None
            if self._on_close:
                self._on_close()
