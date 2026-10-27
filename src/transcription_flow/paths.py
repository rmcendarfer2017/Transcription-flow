"""Where the app keeps its data (models, logs, caches) and its settings.

Data, Windows: %LOCALAPPDATA%\\TranscriptionFlow (so an uninstaller can remove it).
Settings, Windows: %APPDATA%\\TranscriptionFlow\\config.json.
Elsewhere: $XDG_DATA_HOME and $XDG_CONFIG_HOME /transcription-flow (dev and CI only).
TFLOW_HOME overrides all of them with one folder (tests and portable use).
"""

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("TFLOW_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "TranscriptionFlow"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "transcription-flow"


def models_dir() -> Path:
    return data_dir() / "models"


def logs_dir() -> Path:
    return data_dir() / "logs"


def config_file() -> Path:
    override = os.environ.get("TFLOW_HOME")
    if override:
        return Path(override) / "config.json"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "TranscriptionFlow" / "config.json"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "transcription-flow" / "config.json"
