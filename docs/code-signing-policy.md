# Code signing policy

> **Status: not signed yet, and not all of this policy is in force yet.**
> The first release, 0.1.0, will ship unsigned. This
> policy is written for an application to the
> [SignPath Foundation](https://signpath.org/) free code signing programme
> for open-source projects. Until that application is accepted, the
> installer shows the SmartScreen warning described in
> [smartscreen.md](smartscreen.md). Lines marked **TODO (maintainer)** are
> rules this project will follow but does not enforce yet; each must be done
> (and checked against SignPath's current terms) before applying.

<!-- TODO (maintainer): once SignPath accepts the project, replace the status
     note above with the line they require, for example:
     "Free code signing provided by SignPath.io, certificate by SignPath Foundation."
     and link this page from the README and the release notes. -->

## What gets signed

- Only files built by this repository's release workflow,
  [`.github/workflows/release.yml`](../.github/workflows/release.yml), from a
  `v*` tag on a commit of the `main` branch whose CI passed:
  - `TranscriptionFlow-Setup-<version>.exe` (the installer),
  - `TranscriptionFlow.exe` inside the installer and the portable zip.
- Builds run on GitHub-hosted Windows runners only. The repository never
  uses self-hosted runners, and no build made on a personal machine is ever
  signed.
- The build installs its Python packages (PyInstaller included) from
  `requirements-build-lock.txt`, pinned by hash. Python itself and Inno
  Setup come from the GitHub runner image and are not pinned (the build
  checks that Inno Setup is 6.3 or newer). Every GitHub Action is pinned
  to a commit SHA.
- The release workflow checks that the tagged commit is on `main`, that
  CI passed on it, and that the tag still points at the built commit when
  the draft is made. These checks run from the tagged commit's own
  workflow file, so they guard against mistakes, not against someone who
  can push to the repository.
- **TODO (maintainer):** add a tag ruleset for `v*` so that only the
  maintainer can create, move or delete release tags.
- Third-party files in the package (Python, the libraries listed in
  `THIRD-PARTY-NOTICES.txt`) are shipped as their projects publish them.
  This project signs only its own executables, never upstream binaries.

<!-- TODO (maintainer): confirm with SignPath which files of the PyInstaller
     folder they expect to be signed (the app exe and the installer only, or
     also the bootloader-built exe in the portable zip) and update the list. -->

## Team roles

The project currently has one maintainer, who holds every role.

| Role | Who | What they do |
|---|---|---|
| Committers and reviewers | [@rmcendarfer2017](https://github.com/rmcendarfer2017) | Write and review changes, which will reach `main` through pull requests with passing CI (see the TODO under "Security of the accounts") |
| Approvers | [@rmcendarfer2017](https://github.com/rmcendarfer2017) | Approve each signing request by hand, after checking that it comes from the release workflow for the expected tag |

<!-- TODO (maintainer): add any new committer, reviewer or approver here
     before they get those rights. -->

## How a release is signed

1. A maintainer updates `CHANGELOG.md` with the version and date and merges
   it to `main` through a pull request, and waits for CI on `main` to pass.
2. The maintainer pushes the tag `v<version>`.
3. `release.yml` checks the tag and that CI passed on the tagged commit,
   builds and tests the app on a GitHub-hosted
   Windows runner, and creates a **draft** GitHub release with the files and
   `SHA256SUMS.txt`.
4. *(After signing is set up.)* The workflow sends the unsigned files to
   SignPath; an approver approves the request; the signed files replace the
   unsigned ones in the draft.
5. A maintainer checks the draft (download, checksum, a test install) and
   publishes it by hand. Nothing is published automatically.

<!-- TODO (maintainer): step 4 needs SignPath's GitHub integration in
     release.yml (their action, an API token as a repository secret, and the
     artifact configuration). That is a separate change after acceptance. -->

## Security of the accounts

- **TODO (maintainer):** confirm that multi-factor authentication is on
  for every team member's GitHub account, and turn it on for SignPath once
  the account exists. Until then this is a plan, not a fact.
- **TODO (maintainer):** turn on a ruleset for `main` that requires a pull
  request and passing CI. Today `main` is not protected, so changes are
  expected to go through pull requests but nothing enforces it; the release
  workflow's CI check is the safeguard meanwhile.
- The release workflow's write permission is limited to the one job that
  creates the draft release; the build itself runs with read-only access.

## Privacy

This program does not send any information to other networked systems
unless the user asks for it. The one network use is the speech model
download, which the user starts with the **Download** button in the setup
window or by choosing a model in Settings. It fetches fixed files from
Hugging Face and checks their SHA-256 checksums. There is no telemetry.
Details are in [PRIVACY.md](../PRIVACY.md).

## Changes to the system

The installer installs per user (no administrator rights) to
`%LOCALAPPDATA%\Programs\Transcription Flow`, adds a Start menu entry and,
if the user leaves "Start with Windows" ticked, a value under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. The uninstaller
removes all of these, and asks before deleting the user's settings and
downloaded models. Nothing else on the system is changed.
