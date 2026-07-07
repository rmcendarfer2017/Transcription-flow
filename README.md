# Transcription Flow

Local push-to-talk dictation. Hold a hotkey, speak, release, and the transcribed
text is typed into whatever app has focus — using a local Whisper model, no
cloud calls, no per-minute cost.

A small black pill with a white sound-wave indicator appears at the bottom
center of the screen while recording/transcribing.

## Setup

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python dictation.py
```

Requires an NVIDIA GPU + CUDA for fastest transcription; falls back to CPU
automatically if unavailable.

## Usage

Hold **Right Ctrl**, speak, release. The transcription is typed at the
current cursor position and also copied to the clipboard.

## Run on startup (Windows)

Create a shortcut in `shell:startup` targeting:

```
venv\Scripts\pythonw.exe dictation.py
```

with the working directory set to this project folder. `pythonw.exe` runs
without a console window.
