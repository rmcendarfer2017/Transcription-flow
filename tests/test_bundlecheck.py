"""--check-bundle (review-m4 F1): the frozen exe imports PortAudio, the pynput
and pystray Windows backends, Tk and every app module. Run here against
stand-in packages in a child process; the real run is in the windows-build
CI job (packaging/ci_smoke.py frozen)."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import transcription_flow
from helpers import child_env
from transcription_flow import bundlecheck
from transcription_flow import main as app_main

SRC = Path(transcription_flow.__file__).resolve().parent

FAKES = {
    "sounddevice.py": """
        import os
        class PortAudioError(Exception):
            pass
        def query_devices():
            if os.environ.get("FAKE_NO_DEVICE"):
                raise PortAudioError("Error querying device -1")
            return ["mic", "speakers"]
    """,
    "pynput/__init__.py": "",
    "pynput/keyboard/__init__.py": "class Controller: pass\n",
    "pynput/keyboard/_win32.py": "",
    "pystray/__init__.py": "class Icon: pass\n",
    "pystray/_win32.py": "",
    "tkinter/__init__.py": """
        TkVersion = 8.6
        class Tk:
            def withdraw(self): pass
            def call(self, *args): return "8.6.15"
            def destroy(self): print("TK-DESTROYED")
    """,
}


def fake_site(root: Path, leave_out=()) -> Path:
    for rel, body in FAKES.items():
        if rel in leave_out:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), "utf-8")
    return root


def run_check(site: Path, platform="win32", **env):
    """bundlecheck.run(platform) in a child with the stand-ins first on the path."""
    code = f"from transcription_flow import bundlecheck; raise SystemExit(bundlecheck.main({platform!r}))"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120, env=child_env(PYTHONPATH=str(site), **env))


def report(done):
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_every_app_module_is_on_the_list():
    found = {p.relative_to(SRC).with_suffix("").as_posix().replace("/", ".")
             .removesuffix(".__init__") for p in SRC.rglob("*.py")}
    assert set(bundlecheck.APP_MODULES) == found - {"__init__", "__main__"}


def test_a_complete_bundle_passes_and_closes_its_tk_root(tmp_path):
    done = run_check(fake_site(tmp_path))
    assert done.returncode == 0, done.stderr
    result = report(done)
    assert result["ok"] is True
    assert result["checks"]["sounddevice"] == "PortAudio loaded; 2 devices"
    assert result["checks"]["backends"] == "pynput.keyboard._win32, pystray._win32"
    assert result["checks"]["tkinter"] == "Tk 8.6.15"
    assert result["checks"]["app modules"] == f"{len(bundlecheck.APP_MODULES)} modules"
    assert "TK-DESTROYED" in done.stdout


def test_no_audio_device_is_fine(tmp_path):
    done = run_check(fake_site(tmp_path), FAKE_NO_DEVICE="1")
    assert done.returncode == 0, done.stderr
    assert report(done)["checks"]["sounddevice"].startswith("PortAudio loaded; no devices")


@pytest.mark.parametrize("leave_out, failed", [
    ("pynput/keyboard/_win32.py", {"backends"}),
    ("pystray/_win32.py", {"backends"}),
    ("tkinter/__init__.py", {"tkinter", "app modules"}),  # ui/overlay.py imports it too
])
def test_a_missing_part_fails_and_is_named(tmp_path, leave_out, failed):
    done = run_check(fake_site(tmp_path, leave_out=[leave_out]))
    assert done.returncode == 1
    result = report(done)
    assert result["ok"] is False
    bad = {name for name, text in result["checks"].items() if text.startswith("FAIL ")}
    assert bad == failed, result
    assert all("ModuleNotFoundError" in result["checks"][name] for name in failed)


def test_a_portaudio_dll_that_does_not_load_fails(tmp_path):
    site = fake_site(tmp_path)
    (site / "sounddevice.py").write_text("raise OSError('PortAudio library not found')\n")
    done = run_check(site)
    assert done.returncode == 1
    assert report(done)["checks"]["sounddevice"] == "FAIL OSError: PortAudio library not found"


def test_a_broken_app_module_fails(monkeypatch):
    monkeypatch.setattr(bundlecheck, "APP_MODULES", ("config", "no_such_module"))
    monkeypatch.setattr(bundlecheck, "_sounddevice", lambda: "ok")
    monkeypatch.setattr(bundlecheck, "_backends", lambda platform: "ok")
    monkeypatch.setattr(bundlecheck, "_tk", lambda: "ok")
    result = bundlecheck.run()
    assert result["ok"] is False
    assert result["checks"]["app modules"].startswith("FAIL ModuleNotFoundError")
    assert "no_such_module" in result["checks"]["app modules"]


def test_the_hidden_flag_runs_it_and_prints_the_report_last(tmp_path):
    """main --check-bundle: no guard, no log, no settings; JSON on the last line."""
    home = tmp_path / "home"
    done = subprocess.run([sys.executable, "-m", "transcription_flow", "--check-bundle"],
                          capture_output=True, text=True, timeout=120,
                          env=child_env(PYTHONPATH=str(fake_site(tmp_path / "site")),
                                        TFLOW_HOME=str(home)))
    assert done.returncode == 0, done.stderr
    result = report(done)
    assert result["ok"] is True and result["checks"]["backends"].startswith(
        "no fixed backend on ")
    assert not home.exists()  # it never took the single-instance guard or wrote a log
    assert "--check-bundle" not in app_main._parser().format_help()


def test_the_build_bundles_and_checks_the_same_backends():
    sys.path.insert(0, str(SRC.parent.parent / "packaging"))
    try:
        import buildinfo
    finally:
        sys.path.pop(0)
    assert buildinfo.WIN32_BACKENDS == bundlecheck.BACKENDS["win32"]
