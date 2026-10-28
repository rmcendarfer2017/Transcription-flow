"""Transcription Flow: local push-to-talk dictation, inspired by Wispr Flow."""

from . import _env

# Runs before any submodule, so before faster-whisper, onnxruntime or
# huggingface_hub can be imported by any route (review M4).
_env.apply()

__version__ = "0.1.0rc1.dev0"
REPO_URL = "https://github.com/rmcendarfer2017/Transcription-flow"
