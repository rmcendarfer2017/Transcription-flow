"""Speech models for the running app: which are installed, which to start
with, downloading one with progress and loading it (review-m2 F6, QA B3).

Starting never downloads: a chosen model that isn't installed yet gives way
to an installed one, so an offline sign-in still starts. Downloads happen
only when the user asks, from the setup window or Settings.
"""

import logging
from pathlib import Path

from . import models

log = logging.getLogger(__name__)


class Cancelled(models.ModelError):
    def __init__(self):
        super().__init__("The download was cancelled.")


class ModelService:
    def __init__(self, models_dir: Path, device: str = "cpu", manifest=None,
                 download=None, engine_factory=None):
        self.folder = Path(models_dir)
        self.device = device
        self._manifest = manifest or models.load_manifest()
        self._download = download  # None: models.download, looked up at call time
        self._engine_factory = engine_factory
        self._suspect = set()  # failed to load: re-check its files before the next try

    def size(self, name: str) -> int:
        return sum(f["size"] for f in self._manifest["models"][name]["files"])

    def names(self) -> list:
        """Every model, smallest first."""
        return sorted(self._manifest["models"], key=self.size)

    def is_installed(self, name: str) -> bool:
        return models.is_installed(name, self.folder, self._manifest)

    def installed(self) -> list:
        return [n for n in self.names() if self.is_installed(n)]

    def needs_download(self, name: str) -> bool:
        return name in self._suspect or not self.is_installed(name)

    def smaller_than(self, name: str):
        """The next smaller model, or None."""
        names = self.names()
        i = names.index(name)
        return names[i - 1] if i else None

    def startup_choice(self, wanted: str):
        """``wanted`` if installed; else the default, else the largest
        installed model; None when nothing is installed."""
        installed = self.installed()
        if wanted in installed:
            return wanted
        if models.DEFAULT_MODEL in installed:
            return models.DEFAULT_MODEL
        return installed[-1] if installed else None

    def download(self, name: str, progress, cancel) -> None:
        """Worker thread. ``progress(done, total)``; ``cancel`` is a
        threading.Event. Raises Cancelled or a ModelError. A cancelled
        download keeps its .part files, so the next try resumes."""

        def report(done, total):
            if cancel.is_set():
                raise Cancelled()
            progress(done, total)

        download = self._download or models.download
        download(name, self.folder, progress=report, verify=name in self._suspect,
                 manifest=self._manifest)

    def load(self, name: str, language: str, model_dir=None):
        """Worker thread: a loaded engine. ``model_dir`` overrides the
        folder (--model-dir). A model that fails to load is re-checked
        file by file before its next try."""
        factory = self._engine_factory
        if factory is None:
            from .engine import WhisperEngine as factory
        folder = model_dir or models.model_path(name, self.folder)
        try:
            engine = factory(folder, device=self.device,
                             language=None if language == "auto" else language).load()
        except Exception:
            self._suspect.add(name)
            raise
        self._suspect.discard(name)
        return engine
