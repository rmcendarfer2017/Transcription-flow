# PyInstaller spec for TranscriptionFlow.exe: onedir, windowed, no UPX.
#
#   python -m PyInstaller --noconfirm --clean packaging/transcription_flow.spec
#
# Output: dist/TranscriptionFlow/ (TranscriptionFlow.exe, _internal/,
# LICENSE.txt, THIRD-PARTY-NOTICES.txt, licenses/). The Inno Setup script
# (installer.iss) and the portable zip both wrap that folder.
# Onedir, not onefile: no self-extraction to %TEMP% (antivirus heuristics
# dislike it) and the LGPL modules stay replaceable on disk.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
# The package is built from src/ (not pip-installed): the hook helpers below
# import it in a child process that inherits this sys.path.
sys.path[:0] = [SPECPATH, str(ROOT / "src")]
import buildinfo  # noqa: E402

WORK = Path(workpath)
VERSION = buildinfo.app_version(ROOT)

icon = buildinfo.write_icon(WORK / "TranscriptionFlow.ico")
version_file = WORK / "version_info.txt"
version_file.write_text(buildinfo.version_info_text(VERSION), "utf-8")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    # ctranslate2 loads ctranslate2.dll and libiomp5md.dll from its own
    # folder (os.add_dll_directory); there is no contrib hook for it.
    binaries=collect_dynamic_libs("ctranslate2"),
    # faster_whisper/assets holds the Silero VAD model; our package data is
    # models_manifest.json.
    datas=collect_data_files("faster_whisper") + collect_data_files("transcription_flow"),
    # main.py imports the UI and the rest inside functions.
    hiddenimports=collect_submodules("transcription_flow"),
    runtime_hooks=[str(ROOT / "packaging" / "rthook_no_av.py")],
    excludes=buildinfo.EXCLUDES,
    module_collection_mode=buildinfo.LOOSE_MODULES,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=buildinfo.EXE_NAME,
    console=False,
    disable_windowed_traceback=False,
    upx=False,
    icon=str(icon),
    version=str(version_file),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=buildinfo.EXE_NAME)

buildinfo.add_licences(Path(DISTPATH) / buildinfo.EXE_NAME,
                       buildinfo.bundled_distributions(buildinfo.collected_top_levels(a)))
