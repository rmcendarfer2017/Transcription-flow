"""Build helpers for the Windows package, shared by the PyInstaller spec, the
CI workflow and the static tests (tests/test_packaging.py).

    python packaging/buildinfo.py version            -> 0.1.0.dev0
    python packaging/buildinfo.py numeric-version    -> 0.1.0.0 (Windows version fields)
    python packaging/buildinfo.py tag-version v0.1.0 -> 0.1.0 (exit 1 on a bad tag)
    python packaging/buildinfo.py stamp-version v0.1.0  (release builds: __version__ = "0.1.0")
    python packaging/buildinfo.py release-notes 0.1.0   -> that version's CHANGELOG.md section
    python packaging/buildinfo.py check-onedir DIR   -> exit 1 with the problems found
    python packaging/buildinfo.py portable-zip DIR OUT.zip

Pure standard library at import time; Pillow is needed only for write_icon.
"""

import argparse
import importlib.metadata as metadata
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"

APP_NAME = "Transcription Flow"
EXE_NAME = "TranscriptionFlow"
PUBLISHER = "Transcription Flow contributors"
REPO_URL = "https://github.com/rmcendarfer2017/Transcription-flow"
DESCRIPTION = "Local push-to-talk dictation, inspired by Wispr Flow"

# LGPL-3.0 modules ship as loose .py files, not inside the PYZ archive, so a
# user can swap them for modified versions (review-m2 F8).
LOOSE_MODULES = {"pynput": "py", "pystray": "py"}

# pynput and pystray import their Windows backends by name at run time,
# which PyInstaller's analysis can't see: the spec adds them as hidden
# imports and check_onedir looks for them. bundlecheck.BACKENDS (the
# frozen-app check) must name the same modules; a test compares them.
WIN32_BACKENDS = ("pynput.keyboard._win32", "pystray._win32")

# Not bundled. PyAV's Windows wheel carries an FFmpeg built with x264/x265
# (GPL); we never decode files (audio reaches faster-whisper as numpy
# arrays), so rthook_no_av.py stands in for it. The rest are build tools.
# hf_xet: huggingface_hub's Xet download backend; we download models with
# our own downloader and faster-whisper only imports the hub for its helpers.
EXCLUDES = ["av", "hf_xet", "pytest", "_pytest", "pip", "setuptools", "pkg_resources"]

# Wheels that ship no licence file of their own. VENDORED_LICENCES: the
# licence text copied from the project's repo at the pinned tag
# (ctranslate2: LICENSE at v4.8.4), shipped as licenses/<name>/LICENSE.txt.
# LICENCE_NOTES: a note, for wheels whose text is already in the folder.
VENDORED_LICENCES = {"ctranslate2": PACKAGING / "licenses" / "ctranslate2-LICENSE.txt"}
LICENCE_NOTES = {
    "tokenizers": "tokenizers (Hugging Face) is licensed under the Apache License 2.0;\n"
                  "the text is in LICENSE.txt at the top of this folder.\n",
    "flatbuffers": "FlatBuffers (Google) is licensed under the Apache License 2.0;\n"
                   "the text is in LICENSE.txt at the top of this folder.\n",
}

_LICENCE_NAME = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|ThirdPartyNotices|AUTHORS)", re.I)
_SKIP_SUFFIXES = {".py", ".pyc", ".pyi"}


def app_version(root: Path = ROOT) -> str:
    """__version__ from the package source (not imported: that would apply
    the runtime environment to the build process)."""
    text = (root / "src" / "transcription_flow" / "__init__.py").read_text("utf-8")
    return re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)


# Release tags: v1.2.3, or a pre-release such as v1.2.3rc1 (PEP 440 spelling,
# so the tag is the version the app reports).
_TAG = re.compile(r"v(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)")
_VERSION_LINE = re.compile(r'^__version__ = "[^"]+"$', re.M)


def tag_version(tag: str) -> str:
    """'v0.1.0' -> '0.1.0'. Anything else stops the release."""
    match = _TAG.fullmatch(tag)
    if not match:
        raise SystemExit(f"{tag!r} is not a release tag (v1.2.3 or v1.2.3rc1)")
    return match.group(1)


def _release(version: str) -> str:
    """'0.1.0.dev0' -> '0.1.0'; '0.1.0rc1' -> '0.1.0'."""
    return re.match(r"\d+\.\d+\.\d+", version).group(0)


def stamp_version(tag: str, root: Path = ROOT) -> str:
    """Set __version__ to the tag's version in a release build's checkout, so
    the exe, the installer and the file names say 0.1.0, not 0.1.0.dev0.
    The tag must be a release of the version the source is working towards:
    tagging v0.2.0 on a 0.1.0.dev0 tree stops (bump __init__.py first)."""
    version = tag_version(tag)
    init = root / "src" / "transcription_flow" / "__init__.py"
    source = app_version(root)
    if _release(source) != _release(version):
        raise SystemExit(f"tag {tag} does not match the source version {source}; "
                         f"set __version__ in {init.relative_to(root)} first")
    text = init.read_text("utf-8")
    init.write_text(_VERSION_LINE.sub(f'__version__ = "{version}"', text, count=1), "utf-8")
    return version


def release_notes(version: str, changelog: Path = ROOT / "CHANGELOG.md") -> str:
    """The body of the '## [version] - YYYY-MM-DD' section of the changelog.
    A missing, undated ('Unreleased') or empty section stops the release."""
    text = changelog.read_text("utf-8")
    heading = re.search(rf"^## \[{re.escape(version)}\](.*)$", text, re.M)
    if not heading:
        raise SystemExit(f"CHANGELOG.md has no '## [{version}]' section")
    if not re.fullmatch(r" - \d{4}-\d{2}-\d{2}", heading.group(1).rstrip()):
        raise SystemExit(f"CHANGELOG.md: give '## [{version}]' its release date "
                         f"('## [{version}] - YYYY-MM-DD') before tagging")
    rest = text[heading.end():]
    end = re.search(r"^## |^\[[^\]]+\]: ", rest, re.M)
    body = rest[:end.start() if end else None].strip()
    if not body:
        raise SystemExit(f"CHANGELOG.md: the '## [{version}]' section is empty")
    return body + "\n"


def numeric_version(version: str) -> str:
    """'0.1.0.dev0' -> '0.1.0.0': Windows version fields are four numbers."""
    release = re.match(r"\d+(?:\.\d+)*", version).group(0).split(".")[:3]
    return ".".join((release + ["0"] * 4)[:4])


def version_info_text(version: str) -> str:
    """The VSVersionInfo that PyInstaller's EXE(version=...) reads."""
    nums = tuple(int(n) for n in numeric_version(version).split("."))
    strings = [("CompanyName", PUBLISHER), ("FileDescription", APP_NAME),
               ("FileVersion", version), ("InternalName", EXE_NAME),
               ("LegalCopyright", "Apache-2.0; see LICENSE.txt"),
               ("OriginalFilename", f"{EXE_NAME}.exe"), ("ProductName", APP_NAME),
               ("ProductVersion", version), ("Comments", DESCRIPTION)]
    table = ",\n          ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
          {table}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_icon(path: Path) -> Path:
    """The app icon, drawn by the same code as the tray icon (one source)."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from transcription_flow.ui.tray import make_icon
    finally:
        sys.path.pop(0)
    path.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)]
    make_icon(size=256).save(path, format="ICO", sizes=sizes)
    return path


# -- licences ----------------------------------------------------------------


def _licence_files(dist):
    """Licence-like files a distribution installed: its dist-info licences
    and package files such as onnxruntime/ThirdPartyNotices.txt."""
    found = []
    for rel in dist.files or ():
        parts = rel.parts
        if Path(rel.name).suffix.lower() in _SKIP_SUFFIXES:
            continue
        in_licenses = len(parts) > 2 and parts[0].endswith(".dist-info") and parts[1] == "licenses"
        if in_licenses or _LICENCE_NAME.match(rel.name):
            found.append(rel)
    return found


def _licence_dest(rel) -> Path:
    """x-1.dist-info/licenses/LICENSE -> LICENSE; x/NOTICE stays x/NOTICE."""
    parts = rel.parts
    if parts[0].endswith(".dist-info"):
        parts = parts[2:] if parts[1] == "licenses" else parts[1:]
    return Path(*parts)


def _dist_licence(dist) -> str:
    meta = dist.metadata
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    first = (meta.get("License") or "").strip().splitlines()[:1]
    return first[0] if first and len(first[0]) < 80 else "see its licence files"


def bundled_distributions(top_level_names) -> dict:
    """{dist name: Distribution} for the top-level modules PyInstaller
    collected, plus PyInstaller itself (its bootloader is the exe)."""
    owners = metadata.packages_distributions()
    names = {"pyinstaller"}
    for top in top_level_names:
        names.update(owners.get(top, ()))
    found = {}
    for name in names:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        found[dist.metadata["Name"].lower().replace("_", "-")] = dist
    return dict(sorted(found.items()))


def collected_top_levels(analysis) -> set:
    """Top-level module and folder names in a PyInstaller Analysis."""
    tops = {entry[0].split(".")[0] for entry in analysis.pure}
    for dest, *_ in [*analysis.binaries, *analysis.datas]:
        first = Path(dest).parts[0]
        tops.add(first.split(".")[0])  # numpy.libs -> numpy, _cffi_backend.cp312... -> _cffi_backend
    return tops


# A python.org Windows build's LICENSE.txt is the PSF licence followed by the
# licences of the parts built into it (CPython PCbuild/regen.targets).
PYTHON_ROW = ("Python runtime: PSF-2.0. Its LICENSE.txt also holds the licences of the "
              "parts\nbundled with it: Tcl/Tk (the Tcl/Tk licence, BSD-style), OpenSSL "
              "(Apache-2.0),\nlibffi (MIT) and bzip2 (BSD-style)  (licenses/python/)")


def add_licences(onedir: Path, dists: dict, python_licence=None,
                 platform: str = sys.platform) -> list:
    """Write LICENSE.txt, THIRD-PARTY-NOTICES.txt and licenses/<dist>/ into
    the onedir. Raises if a bundled distribution has no licence text and no
    note, so a new dependency can't slip in without one. Returns the names."""
    out = onedir / "licenses"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copyfile(ROOT / "LICENSE", onedir / "LICENSE.txt")
    for name in ("GPL-3.0.txt", "LGPL-3.0.txt"):
        shutil.copyfile(PACKAGING / "licenses" / name, out / name)
    missing, rows = [], []
    for name, dist in dists.items():
        target = out / name
        files = _licence_files(dist)
        for rel in files:
            src = Path(dist.locate_file(rel))
            dest = target / _licence_dest(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        if not files:
            if name in VENDORED_LICENCES:
                target.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(VENDORED_LICENCES[name], target / "LICENSE.txt")
            elif name in LICENCE_NOTES:
                target.mkdir(parents=True, exist_ok=True)
                (target / "LICENSE-NOTE.txt").write_text(LICENCE_NOTES[name], "utf-8")
            else:
                missing.append(name)
                continue
        rows.append(f"{name} {dist.version}: {_dist_licence(dist)}  (licenses/{name}/)")
    if missing:
        raise SystemExit(f"no licence text for bundled: {', '.join(missing)}; "
                         "add a LICENCE_NOTES entry in packaging/buildinfo.py")
    python_licence = python_licence or Path(sys.base_prefix) / "LICENSE.txt"
    if python_licence.is_file():
        (out / "python").mkdir()
        shutil.copyfile(python_licence, out / "python" / "LICENSE.txt")
        rows.append(PYTHON_ROW)
    elif platform == "win32":  # the only source of the Tcl/Tk and OpenSSL texts (review-m4 F5)
        raise SystemExit(f"no Python licence at {python_licence}; the bundle needs it")
    notices = PACKAGING / "THIRD-PARTY-NOTICES.txt"
    (onedir / "THIRD-PARTY-NOTICES.txt").write_text(
        notices.read_text("utf-8") + "\n" + "\n".join(rows) + "\n", "utf-8")
    return list(dists)


# -- checks on a built onedir (CI) -------------------------------------------


def check_onedir(onedir: Path) -> list:
    """Problems with a built onedir; empty when it is fine."""
    internal = onedir / "_internal"
    problems = []
    for module in LOOSE_MODULES:
        if not (internal / module / "__init__.py").is_file():
            problems.append(f"{module} is not loose in _internal/{module}/ (LGPL)")
    for module in WIN32_BACKENDS:
        rel = module.replace(".", "/") + ".py"  # loose files (LOOSE_MODULES)
        if not (internal / rel).is_file():
            problems.append(f"missing _internal/{rel} (a run-time backend)")
    if (internal / "av").exists() or list(internal.glob("av.libs")):
        problems.append("PyAV/FFmpeg was bundled")
    for rel in ("LICENSE.txt", "THIRD-PARTY-NOTICES.txt", "licenses/LGPL-3.0.txt",
                "licenses/GPL-3.0.txt", "licenses/pynput", "licenses/pystray",
                "licenses/python/LICENSE.txt"):
        if not (onedir / rel).exists():
            problems.append(f"missing {rel}")
    if not list(internal.glob("faster_whisper/assets/silero_vad*.onnx")):
        problems.append("faster_whisper/assets (VAD model) missing")
    if not [*internal.glob("ctranslate2/ctranslate2.dll"),
            *internal.glob("ctranslate2.libs/libctranslate2*.so*")]:
        problems.append("the ctranslate2 library is missing")
    if not any(p.is_file() for p in (onedir / f"{EXE_NAME}.exe", onedir / EXE_NAME)):
        problems.append(f"{EXE_NAME}.exe is missing")
    return problems


def portable_zip(onedir: Path, out: Path) -> Path:
    """The onedir as a zip with one top folder, plus the uninstall note."""
    out.parent.mkdir(parents=True, exist_ok=True)
    top = EXE_NAME
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(onedir.rglob("*")):
            if path.is_file():
                z.write(path, f"{top}/{path.relative_to(onedir).as_posix()}")
        z.write(PACKAGING / "Uninstall portable.txt", f"{top}/Uninstall portable.txt")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version")
    sub.add_parser("numeric-version")
    for name in ("tag-version", "stamp-version"):
        sub.add_parser(name).add_argument("tag")
    sub.add_parser("release-notes").add_argument("version")
    c = sub.add_parser("check-onedir")
    c.add_argument("onedir", type=Path)
    z = sub.add_parser("portable-zip")
    z.add_argument("onedir", type=Path)
    z.add_argument("out", type=Path)
    args = p.parse_args(argv)
    if args.cmd == "version":
        print(app_version())
    elif args.cmd == "numeric-version":
        print(numeric_version(app_version()))
    elif args.cmd == "tag-version":
        print(tag_version(args.tag))
    elif args.cmd == "stamp-version":
        print(stamp_version(args.tag))
    elif args.cmd == "release-notes":
        sys.stdout.write(release_notes(args.version))
    elif args.cmd == "check-onedir":
        problems = check_onedir(args.onedir)
        for problem in problems:
            print(f"FAIL: {problem}")
        if problems:
            return 1
        print("onedir OK")
    else:
        print(portable_zip(args.onedir, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
