"""Privacy environment, applied by the package __init__ before anything else.

onnxruntime (used by the Silero VAD) sends telemetry unless
ORT_DISABLE_TELEMETRY is set before it is imported. The HF variables make
sure faster-whisper's huggingface_hub never goes online or prints progress
bars to a missing stderr: we download models ourselves (models.py).
"""

import os

PRIVACY_ENV = {
    "ORT_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "TQDM_DISABLE": "1",
}


def apply(environ=None):
    """Set the privacy variables (overriding any earlier value)."""
    (os.environ if environ is None else environ).update(PRIVACY_ENV)
