"""Settings in a small versioned JSON file (``paths.config_file()``).

Every value is checked on load: an unknown, invalid or missing value falls
back to its default with a log line, so a hand-edited or damaged file can
never stop the app. A file that isn't JSON at all is kept as
``config.json.bad`` for the user. Saves are atomic (temp file + replace), so
a crash mid-save leaves the old file. "Start with Windows" is not stored
here: the Run registry value is its single source of truth (plan 3.6).
"""

import dataclasses
import json
import logging
import os
import tempfile
from pathlib import Path

from .hotkey import DEFAULT_HOTKEY, HOTKEYS
from .models import DEFAULT_MODEL, load_manifest
from .output import DEFAULT_OUTPUT, OUTPUT_MODES

log = logging.getLogger(__name__)

VERSION = 1
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


def _checks() -> dict:
    return {
        "hotkey": lambda v: v in HOTKEYS,
        "model": lambda v: v in model_names(),
        "language": lambda v: isinstance(v, str),  # checked against the model below
        "output": lambda v: v in OUTPUT_MODES,
        "keep_mic_warm": lambda v: isinstance(v, bool),
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
    checks = _checks()
    for key in sorted(set(data) - set(checks) - {"version"}):
        log.warning("config: unknown setting %r ignored", key)
    for key, ok in checks.items():
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
    try:
        raw = path.read_text(encoding="utf-8-sig")  # Notepad may add a BOM
    except FileNotFoundError:
        return Config()
    except OSError as err:
        log.warning("config: could not read the settings (%s); using the defaults",
                    type(err).__name__)
        return Config()
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("config: the settings file is not valid JSON; kept as %s.bad, "
                    "using the defaults", path.name)
        try:
            os.replace(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return Config()
    return from_dict(data)


def save(config: Config, path: Path) -> None:
    """Write atomically: a crash leaves either the old or the new file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"version": VERSION, **dataclasses.asdict(config)}, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
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
