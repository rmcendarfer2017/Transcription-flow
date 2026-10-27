# "Windows protected your PC": installing an unsigned app

Transcription Flow's installer is not code-signed yet (see
[code-signing-policy.md](code-signing-policy.md) for the plan). Windows
SmartScreen warns about any new download that has no signature and no
download history, so the first time you run
`TranscriptionFlow-Setup-<version>.exe` you will see a blue warning. This
page shows what to expect and how to check the file first.

## 1. Check the download (optional, recommended)

Each release lists `SHA256SUMS.txt` next to the installer. To check the
installer against it:

1. Open the folder you downloaded to (usually **Downloads**) in File
   Explorer.
2. Open PowerShell there: on Windows 11, right-click an empty part of the
   folder and choose **Open in Terminal**; on Windows 10, hold **Shift**,
   right-click an empty part of the folder and choose **Open PowerShell
   window here**. (Or open PowerShell from the Start menu and type
   `cd $HOME\Downloads`.)
3. Type this, with your file's version number, and press Enter:

```
Get-FileHash .\TranscriptionFlow-Setup-0.1.0.exe
```

The `Hash` it prints must match the line for that file in
`SHA256SUMS.txt` (letter case doesn't matter). If it doesn't, delete the
file and download it again from the
[Releases page](https://github.com/rmcendarfer2017/Transcription-flow/releases).

## 2. The SmartScreen warning

Double-click the installer. Windows shows **"Windows protected your PC"**
with only a **Don't run** button.

<!-- SCREENSHOT (Ryan): docs/images/smartscreen-1-protected.png
     The first blue "Windows protected your PC" box, before "More info". -->
_Screenshot to come: the "Windows protected your PC" box._

Click **More info**. The box now shows the file name, "Publisher: Unknown
publisher" and a **Run anyway** button.

<!-- SCREENSHOT (Ryan): docs/images/smartscreen-2-run-anyway.png
     The same box after "More info", showing "Unknown publisher" and "Run anyway". -->
_Screenshot to come: the box after "More info", with **Run anyway**._

Click **Run anyway**. The installer starts. It installs for your user only,
so Windows does not ask for an administrator password.

<!-- SCREENSHOT (Ryan): docs/images/installer-1-tasks.png
     The installer page with "Start Transcription Flow when I sign in to Windows". -->
_Screenshot to come: the installer's "Start when I sign in" page._

## Why the warning appears

- SmartScreen trusts files by **reputation**: a signature from a known
  publisher, or many earlier downloads of the same file. A new, unsigned
  file has neither.
- Reputation belongs to each exact file, so every new version starts again
  from zero until it is signed.
- "Unknown publisher" means there is no signature, not that something is
  wrong with the file. It also means Windows can't vouch for it, which is
  why the checksum check above and the points below exist.

## How to trust it without a signature

- The source is public: <https://github.com/rmcendarfer2017/Transcription-flow>.
- Every release is built by GitHub Actions on a GitHub-hosted Windows
  machine from a tagged commit, by
  [`.github/workflows/release.yml`](../.github/workflows/release.yml). The
  build log is public in the repository's **Actions** tab, and it prints
  the SHA-256 of both files.
- The build runs the same checks as every pull request: it starts the built
  app, installs and uninstalls it, and scans it with Microsoft Defender when
  the build machine has it.

## If your antivirus complains

A dictation app has to watch for a hotkey and type keys into other apps,
which is also what some malware does, so a heuristic scanner may flag a new
unsigned build. If that happens:

1. Check the SHA-256 as in step 1.
2. Please [open an issue](https://github.com/rmcendarfer2017/Transcription-flow/issues)
   with the antivirus name and the detection name.
3. The project submits false positives to the vendor (for Microsoft
   Defender, through the Microsoft Security Intelligence submission site).

Don't turn off your antivirus to install it.
