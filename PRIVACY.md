# Privacy

Transcription Flow is built so that what you say stays on your PC. This page
lists everything the app does with your voice, your text and the network.
It describes the code in this repository; if anything here and the code
disagree, please [open an issue](https://github.com/rmcendarfer2017/Transcription-flow/issues).

## In short

- Your voice is recorded and transcribed on your PC. It is never uploaded.
- The only network use is a one-time download of the speech model.
- No telemetry, no analytics, no accounts, no update checks.
- Logs never contain what you said.
- In the default "type" mode the clipboard is not touched.

## What runs offline

Everything that handles your voice:

- **Recording.** The microphone opens only while you hold the dictation
  key (unless you turn on "Keep the microphone open" in Settings), and
  while a level meter is showing: the microphone check in the setup window
  and **Test** (or picking a microphone) in Settings. The audio is kept in
  memory for that one clip and is never written to disk; level-meter audio
  is not kept at all.
- **Transcription.** A Whisper model runs locally through
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on your CPU.
  The speech detector (Silero VAD, through ONNX Runtime) runs locally too.
- **Typing.** The text is sent to the app that has focus as key presses,
  like a keyboard would.

Once a model is downloaded, starting and using the app makes no network
calls. It never downloads anything at start, so it also starts offline.

## The one-time model download

The first-run setup window (or Settings, when you pick another model)
downloads one speech model:

- **From:** Hugging Face, `https://huggingface.co/<repo>/resolve/<revision>/<file>`,
  where the repository is one of `Systran/faster-whisper-tiny.en`,
  `Systran/faster-whisper-base.en` or `Systran/faster-whisper-small.en`,
  and the revision is a fixed commit pinned in the app.
- **Checked:** every file's size and SHA-256 checksum must match the values
  built into the app
  ([`src/transcription_flow/models_manifest.json`](src/transcription_flow/models_manifest.json)),
  or the file is rejected. `--verify` re-checks the installed files.
- **What is sent:** an ordinary HTTPS file request. The only identifying
  header the app adds is a User-Agent with the app's name, version and this
  repository's URL. No account, token or device ID is sent. Hugging Face
  sees your IP address like any web server does.
- **Where it goes:** `%LOCALAPPDATA%\TranscriptionFlow\models\`.
- **Size:** tiny.en about 78 MB, base.en about 148 MB, small.en (the
  recommended one) about 486 MB. These are decimal megabytes, as the app
  shows them; Windows Explorer counts 1 MB as 1,048,576 bytes, so it shows
  small.en as about 463 MB.

The app does its own download and does not use the `huggingface_hub`
download code, so there are no revision checks or Hugging Face telemetry.
If you would rather not download from inside the app, the setup window has
**Open model folder**: copy the same files there by hand.

## No telemetry

The app has no telemetry, analytics, crash reporting or update checks. Two
libraries it uses could send usage data by default; the app turns that off
before either is loaded:

| Variable | Why |
|---|---|
| `ORT_DISABLE_TELEMETRY=1` | ONNX Runtime (speech detection) |
| `HF_HUB_DISABLE_TELEMETRY=1`, `HF_HUB_OFFLINE=1` | Hugging Face Hub code inside faster-whisper: never goes online |

See [`src/transcription_flow/_env.py`](src/transcription_flow/_env.py). A
test checks that these are set before those libraries can be imported.

## Logs

The log file is `%LOCALAPPDATA%\TranscriptionFlow\logs\app.log` (it rotates
at 1 MB and keeps 3 old files). It records events, timings and lengths, for
example "clip of 2.40 s queued" or "transcribed 42 characters". It never
records the text of a transcript, not even when an error happens; a test
checks this. Logs stay on your PC: **Open log folder** in the tray menu
shows them, and nothing sends them anywhere.

## The clipboard

- **Type mode (the default):** the clipboard is not read or changed.
- **Paste mode (optional, in Settings):** for apps that drop typed keys.
  The app saves your current clipboard, puts the transcript on it, presses
  Ctrl+V, waits a moment (0.75 s by default) and then puts your previous
  clipboard back. Text, rich text, HTML, pictures and copied files come back;
  a few formats that some apps keep for themselves (private and drawing-handle
  formats) are not saved, so after a paste such an app may offer only the
  plain text or picture of what you had copied. While the transcript is on the clipboard it is marked so
  that Windows leaves it out of clipboard history (Win+V), cloud clipboard
  sync and clipboard monitors. If another app changes the clipboard during
  the paste, the app does not overwrite that change.

## What is stored on your PC

| Where | What |
|---|---|
| `%LOCALAPPDATA%\Programs\Transcription Flow` | The app (installer version) |
| `%LOCALAPPDATA%\TranscriptionFlow` | Downloaded models and logs |
| `%APPDATA%\TranscriptionFlow\config.json` | Your settings |
| `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` | The "Start with Windows" entry, if on |

No audio and no transcripts are stored. Uninstalling removes the app and
can remove the two data folders too (see the README).
