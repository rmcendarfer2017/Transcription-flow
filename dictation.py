"""Dev shim: run Transcription Flow from a source checkout (python dictation.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from transcription_flow.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
