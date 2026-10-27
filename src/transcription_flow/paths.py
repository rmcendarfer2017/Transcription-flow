"""Where the app keeps its data (models, logs, caches).

Windows: %LOCALAPPDATA%\\TranscriptionFlow (so an uninstaller can remove it).
Elsewhere: $XDG_DATA_HOME/transcription-flow (dev and CI only).
TFLOW_HOME overrides both (tests and portable use).
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
