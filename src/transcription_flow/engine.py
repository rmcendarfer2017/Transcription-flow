"""Speech-to-text with faster-whisper, loaded only from a local folder."""

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

COMPUTE_TYPES = {"cpu": "int8", "cuda": "float16"}


class WhisperEngine:
    def __init__(self, model_dir: Path, device: str = "cpu", language: str = "en"):
        self.model_dir = Path(model_dir)
        self.device = device
        self.language = language
        self._model = None

    def load(self) -> "WhisperEngine":
        if self._model is None:
            from faster_whisper import WhisperModel

            started = time.monotonic()
            self._model = WhisperModel(
                str(self.model_dir), device=self.device,
                compute_type=COMPUTE_TYPES[self.device], local_files_only=True,
            )
            log.info("model %s loaded on %s in %.1f s", self.model_dir.name, self.device,
                     time.monotonic() - started)
        return self

    def transcribe(self, audio) -> str:
        self.load()
        segments, _info = self._model.transcribe(audio, language=self.language, vad_filter=True)
        return "".join(segment.text for segment in segments).strip()
