# Transcription Flow

Hold a key, speak, let go: your words are typed into whatever app you're
using. Local push-to-talk dictation for Windows, inspired by Wispr Flow. It
runs a Whisper speech model on your own PC, so your voice never leaves it
and there is no subscription.

<!-- SCREENSHOT (Ryan): docs/images/pill.gif
     A short GIF of the pill at the bottom of the screen while dictating into Notepad. -->
_Animation to come: the recording pill while dictating into Notepad._

**[Download for Windows](https://github.com/rmcendarfer2017/Transcription-flow/releases/latest)**
(Windows 10 or 11, 64-bit; no Python needed)

## Install

1. From the [latest release](https://github.com/rmcendarfer2017/Transcription-flow/releases/latest),
   download `TranscriptionFlow-Setup-<version>.exe`.
2. Double-click it. Because the installer is not code-signed yet, Windows
   shows **"Windows protected your PC"**. Click **More info**, then
   **Run anyway**. [docs/smartscreen.md](docs/smartscreen.md) shows each
   step with screenshots, explains why, and shows how to check the file's
   SHA-256 against the release's `SHA256SUMS.txt` first.
3. The installer installs for your user only (no administrator password)
   to `%LOCALAPPDATA%\Programs\Transcription Flow`, adds a Start menu entry
   and, unless you untick it, starts the app when you sign in.

Can't run installers? The release also has
`TranscriptionFlow-<version>-portable.zip`: extract it (don't run it from
inside the zip) and start `TranscriptionFlow.exe`.

The app runs on the CPU. There is no GPU build yet.

## First launch

A setup window opens:

1. **Microphone check** with a level meter. If it stays flat, Windows may be
   blocking the microphone; the window has a button that opens the Windows
   microphone privacy settings.
2. **Speech model download** with a progress bar. `small.en` (about 486 MB)
   is recommended; `tiny.en` and `base.en` are smaller and faster but less
   accurate. If the download fails, the window offers **Retry**,
   **Use a smaller model** and **Open model folder** (you can copy the
   model files there by hand). A download that was cut short resumes where
   it stopped.
3. **Dictation key** (Right Ctrl by default).
4. **Try it**: hold the key and say something into the box.

<!-- SCREENSHOT (Ryan): docs/images/setup-window.png
     The setup window during the model download. -->
_Screenshot to come: the setup window._

If you close the window before a model is downloaded, it opens again at the
next start. After that the app lives in the notification area (the tray)
and makes no network calls.

## Using it

Hold **Right Ctrl** (or the key you picked), speak, release. A small pill
at the bottom of the screen shows that it is recording, then transcribing,
and the text is typed at the cursor.

- Pressing any other key while the hotkey is held (for example Right Ctrl+C)
  cancels the recording, so shortcuts don't turn into dictation.
- If the key's release is missed (focus moved to an admin window, the UAC
  prompt or the lock screen), the recording is cancelled after about 1.5 s
  (longer if Filter Keys sets a slow key repeat), never
  typed into whatever has focus then, and a notification says so.
- With Filter Keys set to no key repeat, a missed release of Caps Lock,
  Scroll Lock or an F key is noticed only at the lock screen or a UAC
  prompt. In an admin window it can't be, so a recording that reaches the
  5-minute limit is dropped, not typed, and a one-time notification says so.
  Right Ctrl doesn't have this limit.
- When a clip has little or no speech, Whisper sometimes "hears" stock
  phrases from video subtitles ("Thanks for watching!", "Thank you."). These
  are dropped when they are the whole transcript; a sentence that contains
  them is always typed. A quick "Thanks." or "Thank you." that you really
  say is kept: only one on under a quarter of a second of speech (a cough
  or a click) is dropped, and a notification then says nothing was typed.
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
- Only one copy runs at a time. Starting it again (say, from the Start
  menu) opens the running copy's Settings, or its setup window while no
  model is downloaded.

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
  models (`.en`) need no language setting. A model that isn't downloaded yet
  downloads when you save, with the progress in the tray icon's status; the
  current model keeps working until the new one is ready. If the app started
  on another model because the chosen one isn't downloaded (or couldn't be
  loaded), any Save downloads the chosen one; Settings says so.
- **Microphone:** the Windows default, or a specific one. Picking one, or
  pressing **Test**, runs a level meter (opening Settings alone doesn't turn
  the microphone on). A microphone plugged in while the app runs shows up
  the next time Settings opens (with "Keep the microphone open" on, after
  the app restarts: the device list can't be re-read while a microphone is
  open). If the chosen one is unplugged or can't be
  opened, the Windows default is used.
- **Output:** type the text (default), or paste it and restore the
  clipboard.
- **Keep the microphone open** between dictations.
- **Start with Windows:** a per-user entry under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (no admin rights).

If the model in your settings isn't downloaded (a download was cut short,
say) or can't be loaded (a damaged file), the app starts with another
downloaded one and a notification says which; it
never downloads at start, so it starts offline too. Settings are saved in
`%APPDATA%\TranscriptionFlow\config.json` (UTF-8); a damaged file, or one
saved in another encoding, is kept as `config.json.bad` and the defaults are
used. One setting is in the file only: `paste_settle_seconds` (default 0.75,
0.1 to 5) is how long an app gets to read the pasted text before your
clipboard is put back. Raise it if paste mode sometimes pastes your old
clipboard (busy apps or Remote Desktop); it is read when the app starts.

If you turned the app off in Task Manager's Startup tab, Settings shows
"Start with Windows" unticked; ticking it and saving turns it back on.

Command-line options, for a source checkout (`venv\Scripts\python dictation.py --help`):

| Option | What it does |
|---|---|
| `--model NAME` | `tiny.en`, `base.en` or `small.en` for this run (default: the one in Settings) |
| `--device auto` | use an NVIDIA GPU if the GPU probe passes (default `cpu`) |
| `--keep-mic-warm` | keep the microphone open between dictations |
| `--download-model` | download and verify the model, then exit |
| `--verify` | re-check the SHA-256 of every installed model file (re-downloads any that fail), then exit |
| `--reset-config` | put every setting back to its default, then exit |
| `--tray` | started at sign-in (used by Start with Windows); the setup window still opens while no model is downloaded |
| `--selftest WAV` | transcribe a WAV file on the CPU and print JSON (no UI) |

Models and logs live in `%LOCALAPPDATA%\TranscriptionFlow`. On start the app
checks only that the model's files are there at the right sizes; run
`--verify` if you think a model file is damaged.

## Privacy

- Your voice is recorded and transcribed on your PC only. Audio and
  transcripts are never saved or uploaded.
- The only network use is the one-time model download from Hugging Face
  (a pinned version, checked against SHA-256 checksums).
- No telemetry, analytics or update checks.
- The log file records timings and lengths, never what you said.
- In the default type mode the clipboard is untouched. Paste mode (an
  option) puts the text on the clipboard, marked "not for clipboard history
  or cloud sync", pastes it and then puts your previous clipboard back.

[PRIVACY.md](PRIVACY.md) has the details.

## Troubleshooting

- **Nothing is typed.** Check the tray icon's status line. Windows doesn't
  let normal apps type into windows that run as administrator (Task
  Manager, admin terminals, some installers); running Transcription Flow as
  administrator is not the fix. Some apps drop typed keys: try **Paste**
  as the output in Settings.
- **"Microphone blocked" or a flat level meter.** Open Windows Settings >
  Privacy & security > Microphone (on Windows 10: Privacy > Microphone) and
  allow desktop apps to use it, or pick another microphone in Settings.
- **Your antivirus warns about it.** The build is new and unsigned, and a
  hotkey-and-typing app looks like a keylogger to some scanners. See
  [docs/smartscreen.md](docs/smartscreen.md#if-your-antivirus-complains).
- **It is slow.** Pick `base.en` or `tiny.en` in Settings; they are much
  faster on older laptops.
- **Something else.** **Open log folder** in the tray menu shows `app.log`;
  please attach it to an
  [issue](https://github.com/rmcendarfer2017/Transcription-flow/issues)
  (it never contains what you said).

## Uninstall

Windows Settings > Apps > Installed apps > **Transcription Flow** >
Uninstall (on Windows 10: Settings > Apps > Apps & features). It removes the app, its Start menu entry and its "Start with
Windows" entry, then asks whether to also delete your downloaded speech
models and settings (`%LOCALAPPDATA%\TranscriptionFlow` and
`%APPDATA%\TranscriptionFlow`; the default is No, which keeps them for a
later reinstall).

For the portable zip, follow `Uninstall portable.txt` in its folder.

## For developers

### Run from source

Needs Python 3.12 or 3.13 (the python.org installer, which includes Tk).

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python dictation.py
```

With **Start with Windows** ticked in Settings, a source checkout starts
with `pythonw.exe`, without a console window; messages then go to the tray
and the log file.

`--device auto` uses an NVIDIA GPU, but only after a safe test run in a
separate process shows it works (GPU mode needs the cuBLAS 12 and cuDNN 9
DLLs, which pip does not install). The released app is CPU-only.


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
  app.py             wires hotkey, controller, tray, settings and setup window
  model_service.py   installed models, the start-up choice, download and load
  hallucination.py   drops Whisper's stock phrases on near-silent clips
  config.py          settings file (versioned, checked, atomic save)
  hotkey.py          hold-to-talk key (pynput hook that only queues events)
  output.py          typing, or paste-and-restore
  clipboard.py       the Windows clipboard, for paste mode
  autostart.py       Start with Windows (HKCU Run value)
  ui/overlay.py      the on-screen pill
  ui/tray.py         the tray icon (pystray)
  ui/settings.py     the settings window (Tk)
  ui/wizard.py       the first-run setup window (Tk)
  ui/display.py      DPI awareness and the screen's work area
dictation.py         runs the app from a source checkout
tests/               pytest suite (no microphone, hotkey or network needed)
packaging/
  transcription_flow.spec  PyInstaller (onedir, windowed, no UPX)
  installer.iss      Inno Setup, per-user
  buildinfo.py       version, icon, licences, onedir check, portable zip
  ci_smoke.py        the build, install and uninstall checks CI runs
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
elsewhere and run in the windows-latest CI job. CI runs
`tests\test_windows_ui.py` in a pytest process of its own (see the note
at its top); do the same if a Tk test fails with "Can't find a usable
init.tcl". The two https-redirect
tests skip when `openssl` is not on PATH (it makes their test certificate).

The test clip was made with the Kokoro text-to-speech model (Apache-2.0).

### Building the Windows app

The `windows-build` CI job builds it on GitHub's `windows-latest`; to build
it yourself on Windows (Inno Setup 6 installed):

```
venv\Scripts\pip install --require-hashes --no-deps -r requirements-build-lock.txt
venv\Scripts\python -m PyInstaller --noconfirm --clean packaging\transcription_flow.spec
for /f %v in ('venv\Scripts\python packaging\buildinfo.py version') do set TF_VERSION=%v
for /f %v in ('venv\Scripts\python packaging\buildinfo.py numeric-version') do set TF_NUM=%v
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" /DAppVersion=%TF_VERSION% /DAppNumVersion=%TF_NUM% /DSourceDir=%CD%\dist\TranscriptionFlow /DOutputDir=%CD%\dist\out /DIconFile=%CD%\build\transcription_flow\TranscriptionFlow.ico packaging\installer.iss
```

(These are `cmd.exe` lines; in a `.bat` file write `%%v` for `%v`. CI's
PowerShell version is in
[`windows-build.yml`](.github/workflows/windows-build.yml).)

`dist\TranscriptionFlow\` is the app folder (the portable zip is that
folder plus `Uninstall portable.txt`); `dist\out\` holds the installer.
`packaging\ci_smoke.py` has the checks CI runs on both. The installer is
per-user (no admin prompt) and unsigned for now.

### Making a release

1. Set the date of the version's section in `CHANGELOG.md`
   (`## [0.1.0] - YYYY-MM-DD`) and merge that to `main`.
2. Wait until CI on `main` has passed for that commit. The release stops
   if CI is missing, still running or failed for the tagged commit; after
   CI finishes, re-run the failed release jobs from the Actions tab. If a
   later push to `main` cancelled CI on that commit, re-run that CI run
   first, or tag the newer commit instead.
3. Tag the merged commit on `main` and push the tag:
   `git tag v0.1.0 && git push origin v0.1.0`.
4. [`release.yml`](.github/workflows/release.yml) checks that the tag is
   exactly the version `src/transcription_flow/__init__.py` is working
   towards (a `0.1.0.dev0` source takes only `v0.1.0`), that the changelog
   section is dated and that CI passed, runs the same Windows build and
   checks as CI, and creates a **draft** release with the installer, the
   portable zip, `SHA256SUMS.txt` and the changelog section as notes.
5. Check the draft (download, checksum, test install), then publish it by
   hand. The workflow never publishes. After a release, set `__version__`
   to the next `.dev0` version.

A pre-release (`v0.1.0rc1`, `a1`, `b1`) is drafted as a GitHub
pre-release, so it never becomes the "latest" release that the Download
link serves. It needs its own version and changelog section: set
`__version__ = "0.1.0rc1.dev0"` and add a dated `## [0.1.0rc1] - YYYY-MM-DD`
section (the `0.1.0` section does not count) before tagging, then go back
to `0.1.0.dev0` afterwards. The tag has no hyphen: `v0.1.0rc1`, not
`v0.1.0-rc1`. The final `v0.1.0` then needs `__version__ = "0.1.0.dev0"`
(or `"0.1.0"`) and its own dated `## [0.1.0] - YYYY-MM-DD` section; keep
the `0.1.0rc1` section below it. Links in a changelog section must be
absolute (`https://github.com/...`): the section becomes the release
notes, where relative links break.

## Third-party licences

The app folder and the installer include these open-source parts; each
one's licence text is in the `licenses` folder next to
`TranscriptionFlow.exe`, listed in `THIRD-PARTY-NOTICES.txt`.

- **pynput** (hotkey and typing) and **pystray** (tray icon): LGPL-3.0.
  They are used unmodified and ship as loose `.py` files in
  `_internal\pynput` and `_internal\pystray`, not inside the compiled
  archive, so you can replace them with modified versions. The LGPL-3.0
  text and the GPL-3.0 text it builds on are in `licenses`.
- **faster-whisper**, **CTranslate2**, **ONNX Runtime** (voice detection),
  **PortAudio** (via sounddevice), **six**: MIT. **Pillow**: MIT-CMU.
  **NumPy**, **tokenizers**, **huggingface_hub** and the smaller helpers:
  BSD, MIT or Apache-2.0, as listed.
- **Python**: the PSF licence. **Tcl/Tk** (the windows) has its own
  BSD-style licence; its text, and those of OpenSSL, libffi and bzip2,
  follow the PSF text in `licenses\python\LICENSE.txt`.
  **PyInstaller**'s bootloader: GPL-2.0 with its exception for bundled apps.
- **PyAV is not bundled.** faster-whisper imports it only to decode audio
  files, and the app hands it audio as numbers; PyAV's Windows build
  includes GPL codecs (x264/x265), so the app folder leaves it out.
- The **Whisper models** (MIT) are downloaded on first use, not bundled.
