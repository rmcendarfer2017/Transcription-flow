"""Static checks of the Windows packaging (M4): the PyInstaller spec, the Inno
Setup script, the CI workflow and the build helpers. The real build runs only
in the windows-build CI job; these catch drift between the packaging and the
app (mutex name, Run value, data folders) on any OS."""

import ast
import importlib.util
import os
import re
import subprocess
import sys
import textwrap
import zipfile
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
    buildinfo.add_licences(tmp_path, dists, python_licence=tmp_path / "none")
    assert (tmp_path / "LICENSE.txt").read_text("utf-8") == (ROOT / "LICENSE").read_text("utf-8")
    assert (tmp_path / "licenses" / "LGPL-3.0.txt").is_file()
    assert (tmp_path / "licenses" / "GPL-3.0.txt").is_file()
    assert (tmp_path / "licenses" / "numpy" / "LICENSE.txt").is_file()
    assert (tmp_path / "licenses" / "faster-whisper" / "LICENSE").is_file()
    note = (tmp_path / "licenses" / "ctranslate2" / "LICENSE-NOTE.txt").read_text("utf-8")
    assert "MIT" in note  # its wheel ships no licence file
    notices = (tmp_path / "THIRD-PARTY-NOTICES.txt").read_text("utf-8")
    assert "pynput and pystray" in notices and "_internal/pynput/" in notices
    assert re.search(r"^numpy \S+: BSD-3-Clause", notices, re.M)


class FakeDist:
    def __init__(self, name, files=()):
        self.metadata = {"Name": name, "License-Expression": "MIT"}
        self.version = "1.0"
        self.files = [Path(f) for f in files]

    def locate_file(self, rel):
        return Path(__file__)  # any readable file


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
    exe = "{app}\\TranscriptionFlow.exe"
    assert entry["ValueData"] == autostart.launch_command(exe, frozen=True)
    assert entry["ValueData"].endswith(" --tray")


def test_the_autostart_task_is_on_by_default():
    (task,) = [iss_entry(line) for line in iss_sections()["Tasks"]]
    assert task["Name"] == "autostart" and "unchecked" not in task.get("Flags", "")


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
    assert "DelTree(ExpandConstant('{localappdata}\\TranscriptionFlow')" in code
    assert "DelTree(ExpandConstant('{userappdata}\\TranscriptionFlow')" in code
    assert "MB_YESNO or MB_DEFBUTTON1" in code  # Yes is the default (plan 3.9)
    assert "HasParam('/DELETEUSERDATA')" in code and "UninstallSilent" in code


# -- CI workflows ---------------------------------------------------------------------


def workflows():
    return {p.name: yaml.safe_load(p.read_text("utf-8")) for p in WORKFLOWS}


def test_every_workflow_parses_and_runs_on_github_hosted_runners_only():
    assert WORKFLOWS
    for name, flow in workflows().items():
        assert flow["permissions"] == {"contents": "read"}, name
        for job_name, job in flow["jobs"].items():
            assert job["runs-on"] in ("ubuntu-latest", "windows-latest"), (name, job_name)
            assert "self-hosted" not in str(job)


def test_every_action_is_pinned_by_commit_sha():
    for name, flow in workflows().items():
        for job in flow["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step


def test_the_windows_build_job_builds_installs_uninstalls_and_uploads():
    job = workflows()["ci.yml"]["jobs"]["windows-build"]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    for needed in ("requirements-build-lock.txt", "PyInstaller --noconfirm --clean "
                   "packaging/transcription_flow.spec", "ci_smoke.py frozen dist/TranscriptionFlow",
                   "ISCC", "packaging\\installer.iss", "ci_smoke.py install",
                   "ci_smoke.py uninstall\n", "ci_smoke.py uninstall --delete-user-data",
                   "portable-zip", "ci_smoke.py sizes", "--verify --model tiny.en"):
        assert needed in runs + "\n", needed
    upload = [s for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert len(upload) == 1
    assert "Setup-*.exe" in upload[0]["with"]["path"]
    assert "-portable.zip" in upload[0]["with"]["path"]
    assert "release" not in str(job).lower().replace("no release", "")


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
