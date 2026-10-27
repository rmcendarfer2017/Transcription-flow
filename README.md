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
- The clipboard is untouched by default: the text is typed, not pasted.
  Paste mode (an option for apps that drop typed keys) puts the text on the
  clipboard marked "not for clipboard history or cloud sync", pastes it, and
  then puts your previous clipboard back.
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

Hold **Right Ctrl** (or the key you picked in Settings), speak, release. The
transcription is typed at the current cursor position.

- Pressing any other key while the hotkey is held (for example Right Ctrl+C)
  cancels the recording, so shortcuts don't turn into dictation.
- If the key's release is missed (focus moved to an admin window, the UAC
  prompt or the lock screen), the recording is cancelled after about 1.5 s
  (longer if Filter Keys sets a slow key repeat), never
  typed into whatever has focus then, and a notification says so.
- With Filter Keys set to no key repeat, a missed release of Caps Lock,
  Scroll Lock or an F key can't be noticed. Then a recording that reaches
  the 5-minute limit is dropped, not typed, and a one-time notification
  says so. Right Ctrl doesn't have this limit.
- Clips shorter than 0.3 s are ignored; a recording stops by itself after
  5 minutes.
- The microphone is opened only while you hold the hotkey, so Windows doesn't
  show the microphone as in use and Bluetooth headsets keep their full audio
  quality. "Keep the microphone open" in Settings (or `--keep-mic-warm`)
  keeps it open instead and catches the first syllable.
- Windows doesn't let normal apps send keys to windows that run as
  administrator (Task Manager, admin terminals, some installers), so
  dictation can't type there. Running Transcription Flow as administrator is
  not the fix.
- Only one copy runs at a time; a second launch exits.

### The tray icon

The pill icon in the notification area shows the status (Ready, Paused or
the last problem, such as a blocked microphone) and has **Pause dictation**,
**Settings...**, **Open log folder** and **Quit**. Problems also show as a
Windows notification. While dictation is paused, the hotkey works as a normal
key again (Caps Lock toggles, F12 opens developer tools).

### Settings

- **Hold to dictate:** Right Ctrl (default), Caps Lock, Scroll Lock or
  F8-F12. Caps Lock, Scroll Lock and the F keys no longer reach other apps
  while they are the hotkey (unless dictation is paused). Right Alt is not
  offered: on many European keyboard layouts it is AltGr, which types
  characters such as @ and €, and on a US layout releasing it opens the
  app's menu bar. Right Shift is not offered either: holding it for 8
  seconds opens the Windows Filter Keys dialog.
- **Speech model:** tiny.en, base.en or small.en (recommended). The English
  models (`.en`) need no language setting.
- **Output:** type the text (default), or paste it and restore the
  clipboard.
- **Keep the microphone open** between dictations.
- **Start with Windows:** a per-user entry under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (no admin rights).

A new model is used the next time the app starts. Settings are saved in
`%APPDATA%\TranscriptionFlow\config.json` (UTF-8); a damaged file, or one
saved in another encoding, is kept as `config.json.bad` and the defaults are
used. One setting is in the file only: `paste_settle_seconds` (default 0.75,
0.1 to 5) is how long an app gets to read the pasted text before your
clipboard is put back. Raise it if paste mode sometimes pastes your old
clipboard (busy apps or Remote Desktop); it is read when the app starts.

If you turned the app off in Task Manager's Startup tab, Settings shows
"Start with Windows" unticked; ticking it and saving turns it back on.

Command-line options (`venv\Scripts\python dictation.py --help`):

| Option | What it does |
|---|---|
| `--model NAME` | `tiny.en`, `base.en` or `small.en` for this run (default: the one in Settings) |
| `--device auto` | use an NVIDIA GPU if the GPU probe passes (default `cpu`) |
| `--keep-mic-warm` | keep the microphone open between dictations |
| `--download-model` | download and verify the model, then exit |
| `--verify` | re-check the SHA-256 of every installed model file (re-downloads any that fail), then exit |
| `--reset-config` | put every setting back to its default, then exit |
| `--tray` | started at sign-in (used by Start with Windows) |
| `--selftest WAV` | transcribe a WAV file on the CPU and print JSON (no UI) |

Models and logs live in `%LOCALAPPDATA%\TranscriptionFlow`. On start the app
checks only that the model's files are there at the right sizes; run
`--verify` if you think a model file is damaged.

## Run on startup (Windows)

Run the app once from a console first (`venv\Scripts\python dictation.py`):
the first start downloads the `small.en` model, about 480 MB, and without a
console window you would see no sign of it (a first-run window comes in a
later version). Then tick **Start with Windows** in Settings. It starts the
app with `pythonw.exe`, without a console window; messages then go to the
tray and the log file.

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
  app.py             wires hotkey, controller, tray and settings together
  config.py          settings file (versioned, checked, atomic save)
  hotkey.py          hold-to-talk key (pynput hook that only queues events)
  output.py          typing, or paste-and-restore
  clipboard.py       the Windows clipboard, for paste mode
  autostart.py       Start with Windows (HKCU Run value)
  ui/overlay.py      the on-screen pill
  ui/tray.py         the tray icon (pystray)
  ui/settings.py     the settings window (Tk)
  ui/display.py      DPI awareness and the screen's work area
dictation.py         runs the app from a source checkout
tests/               pytest suite (no microphone, hotkey or network needed)
```

`requirements-lock.txt` pins every dependency with hashes;
`requirements-dev-lock.txt` adds pytest and Pillow (for the tray icon
test on Linux). Both are generated from
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

Tests that need Windows (the real clipboard, registry and Tk windows) skip
elsewhere and run in the windows-latest CI job. The two https-redirect
tests skip when `openssl` is not on PATH (it makes their test certificate).

The test clip was made with the Kokoro text-to-speech model (Apache-2.0).

## Third-party licences

pynput (hotkey and typing) and pystray (tray icon) are LGPL-3.0; they are
used as unmodified, separate modules. faster-whisper, ctranslate2 and the
Whisper models are MIT.
