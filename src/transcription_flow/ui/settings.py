"""The settings window (Tk/ttk, one page).

``Form`` and the helpers above the window class are plain Python, so the
choices and the round trip to ``Config`` are tested without a display. The
window imports tkinter only when it opens and runs on the Tk thread.
"""

import dataclasses
import logging

from ..config import Config, language_for, languages_for
from ..hotkey import HOTKEYS
from ..model_service import ModelService
from ..models import DEFAULT_MODEL, size_label
from ..output import OUTPUT_MODES
from ..paths import models_dir
from ..recorder import MicrophoneError

log = logging.getLogger(__name__)

TITLE = "Transcription Flow settings"
MODEL_NOTE = ("A model that isn't downloaded yet downloads when you save (progress in the tray "
              "icon); the current one keeps working until it's ready.")
DEFAULT_MIC_LABEL = "Windows default microphone"
NO_METER = "The microphone can't be tested here."


@dataclasses.dataclass
class Form:
    hotkey: str
    model: str
    language: str
    output: str
    keep_mic_warm: bool
    start_with_windows: bool
    mic: str = ""

    @classmethod
    def from_config(cls, config: Config, start_with_windows: bool) -> "Form":
        return cls(config.hotkey, config.model, config.language, config.output,
                   config.keep_mic_warm, start_with_windows, config.mic)

    def to_config(self, base: Config = None) -> Config:
        """``base`` keeps the settings the form doesn't show (file-only ones)."""
        return dataclasses.replace(base or Config(), hotkey=self.hotkey, model=self.model,
                                   language=language_for(self.model, self.language),
                                   output=self.output,
                                   keep_mic_warm=self.keep_mic_warm, mic=self.mic)


def model_choices(service) -> dict:
    """name -> label with the download size, smallest first; installed
    models are marked "downloaded". ``service`` is a ModelService."""
    installed = set(service.installed())
    labels = {}
    for name in service.names():
        notes = [size_label(service.size(name))]
        if name == DEFAULT_MODEL:
            notes.append("recommended")
        if name in installed:
            notes.append("downloaded")
        labels[name] = f"{name} ({', '.join(notes)})"
    return labels


def mic_choices(available, saved: str) -> dict:
    """device name ("" = the default) -> label. A saved mic that isn't
    plugged in stays listed, so opening a window doesn't silently drop it."""
    choices = {"": DEFAULT_MIC_LABEL, **{name: name for name in available}}
    if saved and saved not in choices:
        choices[saved] = f"{saved} (not connected)"
    return choices


def hotkey_choices() -> dict:
    return {key: spec.label for key, spec in HOTKEYS.items()}


# -- Tk helpers shared with the setup window (Tk thread only) -------------------

def make_combo(parent, choices: dict, value, width=36):
    """A read-only dropdown of ``choices`` labels showing ``value``'s (or the first)."""
    from tkinter import ttk

    combo = ttk.Combobox(parent, state="readonly", values=list(choices.values()), width=width)
    combo.set(choices.get(value, next(iter(choices.values()))))
    return combo


def combo_key(combo, choices: dict):
    """The key of the label a dropdown shows."""
    label = combo.get()
    return next(key for key, text in choices.items() if text == label)


class MeterPoller:
    """Runs a LevelMeter for a window: ``on_tick(meter)`` every METER_MS on
    the Tk loop until ``stop()``; one stream at a time."""

    METER_MS = 100

    def __init__(self, widget, factory, on_tick):
        self._widget, self._factory, self._on_tick = widget, factory, on_tick
        self._meter = self._after = None

    def start(self, device):
        """Test ``device`` (None: the default); returns an error message or None."""
        self.stop()
        if self._factory is None:
            return None
        meter = self._factory(device)
        try:
            meter.start()
        except (MicrophoneError, OSError) as err:  # OSError: no audio system at all
            log.info("level meter unavailable (%s)", type(err).__name__)
            return str(err) if isinstance(err, MicrophoneError) else NO_METER
        self._meter = meter
        self._tick()
        return None

    def _tick(self) -> None:
        self._on_tick(self._meter)
        self._after = self._widget.after(self.METER_MS, self._tick)

    def stop(self) -> None:
        if self._after is not None:
            self._widget.after_cancel(self._after)
            self._after = None
        if self._meter is not None:
            self._meter.stop()
            self._meter = None


class SettingsWindow:
    """One Toplevel; ``on_save(form)`` runs on the Tk thread. ``app`` gives
    the microphone list, the level meter and the installed models."""

    def __init__(self, parent, form: Form, autostart_available: bool, on_save, on_close=None,
                 app=None):
        import tkinter as tk
        from tkinter import ttk

        self._on_save = on_save
        self._on_close = on_close
        service = app.models if app is not None else ModelService(models_dir())
        mics = app.mic_list() if app is not None else []
        self._hotkeys, self._models, self._outputs, self._mics = (
            hotkey_choices(), model_choices(service), dict(OUTPUT_MODES),
            mic_choices(mics, form.mic))
        win = self.window = tk.Toplevel(parent)
        win.title(TITLE)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(win, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)

        self.hotkey = self._combo(frame, 0, "Hold to dictate", self._hotkeys, form.hotkey)
        self.model = self._combo(frame, 1, "Speech model", self._models, form.model)
        self.model.bind("<<ComboboxSelected>>", lambda e: self._update_languages())
        self._languages = languages_for(form.model)
        self.language = self._combo(frame, 2, "Language", self._languages, form.language)
        self.output = self._combo(frame, 3, "Output", self._outputs, form.output)
        self.mic = self._combo(frame, 4, "Microphone", self._mics, form.mic)
        self.mic.bind("<<ComboboxSelected>>", lambda e: self._start_meter())
        self.meter_bar = ttk.Progressbar(frame, length=200, maximum=1.0)
        self.meter_bar.grid(row=5, column=1, sticky="w", pady=(0, 4))
        self._meter = MeterPoller(win, app.meter_factory if app is not None else None,
                                  lambda meter: self.meter_bar.configure(value=meter.level()))

        self.keep_warm = tk.BooleanVar(value=form.keep_mic_warm)
        ttk.Checkbutton(frame, text="Keep the microphone open between dictations "
                        "(catches the first word; the mic-in-use icon stays on)",
                        variable=self.keep_warm).grid(row=6, column=0, columnspan=2,
                                                      sticky="w", pady=(8, 0))
        self.autostart = tk.BooleanVar(value=form.start_with_windows)
        check = ttk.Checkbutton(frame, text="Start with Windows", variable=self.autostart)
        check.grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        if not autostart_available:
            check.state(["disabled"])
        ttk.Label(frame, text=MODEL_NOTE, wraplength=360).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(12, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=9, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self.close).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Save", command=self.save, default="active").grid(row=0,
                                                                                    column=1)
        win.bind("<Escape>", lambda e: self.close())
        self._update_languages()
        self._start_meter()
        win.lift()
        win.focus_force()

    @staticmethod
    def _combo(frame, row, label, choices: dict, value):
        from tkinter import ttk

        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        combo = make_combo(frame, choices, value)
        combo.grid(row=row, column=1, sticky="ew", pady=4)
        return combo

    _key = staticmethod(combo_key)

    def _update_languages(self) -> None:
        current = self._key(self.language, self._languages)
        self._languages = languages_for(self._key(self.model, self._models))
        self.language.configure(values=list(self._languages.values()))
        self.language.set(self._languages.get(current, next(iter(self._languages.values()))))
        self.language.state(["disabled"] if len(self._languages) == 1 else ["!disabled",
                                                                             "readonly"])

    def _start_meter(self) -> None:
        """The picked mic, saved or not; no bar if it can't be opened."""
        self._meter.start(self._key(self.mic, self._mics) or None)

    def form(self) -> Form:
        return Form(hotkey=self._key(self.hotkey, self._hotkeys),
                    model=self._key(self.model, self._models),
                    language=self._key(self.language, self._languages),
                    output=self._key(self.output, self._outputs),
                    keep_mic_warm=bool(self.keep_warm.get()),
                    start_with_windows=bool(self.autostart.get()),
                    mic=self._key(self.mic, self._mics))

    def save(self) -> None:
        form = self.form()
        self.close()
        self._on_save(form)

    def close(self) -> None:
        self._meter.stop()
        if self.window is not None:
            self.window.destroy()
            self.window = None
            if self._on_close:
                self._on_close()

    def lift(self) -> None:
        if self.window is not None:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
