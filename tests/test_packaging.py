"""Static checks of the Windows packaging (M4): the PyInstaller spec, the Inno
Setup script, the CI workflow and the build helpers. The real build runs only
in the windows-build CI job; these catch drift between the packaging and the
app (mutex name, Run value, data folders) on any OS."""

import ast
import functools
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import zipfile
from importlib import metadata
from pathlib import Path

import pytest
import yaml

from helpers import child_env
from transcription_flow import autostart, paths, single_instance

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
SPEC = PACKAGING / "transcription_flow.spec"
ISS = PACKAGING / "installer.iss"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"pkg_{name}", PACKAGING / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PACKAGING))  # ci_smoke imports buildinfo
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(PACKAGING))
    return module


buildinfo = _load("buildinfo")
ci_smoke = _load("ci_smoke")


# -- PyInstaller spec ----------------------------------------------------------


def spec_calls():
    """{function name: [ast.Call]} for the top-level calls in the spec."""
    calls = {}
    for node in ast.walk(ast.parse(SPEC.read_text("utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.setdefault(node.func.id, []).append(node)
    return calls


def kwargs(call):
    return {k.arg: k.value for k in call.keywords}


def test_spec_is_onedir_windowed_without_upx():
    calls = spec_calls()
    exe = kwargs(calls["EXE"][0])
    assert ast.literal_eval(exe["console"]) is False  # windowed
    assert ast.literal_eval(exe["exclude_binaries"]) is True  # onedir: binaries go to COLLECT
    assert ast.literal_eval(exe["upx"]) is False
    assert "icon" in exe and "version" in exe
    assert len(calls["COLLECT"]) == 1
    assert ast.literal_eval(kwargs(calls["COLLECT"][0])["upx"]) is False


def test_spec_collects_the_native_parts_and_keeps_lgpl_modules_loose():
    calls = spec_calls()
    analysis = kwargs(calls["Analysis"][0])
    assert ast.unparse(analysis["module_collection_mode"]) == "buildinfo.LOOSE_MODULES"
    assert buildinfo.LOOSE_MODULES == {"pynput": "py", "pystray": "py"}
    collected = {(c.func.id, ast.literal_eval(c.args[0])) for name in
                 ("collect_dynamic_libs", "collect_data_files") for c in calls.get(name, [])}
    assert ("collect_dynamic_libs", "ctranslate2") in collected
    assert ("collect_data_files", "faster_whisper") in collected  # the VAD model
    assert ("collect_data_files", "transcription_flow") in collected  # models_manifest.json
    assert ast.unparse(analysis["excludes"]) == "buildinfo.EXCLUDES"
    assert "av" in buildinfo.EXCLUDES
    source = SPEC.read_text("utf-8")
    assert "rthook_no_av.py" in source and (PACKAGING / "rthook_no_av.py").is_file()
    assert "launcher.py" in source and (PACKAGING / "launcher.py").is_file()
    assert "add_licences" in source
    assert ast.unparse(analysis["hiddenimports"]) == (
        "collect_submodules('transcription_flow') + list(buildinfo.WIN32_BACKENDS)")


def test_numeric_version():
    assert buildinfo.numeric_version("0.1.0.dev0") == "0.1.0.0"
    assert buildinfo.numeric_version("1.2") == "1.2.0.0"
    assert buildinfo.numeric_version("2.10.3rc1") == "2.10.3.0"
    assert buildinfo.numeric_version("1.2.3.4") == "1.2.3.0"


def test_app_version_is_the_package_version():
    import transcription_flow

    assert buildinfo.app_version() == transcription_flow.__version__


def test_version_info_text_is_a_valid_version_resource():
    """Evaluated with stand-ins for PyInstaller's classes (same signatures)."""
    seen = {}

    def record(kind):
        def make(*args, **kw):
            seen.setdefault(kind, []).append((args, kw))
            return (kind, args, kw)
        return make

    names = ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct",
             "VarFileInfo", "VarStruct")
    eval(buildinfo.version_info_text("0.1.0.dev0"), {n: record(n) for n in names})  # noqa: S307
    ffi = seen["FixedFileInfo"][0][1]
    assert ffi["filevers"] == ffi["prodvers"] == (0, 1, 0, 0)
    strings = dict(args for args, _ in seen["StringStruct"])
    assert strings["ProductName"] == "Transcription Flow"
    assert strings["OriginalFilename"] == "TranscriptionFlow.exe"
    assert strings["ProductVersion"] == "0.1.0.dev0"
    assert "Wispr" not in strings["FileDescription"]
    assert "clone" not in buildinfo.version_info_text("1").lower()


def test_version_info_text_loads_in_pyinstaller_when_available(tmp_path, monkeypatch):
    """PyInstaller's own parser and serialiser (when it is installed)."""
    compat = pytest.importorskip("PyInstaller.compat")
    if not hasattr(compat, "win32api"):  # off Windows; the parser doesn't need it
        monkeypatch.setattr(compat, "win32api", None, raising=False)
    versioninfo = pytest.importorskip("PyInstaller.utils.win32.versioninfo", exc_type=ImportError)
    path = tmp_path / "v.txt"
    path.write_text(buildinfo.version_info_text("0.1.0.dev0"), "utf-8")
    info = versioninfo.load_version_info_from_text_file(str(path))
    assert (info.ffi.fileVersionMS, info.ffi.fileVersionLS) == ((0 << 16) | 1, 0)
    assert "TranscriptionFlow.exe".encode("utf-16-le") in info.toRaw()


def test_the_icon_is_a_multi_size_ico(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    path = buildinfo.write_icon(tmp_path / "app.ico")
    with Image.open(path) as ico:
        assert ico.format == "ICO"
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.info["sizes"])


# -- the PyAV stand-in -----------------------------------------------------------

BLOCK_AV = textwrap.dedent("""
    import sys

    class BlockAV:  # the bundle has no av: make any real import of it fail
        def find_spec(self, name, path=None, target=None):
            if name == "av" or name.startswith("av."):
                raise ImportError("av is not bundled")

    sys.meta_path.insert(0, BlockAV())
    exec(open({hook!r}).read())
""")


def run_without_av(body: str) -> subprocess.CompletedProcess:
    code = BLOCK_AV.format(hook=str(PACKAGING / "rthook_no_av.py")) + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120, env=child_env())


def test_faster_whisper_imports_without_pyav_and_decode_audio_says_why():
    done = run_without_av("""
        import faster_whisper
        from faster_whisper.audio import decode_audio
        try:
            decode_audio("x.wav")
        except ImportError as err:
            print("REFUSED", err)
    """)
    assert done.returncode == 0, done.stderr
    assert "REFUSED PyAV is not bundled" in done.stdout


def test_the_stand_in_hides_only_av():
    done = run_without_av("""
        import av
        assert not hasattr(av, "__file__")  # dunder lookups are AttributeError, not ImportError
        import numpy, json  # others still import
        print("OK")
    """)
    assert done.returncode == 0 and done.stdout.strip() == "OK", done.stderr


@pytest.mark.slow
def test_selftest_transcribes_without_pyav():
    """What the frozen app does: the real model with no av module at all."""
    models_dir = os.environ.get("TFLOW_TEST_MODELS_DIR")
    if not models_dir:
        pytest.skip("set TFLOW_TEST_MODELS_DIR to a folder with tiny.en installed (see README)")
    wav = ROOT / "tests" / "fixtures" / "hello.wav"
    done = run_without_av(f"""
        from pathlib import Path
        from transcription_flow import selftest
        report = selftest.run(Path({str(wav)!r}), Path({models_dir!r}) / "tiny.en",
                              expect="hello")
        print(report["ok"])
    """)
    assert done.stdout.strip() == "True", done.stderr


# -- licences ---------------------------------------------------------------------


def test_the_gpl_and_lgpl_texts_are_the_v3_texts():
    gpl = (PACKAGING / "licenses" / "GPL-3.0.txt").read_text("utf-8")
    lgpl = (PACKAGING / "licenses" / "LGPL-3.0.txt").read_text("utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in gpl[:200] and "Version 3, 29 June 2007" in gpl[:200]
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in lgpl[:200]
    assert "Version 3, 29 June 2007" in lgpl[:200]


def test_licences_for_the_collected_distributions(tmp_path):
    dists = buildinfo.bundled_distributions({"numpy", "faster_whisper", "ctranslate2", "yaml"})
    assert {"numpy", "faster-whisper", "ctranslate2", "pyyaml"} <= set(dists)
    buildinfo.add_licences(tmp_path, dists, python_licence=tmp_path / "none", platform="linux")
    assert (tmp_path / "LICENSE.txt").read_text("utf-8") == (ROOT / "LICENSE").read_text("utf-8")
    assert (tmp_path / "licenses" / "LGPL-3.0.txt").is_file()
    assert (tmp_path / "licenses" / "GPL-3.0.txt").is_file()
    assert (tmp_path / "licenses" / "numpy" / "LICENSE.txt").is_file()
    assert (tmp_path / "licenses" / "faster-whisper" / "LICENSE").is_file()
    # Its wheel ships no licence file: the MIT text from its repo at the pinned tag.
    ct2 = (tmp_path / "licenses" / "ctranslate2" / "LICENSE.txt").read_text("utf-8")
    assert ct2.startswith("MIT License") and "The OpenNMT Authors" in ct2
    assert "Permission is hereby granted" in ct2
    notices = (tmp_path / "THIRD-PARTY-NOTICES.txt").read_text("utf-8")
    assert "pynput and pystray" in notices and "_internal/pynput/" in notices
    assert re.search(r"^numpy \S+: BSD-3-Clause", notices, re.M)


class FakeDist:
    def __init__(self, name, files=()):
        self.metadata = {"Name": name, "License-Expression": "MIT"}
        self.version = "1.0"
        # Like Distribution.files: PackagePath is POSIX on Windows too (PR #4 CI).
        self.files = [metadata.PackagePath(f) for f in files]

    def locate_file(self, rel):
        return Path(__file__)  # any readable file


def test_the_python_licence_is_copied_and_tcl_tk_is_not_called_psf(tmp_path):
    licence = tmp_path / "py-LICENSE.txt"
    licence.write_text("PSF ... Tcl/Tk ...", "utf-8")
    buildinfo.add_licences(tmp_path / "out", {}, python_licence=licence, platform="win32")
    assert (tmp_path / "out" / "licenses" / "python" / "LICENSE.txt").read_text("utf-8") == \
        "PSF ... Tcl/Tk ..."
    notices = (tmp_path / "out" / "THIRD-PARTY-NOTICES.txt").read_text("utf-8")
    assert "Python runtime: PSF-2.0." in notices
    assert "Tcl/Tk (the Tcl/Tk licence, BSD-style)" in notices
    assert not re.search(r"Tcl/Tk\W*\)?:\s*PSF", notices)  # review-m4 F5


def test_a_windows_build_without_the_python_licence_stops(tmp_path):
    """review-m4 F5: it is the only source of the Tcl/Tk and OpenSSL texts."""
    with pytest.raises(SystemExit, match="no Python licence"):
        buildinfo.add_licences(tmp_path, {}, python_licence=tmp_path / "none", platform="win32")


def test_a_bundled_distribution_without_a_licence_stops_the_build(tmp_path):
    with pytest.raises(SystemExit, match="no licence text for bundled: newdep"):
        buildinfo.add_licences(tmp_path, {"newdep": FakeDist("newdep", ["newdep/__init__.py"])})


def test_licence_files_are_found_in_dist_info_and_package_folders(tmp_path):
    dist = FakeDist("x", ["x-1.dist-info/licenses/LICENSE", "x-1.dist-info/licenses/sub/NOTICE",
                          "x/ThirdPartyNotices.txt", "x/licenses/__init__.py", "x/core.py",
                          "x-1.dist-info/METADATA", "x-1.dist-info/COPYING.LGPL"])
    found = {str(p) for p in buildinfo._licence_files(dist)}
    assert found == {"x-1.dist-info/licenses/LICENSE", "x-1.dist-info/licenses/sub/NOTICE",
                     "x/ThirdPartyNotices.txt", "x-1.dist-info/COPYING.LGPL"}
    buildinfo.add_licences(tmp_path, {"x": dist})
    assert (tmp_path / "licenses" / "x" / "sub" / "NOTICE").is_file()
    assert (tmp_path / "licenses" / "x" / "COPYING.LGPL").is_file()
    assert (tmp_path / "licenses" / "x" / "x" / "ThirdPartyNotices.txt").is_file()


def test_collected_top_levels_reads_modules_binaries_and_data():
    class A:
        pure = [("numpy.core", "", "PYMODULE"), ("six", "", "PYMODULE")]
        binaries = [("numpy.libs/libopenblas.dll", "", "BINARY"),
                    ("_cffi_backend.cp312-win_amd64.pyd", "", "EXTENSION")]
        datas = [("faster_whisper/assets/silero_vad_v6.onnx", "", "DATA")]

    assert buildinfo.collected_top_levels(A) == {"numpy", "six", "_cffi_backend",
                                                 "faster_whisper"}


# -- the onedir check and the portable zip --------------------------------------


def fake_onedir(root: Path) -> Path:
    onedir = root / "TranscriptionFlow"
    for rel in ("TranscriptionFlow.exe", "LICENSE.txt", "THIRD-PARTY-NOTICES.txt",
                "licenses/LGPL-3.0.txt", "licenses/GPL-3.0.txt", "licenses/pynput/COPYING.LGPL",
                "licenses/pystray/COPYING.LGPL", "_internal/pynput/__init__.py",
                "_internal/pystray/__init__.py", "_internal/ctranslate2/ctranslate2.dll",
                "_internal/pynput/keyboard/_win32.py", "_internal/pystray/_win32.py",
                "licenses/python/LICENSE.txt",
                "_internal/faster_whisper/assets/silero_vad_v6.onnx"):
        (onedir / rel).parent.mkdir(parents=True, exist_ok=True)
        (onedir / rel).write_text("x", "utf-8")
    return onedir


def test_check_onedir_passes_a_complete_folder(tmp_path):
    assert buildinfo.check_onedir(fake_onedir(tmp_path)) == []


@pytest.mark.parametrize("change, problem", [
    (lambda d: (d / "_internal/pynput/__init__.py").unlink(), "pynput is not loose"),
    (lambda d: (d / "_internal/pystray/__init__.py").unlink(), "pystray is not loose"),
    (lambda d: (d / "_internal/av").mkdir(), "PyAV/FFmpeg was bundled"),
    (lambda d: (d / "_internal/av.libs").mkdir(), "PyAV/FFmpeg was bundled"),
    (lambda d: (d / "licenses/LGPL-3.0.txt").unlink(), "missing licenses/LGPL-3.0.txt"),
    (lambda d: (d / "licenses/python/LICENSE.txt").unlink(),
     "missing licenses/python/LICENSE.txt"),
    (lambda d: (d / "_internal/pynput/keyboard/_win32.py").unlink(),
     "missing _internal/pynput/keyboard/_win32.py"),
    (lambda d: (d / "_internal/pystray/_win32.py").unlink(), "missing _internal/pystray/_win32.py"),
    (lambda d: (d / "_internal/faster_whisper/assets/silero_vad_v6.onnx").unlink(), "VAD"),
    (lambda d: (d / "_internal/ctranslate2/ctranslate2.dll").unlink(), "ctranslate2"),
    (lambda d: (d / "TranscriptionFlow.exe").unlink(), "TranscriptionFlow.exe is missing"),
])
def test_check_onedir_names_each_problem(tmp_path, change, problem):
    onedir = fake_onedir(tmp_path)
    change(onedir)
    problems = buildinfo.check_onedir(onedir)
    assert len(problems) == 1 and problem in problems[0]


def test_check_onedir_cli_exit_codes(tmp_path, capsys):
    onedir = fake_onedir(tmp_path)
    assert buildinfo.main(["check-onedir", str(onedir)]) == 0
    (onedir / "_internal/pynput/__init__.py").unlink()
    assert buildinfo.main(["check-onedir", str(onedir)]) == 1
    assert "FAIL: pynput" in capsys.readouterr().out


def test_portable_zip_has_one_top_folder_and_the_uninstall_note(tmp_path):
    out = buildinfo.portable_zip(fake_onedir(tmp_path), tmp_path / "out" / "p.zip")
    names = zipfile.ZipFile(out).namelist()
    assert all(n.startswith("TranscriptionFlow/") for n in names)
    assert "TranscriptionFlow/TranscriptionFlow.exe" in names
    assert "TranscriptionFlow/Uninstall portable.txt" in names
    note = (PACKAGING / "Uninstall portable.txt").read_text("utf-8")
    assert r"%LOCALAPPDATA%\TranscriptionFlow" in note and r"%APPDATA%\TranscriptionFlow" in note


# -- Inno Setup script ----------------------------------------------------------------


def iss_sections():
    """{section: [lines]} with ISPP #define macros expanded."""
    text = ISS.read_text("utf-8")
    defines = dict(re.findall(r'^#define (\w+) "([^"]*)"', text, re.M))
    for name, value in defines.items():
        text = text.replace("{#" + name + "}", value)
    sections, current = {}, None
    for line in text.splitlines():
        if re.fullmatch(r"\[\w+\]", line.strip()):
            current = line.strip()[1:-1]
            sections[current] = []
        elif current and line.strip() and not line.lstrip().startswith(";"):
            sections[current].append(line)
    return sections


def setup_values():
    return dict(line.split("=", 1) for line in iss_sections()["Setup"] if "=" in line
                and not line.startswith("#"))


def iss_entry(line):
    """'Name: "x"; Flags: a b' -> {'Name': 'x', 'Flags': 'a b'} (Inno doubles quotes)."""
    def unquote(value):
        value = value.strip()
        return value[1:-1].replace('""', '"') if value.startswith('"') else value

    return {k: unquote(v) for k, v in re.findall(r'(\w+): ((?:"(?:[^"]|"")*")|[^;]*)', line)}


def test_installer_is_per_user_with_the_apps_mutex():
    setup = setup_values()
    assert setup["PrivilegesRequired"] == "lowest"
    assert "PrivilegesRequiredOverridesAllowed" not in setup  # never an admin install
    assert setup["AppMutex"] == single_instance.MUTEX_NAME
    assert setup["DefaultDirName"].startswith("{autopf}\\")
    assert setup["AppId"].strip("{}") == ci_smoke.APP_ID.strip("{}")
    assert setup["OutputBaseFilename"] == "TranscriptionFlow-Setup-{#AppVersion}"
    assert setup["UninstallDisplayIcon"] == "{app}\\TranscriptionFlow.exe"


def test_installer_run_value_is_the_one_the_app_writes(tmp_path):
    (entry,) = [iss_entry(line) for line in iss_sections()["Registry"]]
    assert entry["Root"] == "HKCU" and entry["Subkey"] == autostart.RUN_KEY
    assert entry["ValueName"] == autostart.VALUE_NAME
    assert "uninsdeletevalue" in entry["Flags"] and entry["Tasks"] == "autostart"
    assert entry["Check"] == "ShouldWriteRunValue"  # review-m4 F4
    exe = "{app}\\TranscriptionFlow.exe"
    assert entry["ValueData"] == autostart.launch_command(exe, frozen=True)
    assert entry["ValueData"].endswith(" --tray")


def test_the_autostart_task_is_on_by_default():
    (task,) = [iss_entry(line) for line in iss_sections()["Tasks"]]
    assert task["Name"] == "autostart" and "unchecked" not in task.get("Flags", "")
    assert task["Check"] == "ShouldWriteRunValue"


def test_an_upgrade_writes_the_run_value_only_if_autostart_is_still_on():
    """review-m4 F4: turning autostart off in Settings must survive an
    upgrade. The decision reads the uninstall key and the Run value before
    anything is written (InitializeSetup)."""
    code = "\n".join(iss_sections()["Code"])
    init = re.search(r"function InitializeSetup\(\): Boolean;.*?\nend;", code, re.S).group(0)
    assert (f"IsUpgrade := RegKeyExists(HKCU, '{ci_smoke.UNINSTALL_KEY}');" in init)
    assert (f"HadRunValue := RegValueExists(HKCU, '{autostart.RUN_KEY}', "
            f"'{autostart.VALUE_NAME}');" in init)
    should = re.search(r"function ShouldWriteRunValue\(\): Boolean;.*?\nend;", code, re.S)
    assert "Result := (not IsUpgrade) or HadRunValue;" in should.group(0)


def test_installer_has_a_start_menu_entry_and_installs_the_whole_onedir():
    (icon,) = [iss_entry(line) for line in iss_sections()["Icons"]]
    assert icon["Name"].startswith("{autoprograms}\\") and icon["Filename"].endswith(
        "TranscriptionFlow.exe")
    (files,) = [iss_entry(line) for line in iss_sections()["Files"]]
    assert files["Source"].endswith("\\*") and "recursesubdirs" in files["Flags"]


def test_uninstall_removes_autostart_and_asks_about_the_apps_data_folders(monkeypatch):
    code = "\n".join(iss_sections()["Code"])
    assert f"'{autostart.RUN_KEY}', '{autostart.VALUE_NAME}'" in code
    assert f"'{autostart.APPROVED_KEY}', '{autostart.VALUE_NAME}'" in code
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("TFLOW_HOME")
    monkeypatch.setenv("LOCALAPPDATA", "L")
    monkeypatch.setenv("APPDATA", "R")
    assert paths.data_dir() == Path("L") / "TranscriptionFlow"
    assert paths.config_file().parent == Path("R") / "TranscriptionFlow"
    # Exactly these two calls, nothing wider (review-m4 F2); CI checks that
    # folders next to them and a junction's target survive.
    assert re.findall(r"\bDelTree\s*\((.*?)\);", code) == [
        "ExpandConstant('{localappdata}\\TranscriptionFlow'), True, True, True",
        "ExpandConstant('{userappdata}\\TranscriptionFlow'), True, True, True"]
    # Also count bare calls: a last statement before `end` has no `;`, and a
    # call can span lines (review-m4 N1).
    assert len(re.findall(r"\bDelTree\s*\(", code)) == 2
    assert not re.search(r"\b(DeleteFile|RemoveDir|DelayDeleteFile)\s*\(", code)
    assert [iss_entry(line) for line in iss_sections()["UninstallDelete"]] == [
        {"Type": "filesandordirs", "Name": "{app}\\_internal"}]
    assert "MB_YESNO or MB_DEFBUTTON2" in code  # No is the default (plan 3.9, QA B5)
    assert "MB_DEFBUTTON1" not in code
    assert "HasParam('/DELETEUSERDATA')" in code and "UninstallSilent" in code


# -- CI workflows ---------------------------------------------------------------------


@functools.cache
def workflows():
    """Parsed once per session; the tests only read it."""
    return {p.name: yaml.safe_load(p.read_text("utf-8")) for p in WORKFLOWS}


def windows_build_job():
    """The one Windows build, shared by ci.yml and release.yml."""
    return workflows()["windows-build.yml"]["jobs"]["build"]


# A job either runs on a GitHub-hosted runner or calls this repo's own
# reusable workflow (which is checked as a workflow of its own).
LOCAL_BUILD = "./.github/workflows/windows-build.yml"


def test_every_workflow_parses_and_runs_on_github_hosted_runners_only():
    assert WORKFLOWS
    for name, flow in workflows().items():
        assert flow["permissions"] == {"contents": "read"}, name
        for job_name, job in flow["jobs"].items():
            if "uses" in job:
                assert job["uses"] == LOCAL_BUILD and "runs-on" not in job, (name, job_name)
            else:
                assert job["runs-on"] in ("ubuntu-latest", "windows-latest"), (name, job_name)
            assert "self-hosted" not in str(job)


def test_both_ci_and_release_use_the_one_windows_build():
    flows = workflows()
    on = flows["windows-build.yml"][True]  # yaml reads the key `on` as True
    (trigger,) = on
    inputs = on["workflow_call"]["inputs"]
    assert trigger == "workflow_call" and list(inputs) == ["release-tag"]
    assert inputs["release-tag"]["type"] == "string" and inputs["release-tag"]["default"] == ""
    assert flows["ci.yml"]["jobs"]["windows-build"]["uses"] == LOCAL_BUILD
    assert "with" not in flows["ci.yml"]["jobs"]["windows-build"]  # CI builds the .dev0 source
    assert flows["release.yml"]["jobs"]["build"]["uses"] == LOCAL_BUILD


def test_every_action_is_pinned_by_commit_sha():
    for name, flow in workflows().items():
        for job in flow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step


def test_the_windows_build_job_builds_installs_uninstalls_and_uploads():
    job = windows_build_job()
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    for needed in ("requirements-build-lock.txt", "PyInstaller --noconfirm --clean "
                   "packaging/transcription_flow.spec", "ci_smoke.py frozen dist/TranscriptionFlow",
                   "ISCC", "packaging\\installer.iss", "ci_smoke.py install",
                   "ci_smoke.py uninstall\n", "ci_smoke.py uninstall --delete-user-data",
                   "ci_smoke.py upgrade",
                   "portable-zip", "ci_smoke.py sizes", "--verify --model tiny.en"):
        assert needed in runs + "\n", needed
    upload = [s for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert len(upload) == 1
    assert "Setup-*.exe" in upload[0]["with"]["path"]
    assert "-portable.zip" in upload[0]["with"]["path"]
    # It builds and uploads; creating a release is release.yml's draft job alone.
    assert "gh release" not in str(job) and "permissions" not in job


def test_the_tk_window_tests_run_in_their_own_process_on_windows():
    steps = workflows()["ci.yml"]["jobs"]["windows-unit"]["steps"]
    runs = [s.get("run", "") for s in steps if "pytest" in s.get("run", "")]
    assert any("--ignore=tests/test_windows_ui.py" in r for r in runs)
    assert any(r.endswith("pytest -ra tests/test_windows_ui.py") for r in runs)


# -- CI smoke helpers -------------------------------------------------------------------


def test_last_json_reads_the_report_line():
    assert ci_smoke.last_json('noise\n{"ok": true}\n\n') == {"ok": True}
    assert ci_smoke.last_json("") is None
    assert ci_smoke.last_json("not json") is None


def test_checks_count_failures(capsys):
    check = ci_smoke.Checks()
    assert check(True, "a") is True and check(False, "b", "why") is False
    out = capsys.readouterr().out
    assert "PASS: a" in out and "FAIL: b (why)" in out and check.failed == 1


def test_sizes_table_marks_what_was_not_built(tmp_path):
    (tmp_path / "s.exe").write_bytes(b"x" * (3 << 20))
    table = ci_smoke.sizes_table([tmp_path / "s.exe", tmp_path / "missing.zip"])
    assert "| s.exe | 3.0 MB |" in table and "| missing.zip | not built |" in table


def test_sizes_go_to_the_job_summary(tmp_path, monkeypatch):
    (tmp_path / "a.zip").write_bytes(b"x")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert ci_smoke.main(["sizes", str(tmp_path / "a.zip")]) == 0
    assert "Windows build sizes" in summary.read_text("utf-8")


def test_the_upload_runs_only_for_pushes_and_same_repo_prs():
    """review-m4 F6: a fork PR's build must not upload an installer that
    looks like ours."""
    job = windows_build_job()
    (upload,) = [s for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert upload["if"] == ("${{ !cancelled() && (github.event_name == 'push' || "
                            "github.event.pull_request.head.repo.full_name == github.repository) }}")


def test_the_iscc_step_checks_the_inno_version_and_the_version_calls():
    """QA B2 and review-m4 F7: fail with the real cause, not a later 'file not found'."""
    job = windows_build_job()
    (step,) = [s for s in job["steps"] if "ISCC" in s.get("run", "")]
    run = step["run"]
    assert '[version]"6.3.0"' in run and "throw" in run
    # PR #4 CI: ISCC.exe's numeric version fields are 0, which read as "Inno Setup 0.0.0".
    assert "FileMajorPart" not in run and "DisplayVersion" in run
    assert 'throw "could not read the Inno Setup version' in run
    for call in ("$ver = python packaging/buildinfo.py version",
                 "$num = python packaging/buildinfo.py numeric-version"):
        after = run.split(call, 1)[1].splitlines()[1]
        assert after.strip().startswith("if ($LASTEXITCODE -ne 0 -or -not $"), call
        assert "throw" in after


# -- release.yml: tags only, draft only ------------------------------------------------


def release_jobs():
    return workflows()["release.yml"]["jobs"]


def job_runs(job):
    return [step["run"] for step in job.get("steps", []) if "run" in step]


def all_runs(flow):
    return [run for job in flow["jobs"].values() for run in job_runs(job)]


def release_create_run():
    """The one run: line in release.yml that calls gh release."""
    (run,) = [r for r in all_runs(workflows()["release.yml"]) if "gh release" in r]
    assert run.count("gh release") == 1
    return run


def test_release_runs_only_on_a_v_tag_push():
    assert workflows()["release.yml"][True] == {"push": {"tags": ["v*"]}}
    for name, flow in workflows().items():
        if name != "release.yml":
            assert "tags" not in str(flow[True]), name


def test_release_only_drafts():
    """Publishing stays a person's step: the workflow creates a draft and
    nothing in any workflow publishes or edits a release."""
    for name, flow in workflows().items():
        runs = "\n".join(all_runs(flow))
        assert "--draft=false" not in runs and "gh release edit" not in runs, name
        assert "/releases" not in runs, name
        for call in re.findall(r"gh api [^\n]*", runs):  # reads only (GET, no fields)
            assert name == "release.yml", call
            assert not re.search(r" (-X|--method|-f|-F|--field|--raw-field|--input)\b", call), call
        uses = [step["uses"] for job in flow["jobs"].values()
                for step in job.get("steps", []) if "uses" in step]
        for action in uses:  # no third-party release action could publish one
            assert action.split("@")[0] in (
                "actions/checkout", "actions/setup-python", "actions/cache",
                "actions/upload-artifact", "actions/download-artifact"), (name, action)
        if name != "release.yml":
            assert "gh release" not in runs, name
    assert "gh release create \"$TAG\" --draft --verify-tag" in release_create_run()


def test_only_the_draft_job_can_write_and_it_runs_no_build_code():
    for name, flow in workflows().items():
        for job_name, job in flow["jobs"].items():
            if (name, job_name) == ("release.yml", "draft"):
                assert job["permissions"] == {"contents": "write"}
            elif (name, job_name) == ("release.yml", "check"):  # reads the commit's CI results
                assert job["permissions"] == {"contents": "read", "checks": "read"}
            else:
                assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}
    draft = release_jobs()["draft"]
    runs = "\n".join(job_runs(draft))
    assert "pip" not in runs and "pytest" not in runs and "TranscriptionFlow.exe" not in runs
    assert draft["runs-on"] == "ubuntu-latest"


def test_release_checks_then_builds_on_windows_then_drafts():
    jobs = release_jobs()
    assert jobs["build"]["needs"] == "check" and jobs["draft"]["needs"] == "build"
    assert jobs["build"]["with"] == {"release-tag": "${{ github.ref_name }}"}
    check = "\n".join(job_runs(jobs["check"]))
    assert 'check-tag "$TAG"' in check and 'release-notes "$version"' in check
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in check
    assert jobs["check"]["steps"][0]["with"]["fetch-depth"] == 0  # origin/main is fetched
    assert windows_build_job()["runs-on"] == "windows-latest"
    (ci,) = [s for s in jobs["check"]["steps"] if "check-ci" in s.get("run", "")]
    assert ci["run"].splitlines() == [
        'gh api "repos/$GITHUB_REPOSITORY/commits/$GITHUB_SHA/check-runs?filter=latest&per_page=100"'
        " > check-runs.json",
        "python3 packaging/buildinfo.py check-ci check-runs.json"]
    assert ci["env"] == {"GH_TOKEN": "${{ github.token }}"}


def check_names(flow):
    """The check-run names a workflow's jobs get on GitHub."""
    names = set()
    for job in flow["jobs"].values():
        if "uses" in job:
            called = workflows()[Path(job["uses"]).name]
            names |= {f"{job['name']} / {c['name']}" for c in called["jobs"].values()}
        else:
            names.add(job["name"])
    return names


def test_the_release_waits_for_exactly_the_checks_ci_makes():
    """review-m5 F3: CI_CHECKS must name every ci.yml job, and the release's
    own jobs must not have one of those names (they run on the same commit)."""
    assert check_names(workflows()["ci.yml"]) == set(buildinfo.CI_CHECKS)
    assert not check_names(workflows()["release.yml"]) & set(buildinfo.CI_CHECKS)
    assert windows_build_job()["name"] == "Windows build, install and uninstall"


def ci_run(name, status="completed", conclusion="success", app="github-actions"):
    return {"name": name, "status": status, "conclusion": conclusion, "app": {"slug": app}}


def ci_reply(runs, total=None):
    return {"total_count": len(runs) if total is None else total, "check_runs": runs}


GREEN = [ci_run(name) for name in buildinfo.CI_CHECKS]


def test_ci_passed_when_every_ci_check_succeeded():
    # The release's own check job, still running, is not one of them.
    others = [ci_run("Check the tag, the changelog entry, CI and that the commit is on main",
                     status="in_progress", conclusion=None)]
    assert buildinfo.ci_problems(ci_reply(GREEN + others)) == []


LINUX = buildinfo.CI_CHECKS[0]


@pytest.mark.parametrize("reply, why", [
    (ci_reply(GREEN[1:]), f"no '{LINUX}' check"),
    (ci_reply([ci_run(LINUX, conclusion="failure"), *GREEN[1:]]), f"'{LINUX}' is completed/failure"),
    (ci_reply([ci_run(LINUX, conclusion="cancelled"), *GREEN[1:]]), "is completed/cancelled"),
    (ci_reply([ci_run(LINUX, status="in_progress", conclusion=None), *GREEN[1:]]),
     "is in_progress/None"),
    (ci_reply([ci_run(LINUX, app="someone-else"), *GREEN[1:]]), f"no '{LINUX}' check"),
    (ci_reply([ci_run(LINUX, conclusion="failure"), *GREEN]), "is completed/failure"),
    (ci_reply(GREEN, total=101), "the list is incomplete"),
    ({"total_count": 0}, "no check_runs list"),
    ([], "no check_runs list"),
])
def test_ci_not_passed_fails_closed(reply, why):
    problems = buildinfo.ci_problems(reply)
    assert len(problems) == 1 and why in problems[0], problems


def test_the_check_ci_command(tmp_path):
    script = str(PACKAGING / "buildinfo.py")
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(ci_reply(GREEN)), "utf-8")
    bad.write_text("<html>rate limited</html>", "utf-8")
    for path, code, out in ((good, 0, "CI passed"), (bad, 1, "FAIL: cannot read"),
                            (tmp_path / "missing.json", 1, "FAIL: cannot read")):
        done = subprocess.run([sys.executable, "-I", script, "check-ci", str(path)],
                              capture_output=True, text=True, timeout=60)
        assert (done.returncode, done.stdout.startswith(out)) == (code, True), done


# -- the draft step, run for real in bash with a stand-in gh ----------------------


def run_draft_step(tmp_path, tag, version, moved_to=None):
    """Runs the draft step's script with the build's files in release/ and a
    fake gh that answers `gh api` with the tag's commit and records the rest."""
    work = tmp_path / "work"
    (work / "packaging").mkdir(parents=True)
    (work / "packaging" / "buildinfo.py").write_bytes((PACKAGING / "buildinfo.py").read_bytes())
    (work / "CHANGELOG.md").write_text(f"## [{version}] - 2026-10-30\n\n- Notes.\n", "utf-8")
    (work / "release").mkdir()
    for name in (f"TranscriptionFlow-Setup-{version}.exe",
                 f"TranscriptionFlow-{version}-portable.zip"):
        (work / "release" / name).write_bytes(name.encode())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "$@"\n')
    (bin_dir / "gh").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" >> "$GH_LOG"\necho ---- >> "$GH_LOG"\n'
        'if [ "$1" = api ]; then echo "$FAKE_TAG_SHA"; fi\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    built = "1" * 40
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "TAG": tag,
           "GITHUB_SHA": built, "GH_REPO": "owner/repo", "GH_LOG": str(tmp_path / "gh.log"),
           "FAKE_TAG_SHA": moved_to or built}
    done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                           release_create_run()], cwd=work, env=env, capture_output=True,
                          text=True, timeout=60)
    log = (tmp_path / "gh.log").read_text() if (tmp_path / "gh.log").exists() else ""
    calls = [c.split("\n")[:-1] for c in log.split("----\n") if c]
    return done, calls


needs_bash = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"),
                                reason="runs the ubuntu-latest draft step in bash")


@needs_bash
def test_the_draft_step_creates_a_normal_draft_for_a_release(tmp_path):
    done, calls = run_draft_step(tmp_path, "v0.1.0", "0.1.0")
    assert done.returncode == 0, done.stderr
    api, create = calls
    assert api == ["api", "repos/owner/repo/commits/v0.1.0", "--jq", ".sha"]
    assert create[:5] == ["release", "create", "v0.1.0", "--draft", "--verify-tag"]
    assert "--prerelease" not in create
    assert create[-3:] == ["release/TranscriptionFlow-Setup-0.1.0.exe",
                           "release/TranscriptionFlow-0.1.0-portable.zip",
                           "release/SHA256SUMS.txt"]


@needs_bash
@pytest.mark.parametrize("tag, version", [("v0.1.0rc1", "0.1.0rc1"), ("v0.2.0a1", "0.2.0a1"),
                                          ("v0.2.0b3", "0.2.0b3")])
def test_the_draft_step_marks_a_pre_release(tmp_path, tag, version):
    """review-m5 F5: an rc must never become the 'latest' release."""
    done, calls = run_draft_step(tmp_path, tag, version)
    assert done.returncode == 0, done.stderr
    create = calls[-1]
    assert create[:6] == ["release", "create", tag, "--draft", "--verify-tag", "--prerelease"]


@needs_bash
def test_the_draft_step_stops_if_the_tag_moved_after_the_build(tmp_path):
    """review-m5 F6: the files were built from GITHUB_SHA; a draft on a
    moved tag would attach them to another commit."""
    done, calls = run_draft_step(tmp_path, "v0.1.0", "0.1.0", moved_to="2" * 40)
    assert done.returncode == 1
    assert f"v0.1.0 now points at {'2' * 40}, not at the built commit" in done.stdout
    assert [c[0] for c in calls] == ["api"]  # no gh release create


def test_the_draft_has_the_installer_the_zip_and_their_sha256sums():
    steps = release_jobs()["draft"]["steps"]
    (download,) = [s for s in steps if "download-artifact" in s.get("uses", "")]
    (upload,) = [s for s in windows_build_job()["steps"] if "upload-artifact" in s.get("uses", "")]
    assert download["with"]["name"] == upload["with"]["name"]
    create = release_create_run()
    assert ('sha256sum "TranscriptionFlow-Setup-$version.exe" '
            '"TranscriptionFlow-$version-portable.zip" > SHA256SUMS.txt') in create
    for asset in ('"release/TranscriptionFlow-Setup-$version.exe"',
                  '"release/TranscriptionFlow-$version-portable.zip"', "release/SHA256SUMS.txt",
                  "--notes-file notes.md"):
        assert asset in create, asset
    assert 'release-notes "$version" > notes.md' in create


def test_the_tag_reaches_scripts_only_through_env():
    """A tag name is attacker-shaped text: never expand ${{ }} inside run:."""
    for name, flow in workflows().items():
        for run in all_runs(flow):
            assert isinstance(run, str) and "${{" not in run, (name, run)


def test_a_release_build_stamps_the_tag_version_before_pyinstaller():
    steps = windows_build_job()["steps"]
    names = [s.get("name", "") for s in steps]
    stamp = names.index("Release builds take their version from the tag")
    assert stamp < names.index("Build the onedir with PyInstaller")
    assert steps[stamp]["if"] == "${{ inputs.release-tag != '' }}"
    assert steps[stamp]["run"] == 'python packaging/buildinfo.py stamp-version "$env:RELEASE_TAG"'
    assert steps[stamp]["env"] == {"RELEASE_TAG": "${{ inputs.release-tag }}"}


# -- release versions and notes ------------------------------------------------------------


@pytest.mark.parametrize("tag, version", [("v0.1.0", "0.1.0"), ("v1.20.3", "1.20.3"),
                                          ("v0.2.0rc1", "0.2.0rc1"), ("v0.2.0b2", "0.2.0b2")])
def test_tag_version(tag, version):
    assert buildinfo.tag_version(tag) == version


@pytest.mark.parametrize("tag", ["0.1.0", "v0.1", "v0.1.0-beta", "v0.1.0.dev0", "v0.1.0\n",
                                 "v0.1.0; x", "V0.1.0", "vv0.1.0", "v0.1.0rc",
                                 # QA B1: ASCII digits only, no leading zeros
                                 "v\u0660.\u0661.\u0660", "v0.1.0rc\uff11", "v00.1.0", "v0.01.0",
                                 "v0.1.00", "v0.1.0rc01"])
def test_tag_version_refuses_anything_else(tag):
    with pytest.raises(SystemExit, match="not a release tag"):
        buildinfo.tag_version(tag)


def source_tree(tmp_path, version="0.1.0.dev0"):
    """A copy of buildinfo.py and the package __init__ with the given version."""
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "buildinfo.py").write_bytes((PACKAGING / "buildinfo.py").read_bytes())
    init = tmp_path / "src" / "transcription_flow" / "__init__.py"
    init.parent.mkdir(parents=True)
    real = (ROOT / "src" / "transcription_flow" / "__init__.py").read_text("utf-8")
    init.write_text(buildinfo._VERSION_LINE.sub(f'__version__ = "{version}"', real), "utf-8")
    return init


def test_stamp_version_turns_the_dev_source_into_the_tag_version(tmp_path):
    init = source_tree(tmp_path)
    before = init.read_text("utf-8")
    assert buildinfo.stamp_version("v0.1.0", root=tmp_path) == "0.1.0"
    assert buildinfo.app_version(tmp_path) == "0.1.0"
    assert init.read_text("utf-8") == before.replace('"0.1.0.dev0"', '"0.1.0"')
    assert buildinfo.numeric_version(buildinfo.app_version(tmp_path)) == "0.1.0.0"
    assert "StringStruct('ProductVersion', '0.1.0')" in buildinfo.version_info_text("0.1.0")


def test_stamp_version_refuses_a_tag_for_another_release(tmp_path):
    init = source_tree(tmp_path)
    before = init.read_text("utf-8")
    for tag in ("v0.2.0", "v0.1.1", "v1.1.0"):
        with pytest.raises(SystemExit, match="does not match the source version 0.1.0.dev0"):
            buildinfo.stamp_version(tag, root=tmp_path)
    assert init.read_text("utf-8") == before


@pytest.mark.parametrize("tag, version", [("v0.0.0", "0.0.0"), ("v10.0.0rc0", "10.0.0rc0")])
def test_tag_version_takes_zero_and_many_digit_parts(tag, version):
    assert buildinfo.tag_version(tag) == version


def test_stamp_version_keeps_the_pre_release_part(tmp_path):
    """review-m5 F5: v0.1.0rc1 builds 0.1.0rc1, so the file names match."""
    init = source_tree(tmp_path, "0.1.0rc1.dev0")
    before = init.read_text("utf-8")
    assert buildinfo.stamp_version("v0.1.0rc1", root=tmp_path) == "0.1.0rc1"
    assert buildinfo.app_version(tmp_path) == "0.1.0rc1"
    assert init.read_text("utf-8") == before.replace('"0.1.0rc1.dev0"', '"0.1.0rc1"')


@pytest.mark.parametrize("source, tag", [("0.1.0.dev0", "v0.1.0rc1"), ("0.1.0rc1.dev0", "v0.1.0a1"),
                                         ("0.1.0rc1.dev0", "v0.1.0"), ("0.1.0rc1", "v0.1.0rc2"),
                                         ("0.1.0", "v0.1.0rc1")])
def test_check_tag_compares_the_pre_release_part_too(tmp_path, source, tag):
    """QA B2: a tag is accepted only for exactly the release being worked on."""
    source_tree(tmp_path, source)
    with pytest.raises(SystemExit, match=f"does not match the source version {re.escape(source)}"):
        buildinfo.check_tag(tag, root=tmp_path)


@pytest.mark.parametrize("source, tag", [("0.1.0.dev0", "v0.1.0"), ("0.1.0", "v0.1.0"),
                                         ("0.1.0rc1.dev0", "v0.1.0rc1"), ("0.1.0rc1", "v0.1.0rc1"),
                                         ("0.1.0rc1.dev12", "v0.1.0rc1")])
def test_check_tag_takes_the_release_being_worked_on(tmp_path, source, tag):
    source_tree(tmp_path, source)
    assert buildinfo.check_tag(tag, root=tmp_path) == tag[1:]


def test_check_tag_changes_nothing(tmp_path):
    init = source_tree(tmp_path)
    before = init.read_text("utf-8")
    assert buildinfo.check_tag("v0.1.0", root=tmp_path) == "0.1.0"
    with pytest.raises(SystemExit, match="does not match the source version"):
        buildinfo.check_tag("v0.2.0", root=tmp_path)
    assert init.read_text("utf-8") == before


def test_the_cli_stamps_what_ci_then_reads_as_the_version(tmp_path):
    """The release build's steps: stamp-version v0.1.0, then the ISCC step's
    `version` and `numeric-version` calls give 0.1.0, not 0.1.0.dev0."""
    source_tree(tmp_path)
    script = str(tmp_path / "packaging" / "buildinfo.py")

    def run(*args):
        return subprocess.run([sys.executable, script, *args], capture_output=True,
                              text=True, env=child_env(), timeout=60)

    assert run("version").stdout.strip() == "0.1.0.dev0"
    assert run("stamp-version", "v0.1.0").stdout.strip() == "0.1.0"
    assert run("version").stdout.strip() == "0.1.0"
    assert run("numeric-version").stdout.strip() == "0.1.0.0"
    bad = run("stamp-version", "v0.1")
    assert bad.returncode == 1 and "not a release tag" in bad.stderr


CHANGELOG_FIXTURE = """# Changelog

## [Unreleased]

- Next thing.

## [0.2.0] - 2026-11-02

### Fixed
- Two.

## [0.1.0] - 2026-10-30

### Added
- One.

[0.2.0]: https://example.invalid/v0.2.0
"""


def test_release_notes_are_that_versions_section(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG_FIXTURE, "utf-8")
    assert buildinfo.release_notes("0.2.0", changelog) == "### Fixed\n- Two.\n"
    assert buildinfo.release_notes("0.1.0", changelog) == "### Added\n- One.\n"


@pytest.mark.parametrize("text, why", [
    ("## [0.1.0] - 2026-10-30\n\n- One.\n", "no '## [0.3.0]' section"),
    ("## [0.3.0] - Unreleased\n\n- One.\n", "give '## [0.3.0]' its release date"),
    ("## [0.3.0]\n\n- One.\n", "give '## [0.3.0]' its release date"),
    ("## [0.3.0] - 2026-10-30\n\n## [0.1.0] - 2026-10-01\n- One.\n", "section is empty"),
    ("## [0.3.0.1] - 2026-10-30\n\n- One.\n", "no '## [0.3.0]' section"),
    # QA B3: a real calendar date, written YYYY-MM-DD in ASCII digits
    ("## [0.3.0] - 2026-13-45\n\n- One.\n", "'2026-13-45' in '## [0.3.0]' is not a real date"),
    ("## [0.3.0] - 2026-02-30\n\n- One.\n", "is not a real date"),
    ("## [0.3.0] - 20261030\n\n- One.\n", "give '## [0.3.0]' its release date"),
    ("## [0.3.0] - \u0662\u0660\u0662\u0666-10-30\n\n- One.\n", "give '## [0.3.0]' its release date"),
    # QA B4: two sections for one version are ambiguous
    ("## [0.3.0] - 2026-10-30\n\n- One.\n\n## [0.3.0] - 2026-10-31\n\n- Two.\n",
     "has 2 '## [0.3.0]' sections"),
])
def test_release_notes_stop_a_release_without_a_dated_entry(tmp_path, text, why):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(text, "utf-8")
    with pytest.raises(SystemExit, match=re.escape(why)):
        buildinfo.release_notes("0.3.0", changelog)


def test_the_changelog_has_an_entry_for_the_source_version(tmp_path):
    """The version being worked on has a section with notes; it is dated only
    in the commit that gets tagged, so date a copy to read its notes."""
    release = buildinfo.target_version(buildinfo.app_version())
    text = (ROOT / "CHANGELOG.md").read_text("utf-8")
    heading = re.search(rf"^## \[{re.escape(release)}\] - (Unreleased|\d{{4}}-\d{{2}}-\d{{2}})$",
                        text, re.M)
    assert heading, release
    dated = tmp_path / "CHANGELOG.md"
    dated.write_text(text.replace(heading.group(0), f"## [{release}] - 2026-01-01"), "utf-8")
    notes = buildinfo.release_notes(release, dated)
    assert "### Added" in notes and "## [" not in notes


def test_the_release_notes_have_only_absolute_links(tmp_path):
    """review-m5 F4: on a release page a relative link resolves under
    /releases/tag/ and 404s."""
    release = buildinfo.target_version(buildinfo.app_version())
    text = (ROOT / "CHANGELOG.md").read_text("utf-8")
    dated = tmp_path / "CHANGELOG.md"
    dated.write_text(re.sub(rf"^## \[{re.escape(release)}\] - .*$", f"## [{release}] - 2026-01-01",
                            text, count=1, flags=re.M), "utf-8")
    notes = buildinfo.release_notes(release, dated)
    links = re.findall(r"\]\(([^)\s]*)\)", notes) + re.findall(r"<([^>\s]+)>", notes)
    assert links, "the notes link the SmartScreen guide"
    for link in links:
        assert link.startswith(f"{buildinfo.REPO_URL}/") or re.match(r"https://", link), link
    assert f"{buildinfo.REPO_URL}/blob/main/docs/smartscreen.md" in notes


@pytest.mark.parametrize("args, setup, message", [
    (["release-notes", "0.1.0"], None, "cannot read CHANGELOG.md: "),
    (["release-notes", "0.1.0"], "latin1", "cannot read CHANGELOG.md: "),
    (["version"], "no-version", 'has no __version__ = "..." line'),
    (["check-tag", "v0.1.0"], "no-version", 'has no __version__ = "..." line'),
])
def test_bad_inputs_give_one_line_not_a_traceback(tmp_path, args, setup, message):
    """QA B5."""
    init = source_tree(tmp_path)
    if setup == "latin1":
        (tmp_path / "CHANGELOG.md").write_bytes("## [0.1.0] - 2026-10-30\n\n- Caf\u00e9.\n".encode("latin-1"))
    elif setup == "no-version":
        init.write_text(buildinfo._VERSION_LINE.sub("", init.read_text("utf-8")), "utf-8")
    done = subprocess.run([sys.executable, "-I", str(tmp_path / "packaging" / "buildinfo.py"), *args],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 1 and "Traceback" not in done.stderr, done.stderr
    assert done.stderr.count("\n") == 1 and message in done.stderr, done.stderr


# -- CI smoke: the frozen checks, Defender and the uninstall sentinels ---------------------


def test_check_frozen_runs_the_bundle_check(tmp_path, monkeypatch, capsys):
    """review-m4 F1: the frozen exe imports PortAudio, the pynput and pystray
    backends, Tk and the UI, not only the selftest's modules."""
    onedir = fake_onedir(tmp_path)
    runs = []

    def fake_run(exe, *args, env=None):
        runs.append(args[0])
        ok = not (args[0] == "--check-bundle" and bundle_fails)
        return 0 if ok else 1, {"ok": ok}, ""

    monkeypatch.setattr(ci_smoke, "run_exe", fake_run)
    for bundle_fails in (False, True):
        check = ci_smoke.Checks()
        ci_smoke.check_frozen(check, onedir, tmp_path / "x.wav", tmp_path / "models")
        assert check.failed == bundle_fails
    assert runs.count("--check-bundle") == 2 and "--selftest" in runs
    assert "FAIL: --check-bundle" in capsys.readouterr().out


@pytest.mark.parametrize("output, threats", [
    ("Scan starting...\nScan finished.\nScanning C:\\dist found no threats.\n", 0),
    ("Scanning C:\\dist found 1 threats.\n\nThreat : Virus:DOS/EICAR_Test_File\n", 1),
    ("Scan starting...\nCmdTool: Failed with hr = 0x80508023. Check C:\\...\\MpCmdRun.log\n",
     None),
    ("Scan starting...\nCmdTool: The service is not running\n", None),
    ("", None),
    # a threat is never hidden by an error line next to it
    ("Scanning C:\\dist found 2 threats.\nCmdTool: Failed with hr = 0x1\n", 2),
])
def test_the_defender_verdict_comes_from_the_output_not_the_exit_code(output, threats):
    """QA B1: MpCmdRun exits 2 both for a threat and for a scan that can't run."""
    assert ci_smoke.defender_verdict(output)[0] == threats


def test_defender_reports_not_run_when_the_scan_cannot_run(tmp_path, monkeypatch, capsys):
    tool = tmp_path / "MpCmdRun.exe"
    tool.write_text("x")
    monkeypatch.setattr(ci_smoke, "defender_tool", lambda: tool)
    outputs = iter(["CmdTool: Failed with hr = 0x80508023", "found 1 threats", "found no threats"])
    monkeypatch.setattr(ci_smoke.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 2, next(outputs), ""))
    results = []
    for _ in range(3):
        check = ci_smoke.Checks()
        ci_smoke.defender(check, tmp_path)
        results.append(check.failed)
    assert results == [0, 1, 0]
    assert "NOT RUN: Defender scan (the scan did not run; MpCmdRun exit 2)" in \
        capsys.readouterr().out


def test_the_uninstall_sentinels_sit_outside_the_data_folders(tmp_path, monkeypatch):
    """review-m4 F2: next to both data folders, and a junction target outside them."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "temp"))
    data = ci_smoke.data_dirs()
    keep = ci_smoke.sentinels()
    assert [p.parent.parent for p in keep[:2]] == [d.parent for d in data]
    assert all(not p.is_relative_to(d) for p in keep for d in data)
    assert keep[2].parent == ci_smoke.junction_target() == tmp_path / "temp" / "tf-junction-target"
    assert ci_smoke.junction().is_relative_to(data[0] / "models")
