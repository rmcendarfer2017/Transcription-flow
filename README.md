# Transcription Flow

Local push-to-talk dictation for Windows, inspired by Wispr Flow. Hold a
hotkey, speak, release, and the transcribed text is typed into whatever app
has focus. It uses a local Whisper model (faster-whisper), so there is no
per-minute cost and your voice never leaves your PC.

A small black pill with a white sound-wave indicator appears at the bottom
center of the screen while recording/transcribing.

## Privacy

- Audio is recorded and transcribed on your PC only.
- The only network use is a one-time download of the speech model from
  Hugging Face (a pinned version, checked against SHA-256 checksums). After
  that, starting the app makes no network calls.
- onnxruntime telemetry and Hugging Face telemetry are switched off.
- The clipboard is never touched: the text is typed, not pasted.
- The log file records timings and lengths, never what you said.

## Setup

Needs Python 3.12 or 3.13 (the python.org installer, which includes Tk).

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python dictation.py
```

On first start the app downloads the `small.en` model (about 480 MB) with a
progress line, then loads it from disk on every later start.

Transcription runs on the CPU by default, which is fast enough for `small.en`.
`--device auto` uses an NVIDIA GPU, but only after a safe test run in a
separate process shows it works (GPU mode needs the cuBLAS 12 and cuDNN 9
DLLs, which pip does not install).

## Usage

Hold **Right Ctrl**, speak, release. The transcription is typed at the
current cursor position.

- Pressing any other key while Right Ctrl is held (for example Right Ctrl+C)
  cancels the recording, so shortcuts don't turn into dictation.
- Clips shorter than 0.3 s are ignored; a recording stops by itself after
  5 minutes.
- The microphone is opened only while you hold the hotkey, so Windows doesn't
  show the microphone as in use and Bluetooth headsets keep their full audio
  quality. `--keep-mic-warm` keeps it open instead (slightly faster start).
- If no microphone is found, or Windows' microphone privacy setting blocks
  it, the app prints a clear message in its console and log instead of
  typing nothing (tray notifications come later).
- Only one copy runs at a time; a second launch exits.

Command-line options (`venv\Scripts\python dictation.py --help`):

| Option | What it does |
|---|---|
| `--model NAME` | `tiny.en`, `base.en` or `small.en` (default) |
| `--device auto` | use an NVIDIA GPU if the GPU probe passes (default `cpu`) |
| `--keep-mic-warm` | keep the microphone open between dictations |
| `--download-model` | download and verify `--model`, then exit |
| `--verify` | re-check the SHA-256 of every installed model file (re-downloads any that fail), then exit |
| `--selftest WAV` | transcribe a WAV file on the CPU and print JSON (no UI) |

Models and logs live in `%LOCALAPPDATA%\TranscriptionFlow`. On start the app
checks only that the model's files are there at the right sizes; run
`--verify` if you think a model file is damaged.

## Run on startup (Windows)

Run the app once from a console first (`venv\Scripts\python dictation.py`):
the first start downloads the `small.en` model, about 480 MB, and without a
console window you would see no sign of it.

Then create a shortcut in `shell:startup` whose target uses absolute paths,
for example (if the project is in `C:\Tools\Transcription-flow`):

```
C:\Tools\Transcription-flow\venv\Scripts\pythonw.exe C:\Tools\Transcription-flow\dictation.py
```

with "Start in" set to the same project folder. `pythonw.exe` runs without a
console window; messages then go to the log file only.

## For developers

```
src/transcription_flow/
  main.py            entry point and command-line options
  controller.py      the dictation state machine (one transcription worker)
  recorder.py        microphone capture, opened on press
  engine.py          faster-whisper, loaded from a local folder only
  models.py          model manifest and downloader (resume, checksums)
  models_manifest.json
  gpu_probe.py       CUDA test run in a child process
  single_instance.py one copy at a time
  hotkey.py          hold-to-talk key handling (Windows `keyboard` library)
  output.py          typing the text
  ui/overlay.py      the on-screen pill
dictation.py         runs the app from a source checkout
tests/               pytest suite (no microphone, hotkey or network needed)
```

`requirements-lock.txt` pins every dependency with hashes;
`requirements-dev-lock.txt` adds pytest. Both are generated from
`pyproject.toml` with `uv pip compile --universal --generate-hashes`.

Run the tests (Windows paths shown; on Linux use `venv/bin/`):

```
venv\Scripts\pip install --require-hashes -r requirements-dev-lock.txt
venv\Scripts\python -m pytest
```

The slow test that transcribes `tests/fixtures/hello.wav` with a real
`tiny.en` model runs when `TFLOW_TEST_MODELS_DIR` points at a folder that
holds it:

```
set PYTHONPATH=src
venv\Scripts\python -m transcription_flow --download-model --model tiny.en --models-dir .ci-models
set TFLOW_TEST_MODELS_DIR=%CD%\.ci-models
venv\Scripts\python -m pytest
```

The test clip was made with the Kokoro text-to-speech model (Apache-2.0).
