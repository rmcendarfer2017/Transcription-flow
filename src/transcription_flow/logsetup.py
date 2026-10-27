"""Logging to a rotating file, safe when stdout/stderr are None.

Under pythonw.exe or a windowed PyInstaller build, sys.stdout and sys.stderr
are None, and any library that prints (tqdm, warnings) crashes. We point them
at the log instead. Never log transcript text: lengths and timings only.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_NAME = "app.log"
MAX_BYTES = 1_000_000
BACKUPS = 3
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _LogStream:
    """A minimal text stream that forwards whole lines to a logger."""

    def __init__(self, logger: logging.Logger, level: int):
        self._logger = logger
        self._level = level
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, "%s", line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            self._logger.log(self._level, "%s", self._buf.rstrip())
        self._buf = ""

    def isatty(self) -> bool:
        return False


def setup(log_dir: Path, level: int = logging.INFO) -> Path:
    """Configure the root logger once; returns the log file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_NAME
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_tflow", False)]:
        root.removeHandler(handler)
        handler.close()

    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler._tflow = True
    root.addHandler(file_handler)

    stdio = logging.getLogger("stdio")
    if sys.stdout is None:
        sys.stdout = _LogStream(stdio, logging.INFO)
    if sys.stderr is None:
        sys.stderr = _LogStream(stdio, logging.WARNING)
    elif not isinstance(sys.stderr, _LogStream):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        console._tflow = True
        root.addHandler(console)

    root.setLevel(level)
    return path
