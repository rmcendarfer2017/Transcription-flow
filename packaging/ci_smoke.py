"""Smoke checks the Windows CI job runs on the built app (see .github/workflows/ci.yml).

    python packaging/ci_smoke.py frozen DIR WAV MODELS_DIR   selftest, bundle check, GPU probe
    python packaging/ci_smoke.py install SETUP_EXE            silent install, check what it wrote
    python packaging/ci_smoke.py upgrade SETUP_EXE            install over it: autostart choice kept
    python packaging/ci_smoke.py uninstall [--delete-user-data]
    python packaging/ci_smoke.py defender PATH                 Defender scan if the runner has it
    python packaging/ci_smoke.py sizes FILE_OR_DIR...          table for the job summary

The exe is windowed (no console), so it is run the way the app runs its own
GPU probe: a child process with captured output. Each check prints PASS,
FAIL or NOT RUN; any FAIL exits 1.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src")]
import buildinfo  # noqa: E402
from transcription_flow.autostart import RUN_KEY, VALUE_NAME  # noqa: E402 (the app's own)

APP_NAME = buildinfo.APP_NAME
EXE = f"{buildinfo.EXE_NAME}.exe"
APP_ID = "{C41FBC91-05D3-4BBB-ADAB-FBC5C12CBECD}"  # installer.iss AppId
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1"
TIMEOUT = 300


class Checks:
    def __init__(self):
        self.failed = 0

    def __call__(self, ok: bool, what: str, detail: str = "") -> bool:
        print(f"{'PASS' if ok else 'FAIL'}: {what}{f' ({detail})' if detail else ''}", flush=True)
        self.failed += not ok
        return ok


def last_json(stdout: str):
    """The JSON report on the last non-empty line, or None."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError):
        return None


def run_exe(exe: Path, *args, env=None):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=TIMEOUT,
                          stdin=subprocess.DEVNULL, creationflags=flags, env=env)
    return done.returncode, last_json(done.stdout), done.stderr[-2000:]


def check_frozen(check, onedir: Path, wav: Path, models_dir: Path) -> None:
    exe = onedir / EXE
    problems = buildinfo.check_onedir(onedir)
    for problem in problems:
        check(False, problem)
    if not problems:
        check(True, f"{onedir.name} layout: LGPL modules loose, licences, VAD asset, "
              "ctranslate2, no PyAV")
    env = dict(os.environ, TFLOW_HOME=str(models_dir.parent / "selftest-home"))
    code, report, err = run_exe(exe, "--selftest", str(wav), "--model", "tiny.en",
                                "--models-dir", str(models_dir), "--expect", "hello", env=env)
    check(code == 0 and report is not None and report.get("ok") is True,
          "--selftest transcribes the fixture with tiny.en", f"exit {code}, {report or err}")
    code, report, err = run_exe(exe, "--check-bundle", env=env)
    check(code == 0 and report is not None and report.get("ok") is True,
          "--check-bundle loads PortAudio, the pynput and pystray backends, the tray menu, "
          "Tk and the UI",
          f"exit {code}, {report or err}")
    model_dir = models_dir / "tiny.en"
    code, report, err = run_exe(exe, "--probe-gpu", "--model-dir", str(model_dir), env=env)
    check(code == 0 and report is not None and isinstance(report.get("ok"), bool),
          "--probe-gpu answers cleanly", f"exit {code}, {report or err}")


def install_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP_NAME


def shortcut() -> Path:
    return (Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / f"{APP_NAME}.lnk")


def data_dirs():
    return [Path(os.environ["LOCALAPPDATA"]) / "TranscriptionFlow",
            Path(os.environ["APPDATA"]) / "TranscriptionFlow"]


def sentinels():
    """Files /DELETEUSERDATA must not touch (review-m4 F2): folders next to
    the two data folders, and the target of a junction inside one of them
    (a user who moved models to another drive with mklink /J)."""
    local, roaming = (Path(os.environ[v]) for v in ("LOCALAPPDATA", "APPDATA"))
    return [local / "TranscriptionFlowKeep" / "keep.txt",
            roaming / "TranscriptionFlow.bak" / "keep.txt", junction_target() / "keep.txt"]


def junction() -> Path:
    return data_dirs()[0] / "models" / "linked"


def junction_target() -> Path:
    local = Path(os.environ["LOCALAPPDATA"])
    return Path(os.environ.get("RUNNER_TEMP", local.parent)) / "tf-junction-target"


def make_sentinels(check) -> None:
    for path in sentinels():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep", "utf-8")
    link = junction()
    if not os.path.isjunction(link):  # a plain uninstall kept the first one
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(junction_target())],
                              capture_output=True, text=True, timeout=60)
        check(done.returncode == 0, "junction in the models folder for the uninstall check",
              done.stdout + done.stderr)
    check((link / "keep.txt").is_file(), "the junction points outside the data folder")


def reg_value(key: str, name: str):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, name)[0]
    except FileNotFoundError:
        return None


def reg_key_exists(key: str) -> bool:
    import winreg

    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, key).Close()
        return True
    except FileNotFoundError:
        return False


def silent_install(setup: Path, log_name: str = "install.log") -> int:
    log = Path(os.environ.get("RUNNER_TEMP", setup.parent)) / log_name
    return subprocess.run([str(setup), "/VERYSILENT", "/CURRENTUSER", "/SUPPRESSMSGBOXES",
                           "/NORESTART", f"/LOG={log}"], timeout=TIMEOUT).returncode


def check_install(check, setup: Path) -> None:
    code = silent_install(setup)
    check(code == 0, "silent install", f"exit {code}")
    app = install_dir()
    for rel in (EXE, "unins000.exe", "_internal", "LICENSE.txt", "THIRD-PARTY-NOTICES.txt",
                "licenses/LGPL-3.0.txt", "_internal/pynput/__init__.py"):
        check((app / rel).exists(), f"installed {rel}")
    want = f'"{app / EXE}" --tray'
    got = reg_value(RUN_KEY, VALUE_NAME)
    check(got == want, "Start with Windows is on by default (HKCU Run value)", repr(got))
    check(shortcut().is_file(), "Start menu shortcut")
    check(reg_key_exists(UNINSTALL_KEY), "listed in Apps (per-user uninstall key)")
    for folder in data_dirs():  # stand-ins for settings and models, for the uninstall check
        (folder / "models").mkdir(parents=True, exist_ok=True)
        (folder / "models" / "marker.txt").write_text("ci", "utf-8")
    make_sentinels(check)


def check_upgrade(check, setup: Path) -> None:
    """review-m4 F4: installing over an install keeps the Run value if it is
    there and doesn't bring it back once the user turned autostart off."""
    import winreg

    want = reg_value(RUN_KEY, VALUE_NAME)
    code = silent_install(setup, "upgrade-1.log")
    check(code == 0 and reg_value(RUN_KEY, VALUE_NAME) == want,
          "an upgrade keeps autostart on", f"exit {code}, {reg_value(RUN_KEY, VALUE_NAME)!r}")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, VALUE_NAME)  # what Settings' "Start with Windows" off does
    code = silent_install(setup, "upgrade-2.log")
    check(code == 0 and reg_value(RUN_KEY, VALUE_NAME) is None,
          "an upgrade keeps autostart off", f"exit {code}, {reg_value(RUN_KEY, VALUE_NAME)!r}")


def wait_until(done, seconds: float) -> bool:
    """The uninstaller hands over to a copy of itself in %TEMP% and its
    first process can exit before the files and keys are gone."""
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        time.sleep(1)
    return done()


def wait_gone(path: Path, seconds: float = 120) -> bool:
    return wait_until(lambda: not path.exists(), seconds)


def check_uninstall(check, delete_user_data: bool) -> None:
    app = install_dir()
    args = [str(app / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    if delete_user_data:
        args.append("/DELETEUSERDATA")
    code = subprocess.run(args, timeout=TIMEOUT).returncode
    check(code == 0, "silent uninstall", f"exit {code}")
    check(wait_gone(app), "install folder removed", str(app))
    check(reg_value(RUN_KEY, VALUE_NAME) is None, "Run value removed")
    check(not shortcut().exists(), "Start menu shortcut removed")
    check(wait_until(lambda: not reg_key_exists(UNINSTALL_KEY), 30), "uninstall key removed")
    for folder in data_dirs():
        if delete_user_data:
            check(wait_gone(folder, 30), f"/DELETEUSERDATA removed {folder.name} data", str(folder))
        else:
            check(folder.exists(), "a plain silent uninstall keeps settings and models",
                  str(folder))
    keep = sentinels()
    for path in keep:
        check(path.is_file(), "the uninstall left this alone", str(path))
    if delete_user_data:
        for path in keep:
            shutil.rmtree(path.parent, ignore_errors=True)


def defender_tool():
    """MpCmdRun.exe: the platform copy hosted images keep under ProgramData
    (newest version first), else the one in Program Files."""
    platform = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" \
        / "Windows Defender" / "Platform"
    found = sorted(platform.glob("*/MpCmdRun.exe"), reverse=True)
    found.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender"
                 / "MpCmdRun.exe")
    return next((p for p in found if p.is_file()), None)


def defender_verdict(output: str):
    """(threats, why): from MpCmdRun's text, not its exit code, which is 2
    for a found threat and for a scan that could not run alike (QA B1).
    threats is None when the scan did not run or the output is unclear."""
    found = re.search(r"found (\d+) threats?", output, re.I)
    if found and int(found.group(1)) > 0:  # a threat is never hidden by an error line
        return int(found.group(1)), found.group(0)
    if re.search(r"hr\s*=\s*0x[0-9a-f]+|scan (?:failed|could not)|service", output, re.I):
        return None, "the scan did not run"
    if found or re.search(r"found no threats", output, re.I):
        return 0, "found no threats"
    return None, "no threat count in MpCmdRun's output"


def defender(check, path: Path) -> None:
    tool = defender_tool()
    if tool is None:
        print("NOT RUN: Defender scan (MpCmdRun.exe is not on this runner)")
        return
    done = subprocess.run([str(tool), "-Scan", "-ScanType", "3", "-File", str(path.resolve()),
                           "-DisableRemediation"], capture_output=True, text=True, timeout=900)
    output = done.stdout + done.stderr
    print(output[-2000:])
    threats, why = defender_verdict(output)
    if threats is None:
        print(f"NOT RUN: Defender scan ({why}; MpCmdRun exit {done.returncode})")
    else:
        check(threats == 0, "Defender scan found no threats", why)


def size_of(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size


def sizes_table(paths) -> str:
    """Markdown rows; a path an earlier failed step never made says so."""
    rows = [f"| {p.name}{'/' if p.is_dir() else ''} | "
            f"{f'{size_of(p) / 2**20:.1f} MB' if p.exists() else 'not built'} |" for p in paths]
    return "\n".join(["| Artifact | Size |", "|---|---|", *rows]) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("frozen")
    f.add_argument("onedir", type=Path)
    f.add_argument("wav", type=Path)
    f.add_argument("models_dir", type=Path)
    i = sub.add_parser("install")
    i.add_argument("setup", type=Path)
    g = sub.add_parser("upgrade")
    g.add_argument("setup", type=Path)
    u = sub.add_parser("uninstall")
    u.add_argument("--delete-user-data", action="store_true")
    d = sub.add_parser("defender")
    d.add_argument("path", type=Path)
    s = sub.add_parser("sizes")
    s.add_argument("paths", type=Path, nargs="+")
    args = p.parse_args(argv)
    check = Checks()
    if args.cmd == "frozen":
        check_frozen(check, args.onedir, args.wav.resolve(), args.models_dir.resolve())
    elif args.cmd == "install":
        check_install(check, args.setup.resolve())
    elif args.cmd == "upgrade":
        check_upgrade(check, args.setup.resolve())
    elif args.cmd == "uninstall":
        check_uninstall(check, args.delete_user_data)
    elif args.cmd == "defender":
        defender(check, args.path)
    else:
        table = sizes_table(args.paths)
        print(table)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as out:
                out.write("### Windows build sizes\n\n" + table)
    return 1 if check.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
