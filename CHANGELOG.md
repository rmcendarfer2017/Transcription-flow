# Changelog

All notable changes to Transcription Flow are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[PEP 440](https://peps.python.org/pep-0440/) (`0.1.0`, `0.2.0rc1`).

A release build takes its notes from the section for its version, so the
section must carry the release date (`## [0.1.0] - YYYY-MM-DD`) in the
commit that is tagged.

## [0.1.0] - Unreleased

The first Windows release: a per-user installer and a portable zip. The app
runs on the CPU and is not code-signed yet.

### Added

- **Installer.** A per-user Inno Setup installer (no admin prompt) that
  installs to `%LOCALAPPDATA%\Programs\Transcription Flow`, adds a Start
  menu entry, and offers "Start when I sign in to Windows". An upgrade keeps
  the autostart choice made later in Settings or Task Manager.
- **Portable zip.** The same app folder as a zip, with an
  `Uninstall portable.txt` note.
- **Uninstall** from Settings > Apps. It removes the app, its Start menu
  entry and its autostart entry, and asks whether to delete the downloaded
  models and settings too (the default is No). Setup and uninstall ask you
  to quit a running copy first.
- **Hold-to-talk dictation.** Hold Right Ctrl (or Caps Lock, Scroll Lock or
  F8-F12), speak, release: the text is typed where the cursor is. Pressing
  another key while the hotkey is held cancels the recording, and a missed
  key release (an admin window, UAC, the lock screen) cancels it instead of
  typing into whatever has focus.
- **First-run setup window.** A microphone check with a level meter (and a
  button to the Windows microphone privacy page when it is blocked), the
  model download with progress, cancel, Retry, "Use a smaller model" and
  "Open model folder", the hotkey choice, and a box to try dictation. It
  opens again at the next start until a model is downloaded.
- **Tray icon** with the status line, Pause dictation, Settings, Open log
  folder and Quit; problems also show as Windows notifications.
- **Settings window.** Hotkey, speech model (tiny.en, base.en, small.en),
  microphone (with a Test level meter), output mode (type, or paste and
  restore the clipboard), "Keep the microphone open", and Start with
  Windows. A model chosen here downloads in the background while the current
  one keeps working.
- **Model downloader.** Pinned model revisions from Hugging Face, a
  SHA-256 check of every file, resume after an interrupted download, and
  `--verify` to re-check installed models. After the download the app makes
  no network calls.
- **Offline-safe start.** If the chosen model is missing or damaged, the
  app starts with another downloaded one and says so; it never downloads at
  start.
- **Filter for Whisper's stock phrases** ("Thanks for watching!") on clips
  with little or no speech.
- **One copy at a time.** Launching the app again opens the running copy's
  window instead of starting a second one.
- Command-line options for testing: `--selftest`, `--verify`,
  `--download-model`, `--reset-config`, `--model`, `--keep-mic-warm`.

### Changed (from the original `dictation.py` script)

- The microphone opens only while the hotkey is held (Bluetooth headsets
  keep their full audio quality); "Keep the microphone open" is optional.
- The clipboard is no longer used by default: the text is typed. Paste mode
  is an option.
- The hotkey uses pynput instead of the unmaintained `keyboard` package.
- One transcription worker with a clear state machine replaces the old
  threads, and a recording stops by itself after 5 minutes.
- onnxruntime and Hugging Face telemetry are switched off before they load.
- Logs record timings and lengths, never what was said.

### Known limitations

- CPU only (no GPU build yet). On an older laptop, `base.en` or `tiny.en`
  is faster than `small.en`.
- Not code-signed: Windows SmartScreen shows "Windows protected your PC"
  the first time (see
  [the SmartScreen guide](https://github.com/rmcendarfer2017/Transcription-flow/blob/main/docs/smartscreen.md)).
- Windows does not let normal apps type into windows that run as
  administrator, so dictation cannot type there.
- Settings offers English models only (tiny.en, base.en, small.en).

## [0.1.0rc1] - 2026-10-28

A release candidate for 0.1.0, the first Windows release, built to test
the release steps and the installer before 0.1.0 itself. It is a GitHub
pre-release, so the "latest release" download link does not point at it.
The app runs on the CPU and is not code-signed yet.

### Added

- **Hold-to-talk dictation.** Hold Right Ctrl (or Caps Lock, Scroll Lock or
  F8-F12), speak and let go: the text is typed where the cursor is. Pressing
  another key while holding the hotkey, or a key release Windows never
  delivers, cancels the recording instead of typing.
- **Per-user installer** (no admin prompt) with a Start menu entry and an
  optional "Start when I sign in to Windows", and a **portable zip** of the
  same app. Uninstall from Settings > Apps; it asks before deleting the
  downloaded models and settings.
- **First-run setup window**: microphone check with a level meter, model
  download with progress, cancel and retry, hotkey choice, and a box to try
  dictation.
- **Tray icon** (status, Pause dictation, Settings, Open log folder, Quit)
  and a **Settings window** (hotkey, speech model, microphone, type or
  paste output, keep the microphone open, start with Windows).
- **Checked model downloads**: pinned model versions, a SHA-256 check of
  every file and resume after an interruption. After the download the app
  makes no network calls, and it never downloads at start.
- If the chosen model is missing or damaged, the app starts with another
  downloaded one and says so.
- Whisper's stock phrases ("Thanks for watching!") are dropped from clips
  with little or no speech.
- Starting the app a second time opens the running copy's window.

### Changed (from the original `dictation.py` script)

- The microphone is open only while the hotkey is held, and the text is
  typed instead of pasted through the clipboard (paste is an option).
- A new hotkey library, one transcription worker, and a 5-minute limit per
  recording.
- Telemetry in the speech libraries is switched off, and logs never record
  what was said.

### Known limitations

- CPU only; on an older laptop `base.en` or `tiny.en` is faster than
  `small.en`.
- Not code-signed: Windows SmartScreen warns the first time (see
  [the SmartScreen guide](https://github.com/rmcendarfer2017/Transcription-flow/blob/main/docs/smartscreen.md)).
- Dictation cannot type into windows that run as administrator.
- English models only.
