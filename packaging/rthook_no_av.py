"""PyInstaller runtime hook: stand in for PyAV, which is not bundled.

faster_whisper imports av at module level but uses it only in
decode_audio(), for audio files. The app always passes numpy arrays, so a
stand-in that fails on use keeps the bundle free of PyAV's FFmpeg build
(x264/x265, GPL). See packaging/buildinfo.py EXCLUDES.
"""

import sys
import types


class _NoAV(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise ImportError("PyAV is not bundled with Transcription Flow; "
                          "pass audio to faster-whisper as a numpy array")


sys.modules.setdefault("av", _NoAV("av"))
