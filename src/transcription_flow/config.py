"""Settings in a small versioned JSON file (``paths.config_file()``).

Every value is checked on load: an unknown, invalid or missing value (a
number, list or object where a name belongs, too) falls back to its default
with a log line, so a hand-edited or damaged file can never stop the app. A
file that can't be read as UTF-8 JSON (not JSON, saved as "Unicode" or
"ANSI" by Notepad, nested too deeply) is kept as ``config.json.bad`` for the
user. Saves are atomic (temp file + replace), so a crash mid-save leaves the
old file; a temp file a killed save left behind is removed at the next load.
"Start with Windows" is not stored here: the Run registry value is its single
source of truth (plan 3.6).
"""

import dataclasses
import json
import logging
import os
import tempfile
from pathlib import Path

from .hotkey import DEFAULT_HOTKEY, HOTKEYS
from .models import DEFAULT_MODEL, load_manifest
from .output import DEFAULT_OUTPUT, OUTPUT_MODES, PASTE_SETTLE_SECONDS

log = logging.getLogger(__name__)

VERSION = 1
SETTLE_RANGE = (0.1, 5.0)  # seconds
MAX_MIC_NAME = 256  # device names are short (MME cuts them at 31)
TEMP_PREFIX, TEMP_SUFFIX = ".config-", ".tmp"
COMMON_LANGUAGES = {"en": "English", "de": "German", "es": "Spanish", "fr": "French",
                    "it": "Italian", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese"}


def languages_for(model: str) -> dict:
    """The languages a model can be told to expect: English-only models
    (``*.en``) know only English; multilingual ones can also detect it."""
    if model.endswith(".en"):
        return {"en": "English"}
    return {"auto": "Detect automatically", **COMMON_LANGUAGES}


def model_names() -> list:
    return list(load_manifest()["models"])


@dataclasses.dataclass
class Config:
    hotkey: str = DEFAULT_HOTKEY
    model: str = DEFAULT_MODEL
    language: str = "en"
    output: str = DEFAULT_OUTPUT
    keep_mic_warm: bool = False
    mic: str = ""  # a device name; "" is the Windows default microphone
    # How long Ctrl+V gets before the clipboard is put back (review-m2 F7).
    # In the file only, for apps that read the clipboard late.
    paste_settle_seconds: float = PASTE_SETTLE_SECONDS


# isinstance first: a list or object from the file can't be looked up in a dict.
CHECKS = {
    "hotkey": lambda v: isinstance(v, str) and v in HOTKEYS,
    "model": lambda v: isinstance(v, str) and v in model_names(),
    "language": lambda v: isinstance(v, str),  # checked against the model below
    "output": lambda v: isinstance(v, str) and v in OUTPUT_MODES,
    "keep_mic_warm": lambda v: isinstance(v, bool),
    "mic": lambda v: isinstance(v, str) and len(v) <= MAX_MIC_NAME,
    "paste_settle_seconds": lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool)
                                       and SETTLE_RANGE[0] <= v <= SETTLE_RANGE[1]),
}


def from_dict(data) -> Config:
    """A valid Config from whatever was in the file."""
    config = Config()
    if not isinstance(data, dict):
        log.warning("config: not a JSON object; using the defaults")
        return config
    version = data.get("version")
    if version != VERSION:
        log.warning("config: version %r, expected %d; reading the settings it shares",
                    version, VERSION)
    for key in sorted(set(data) - set(CHECKS) - {"version"}):
        log.warning("config: unknown setting %r ignored", key)
    for key, ok in CHECKS.items():
        if key not in data:
            continue
        value = data[key]
        if ok(value):
            setattr(config, key, value)
        else:
            log.warning("config: invalid %s, using the default", key)
    if config.language not in languages_for(config.model):
        log.warning("config: language %r does not suit %s, using the default",
                    config.language, config.model)
        config.language = next(iter(languages_for(config.model)))
    return config


def load(path: Path) -> Config:
    path = Path(path)
    _remove_stale_temp_files(path.parent)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Config()
    except OSError as err:
        log.warning("config: could not read the settings (%s); using the defaults",
                    type(err).__name__)
        return Config()
    try:
        # utf-8-sig: Notepad may add a BOM. UnicodeDecodeError is a
        # ValueError; a deeply nested file raises RecursionError.
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        _keep_aside(path)
        return Config()
    return from_dict(data)


def _keep_aside(path: Path) -> None:
    bad = path.with_name(path.name + ".bad")
    try:
        os.replace(path, bad)
    except OSError as err:
        log.warning("config: the settings file is not valid UTF-8 JSON and could not be "
                    "renamed to %s (%s); using the defaults", bad.name, type(err).__name__)
    else:
        log.warning("config: the settings file is not valid UTF-8 JSON; kept as %s, "
                    "using the defaults", bad.name)


def _remove_stale_temp_files(folder: Path) -> None:
    """Temp files a killed save left behind (the single-instance guard
    means no save of ours is running while we load)."""
    try:
        stale = [p for p in folder.iterdir()
                 if p.name.startswith(TEMP_PREFIX) and p.name.endswith(TEMP_SUFFIX)]
    except OSError:
        return
    for p in stale:
        try:
            p.unlink()
        except OSError:
            pass


def save(config: Config, path: Path) -> None:
    """Write atomically: a crash leaves either the old or the new file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"version": VERSION, **dataclasses.asdict(config)}, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
