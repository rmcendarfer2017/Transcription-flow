"""Smoke checks the Windows CI job runs on the built app (see .github/workflows/ci.yml).

    python packaging/ci_smoke.py frozen DIR WAV MODELS_DIR   selftest + GPU probe of DIR's exe
    python packaging/ci_smoke.py install SETUP_EXE            silent install, check what it wrote
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
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buildinfo  # noqa: E402

APP_NAME = buildinfo.APP_NAME
EXE = f"{buildinfo.EXE_NAME}.exe"
APP_ID = "{C41FBC91-05D3-4BBB-ADAB-FBC5C12CBECD}"  # installer.iss AppId
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TranscriptionFlow"
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


def check_install(check, setup: Path) -> None:
    code = subprocess.run([str(setup), "/VERYSILENT", "/CURRENTUSER", "/SUPPRESSMSGBOXES",
                           "/NORESTART", f"/LOG={Path(os.environ.get('RUNNER_TEMP', setup.parent)) / 'install.log'}"],
                          timeout=TIMEOUT).returncode
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


def wait_gone(path: Path, seconds: float = 120) -> bool:
    """The uninstaller hands over to a copy of itself in %TEMP% and its
    first process can exit before the files are gone."""
    deadline = time.monotonic() + seconds
    while path.exists() and time.monotonic() < deadline:
        time.sleep(1)
    return not path.exists()


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
    check(wait_gone_key(UNINSTALL_KEY), "uninstall key removed")
    for folder in data_dirs():
        if delete_user_data:
            check(wait_gone(folder, 30), f"/DELETEUSERDATA removed {folder.name} data", str(folder))
        else:
            check(folder.exists(), "a plain silent uninstall keeps settings and models",
                  str(folder))


def wait_gone_key(key: str, seconds: float = 30) -> bool:
    deadline = time.monotonic() + seconds
    while reg_key_exists(key) and time.monotonic() < deadline:
        time.sleep(1)
    return not reg_key_exists(key)


def defender(check, path: Path) -> None:
    tool = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" \
        / "MpCmdRun.exe"
    if not tool.is_file():
        print("NOT RUN: Defender scan (MpCmdRun.exe is not on this runner)")
        return
    done = subprocess.run([str(tool), "-Scan", "-ScanType", "3", "-File", str(path),
                           "-DisableRemediation"], capture_output=True, text=True, timeout=900)
    if done.returncode == 2:
        check(False, "Defender scan", done.stdout[-2000:])
    elif done.returncode == 0:
        check(True, "Defender scan found nothing")
    else:  # service off or not usable on the runner
        print(f"NOT RUN: Defender scan (MpCmdRun exit {done.returncode})")


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
