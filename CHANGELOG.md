# Changelog

All notable changes to Transcription Flow are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[PEP 440](https://peps.python.org/pep-0440/) (`0.1.0`, `0.2.0rc1`).

A release build takes its notes from the section for its version, so the
section must carry the release date (`## [0.1.0] - YYYY-MM-DD`) in the
commit that is tagged.

## [0.1.1] - Unreleased

### Removed

- `docs/images/pill.png`, a still of the pill that nothing linked to (the
  README shows the GIF).

### Known limitations

- Not code-signed: Windows SmartScreen warns the first time (see
  [the SmartScreen guide](https://github.com/rmcendarfer2017/Transcription-flow/blob/main/docs/smartscreen.md)).

## [0.1.0] - 2026-11-02

The first Windows release: a per-user installer and a portable zip. It is
the same app as 0.1.0rc1, which was tested on a Windows laptop. The app
runs on the CPU and is not code-signed yet.

### Added

- **Hold-to-talk dictation.** Hold Right Ctrl (or Caps Lock, Scroll Lock or
  F8-F12), speak and let go: the text is typed where the cursor is. A small
  pill shows while you speak and while the text is transcribed. Pressing
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

### Since 0.1.0rc1

- The README and the SmartScreen guide now have pictures of the pill, the
  setup window, the SmartScreen warning and the installer.

### Known limitations

- CPU only; on an older laptop `base.en` or `tiny.en` is faster than
  `small.en`.
- Not code-signed: Windows SmartScreen warns the first time (see
  [the SmartScreen guide](https://github.com/rmcendarfer2017/Transcription-flow/blob/main/docs/smartscreen.md)).
- Dictation cannot type into windows that run as administrator.
- English models only.

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
