"""The settings window (Tk/ttk, one page).

``Form`` and the helpers above the window class are plain Python, so the
choices and the round trip to ``Config`` are tested without a display. The
window imports tkinter only when it opens and runs on the Tk thread.
"""

import dataclasses
import logging

from ..config import Config, languages_for
from ..hotkey import HOTKEYS
from ..models import DEFAULT_MODEL, load_manifest
from ..output import OUTPUT_MODES

log = logging.getLogger(__name__)

TITLE = "Transcription Flow settings"
RESTART_NOTE = "A new model or language is used the next time Transcription Flow starts."


@dataclasses.dataclass
class Form:
    hotkey: str
    model: str
    language: str
    output: str
    keep_mic_warm: bool
    start_with_windows: bool

    @classmethod
    def from_config(cls, config: Config, start_with_windows: bool) -> "Form":
        return cls(config.hotkey, config.model, config.language, config.output,
                   config.keep_mic_warm, start_with_windows)

    def to_config(self, base: Config = None) -> Config:
        """``base`` keeps the settings the form doesn't show (file-only ones)."""
        language = self.language
        if language not in languages_for(self.model):
            language = next(iter(languages_for(self.model)))
        return dataclasses.replace(base or Config(), hotkey=self.hotkey, model=self.model,
                                   language=language, output=self.output,
                                   keep_mic_warm=self.keep_mic_warm)


def model_choices() -> dict:
    """name -> label with the download size, smallest first."""
    models = load_manifest()["models"]
    sizes = {name: sum(f["size"] for f in spec["files"]) for name, spec in models.items()}
    labels = {}
    for name in sorted(models, key=sizes.get):
        mb = sizes[name] / 1e6
        size = f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"
        tip = ", recommended" if name == DEFAULT_MODEL else ""
        labels[name] = f"{name} ({size}{tip})"
    return labels


def hotkey_choices() -> dict:
    return {key: spec.label for key, spec in HOTKEYS.items()}


class SettingsWindow:
    """One Toplevel; ``on_save(form)`` runs on the Tk thread."""

    def __init__(self, parent, form: Form, autostart_available: bool, on_save, on_close=None):
        import tkinter as tk
        from tkinter import ttk

        self._on_save = on_save
        self._on_close = on_close
        self._hotkeys, self._models, self._outputs = (hotkey_choices(), model_choices(),
                                                      dict(OUTPUT_MODES))
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

        self.keep_warm = tk.BooleanVar(value=form.keep_mic_warm)
        ttk.Checkbutton(frame, text="Keep the microphone open between dictations "
                        "(catches the first word; the mic-in-use icon stays on)",
                        variable=self.keep_warm).grid(row=4, column=0, columnspan=2,
                                                      sticky="w", pady=(8, 0))
        self.autostart = tk.BooleanVar(value=form.start_with_windows)
        check = ttk.Checkbutton(frame, text="Start with Windows", variable=self.autostart)
        check.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        if not autostart_available:
            check.state(["disabled"])
        ttk.Label(frame, text=RESTART_NOTE, wraplength=360).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self.close).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Save", command=self.save, default="active").grid(row=0,
                                                                                    column=1)
        win.bind("<Escape>", lambda e: self.close())
        self._update_languages()
        win.lift()
        win.focus_force()

    @staticmethod
    def _combo(frame, row, label, choices: dict, value):
        from tkinter import ttk

        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        combo = ttk.Combobox(frame, state="readonly", values=list(choices.values()), width=36)
        combo.set(choices.get(value, next(iter(choices.values()))))
        combo.grid(row=row, column=1, sticky="ew", pady=4)
        return combo

    @staticmethod
    def _key(combo, choices: dict):
        label = combo.get()
        return next(key for key, text in choices.items() if text == label)

    def _update_languages(self) -> None:
        current = self._key(self.language, self._languages)
        self._languages = languages_for(self._key(self.model, self._models))
        self.language.configure(values=list(self._languages.values()))
        self.language.set(self._languages.get(current, next(iter(self._languages.values()))))
        self.language.state(["disabled"] if len(self._languages) == 1 else ["!disabled",
                                                                             "readonly"])

    def form(self) -> Form:
        return Form(hotkey=self._key(self.hotkey, self._hotkeys),
                    model=self._key(self.model, self._models),
                    language=self._key(self.language, self._languages),
                    output=self._key(self.output, self._outputs),
                    keep_mic_warm=bool(self.keep_warm.get()),
                    start_with_windows=bool(self.autostart.get()))

    def save(self) -> None:
        form = self.form()
        self.close()
        self._on_save(form)

    def close(self) -> None:
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
