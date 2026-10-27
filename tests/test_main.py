import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

import transcription_flow
from conftest import FIXTURES
from transcription_flow import main as app
from transcription_flow import models, paths, single_instance
from transcription_flow.selftest import read_wav

HELLO = FIXTURES / "hello.wav"


def write_wav(path, samples, rate, width=2, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 1:
            data = (samples * 127 + 128).astype(np.uint8)
        else:
            data = (samples * (2 ** (8 * width - 1) - 1)).astype(f"<i{width}")
        w.writeframes(data.tobytes())


def test_read_fixture_wav():
    audio = read_wav(HELLO)
    assert audio.dtype == np.float32 and 1.5 < len(audio) / 16000 < 3.5
    assert 0.5 < np.abs(audio).max() <= 1.0


def test_read_stereo_44k_wav(tmp_path):
    t = np.arange(44100) / 44100
    stereo = np.stack([np.sin(2 * np.pi * 220 * t)] * 2, axis=1).ravel() * 0.5
    write_wav(tmp_path / "s.wav", stereo, 44100, channels=2)
    audio = read_wav(tmp_path / "s.wav")
    assert len(audio) == 16000 and abs(np.abs(audio).max() - 0.5) < 0.01


def test_read_8bit_wav(tmp_path):
    write_wav(tmp_path / "8.wav", np.full(16000, 0.5), 16000, width=1)
    assert abs(read_wav(tmp_path / "8.wav").mean() - 0.5) < 0.02


def test_read_unsupported_width(tmp_path):
    with wave.open(str(tmp_path / "24.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(16000)
        w.writeframes(b"\0" * 300)
    with pytest.raises(ValueError, match="sample width"):
        read_wav(tmp_path / "24.wav")


def test_selftest_without_model_reports_json_and_fails(tmp_path, capsys):
    code = app.main(["--selftest", str(HELLO), "--model-dir", str(tmp_path / "none")])
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["ok"] is False and "not found" in report["error"]


def test_selftest_load_error_is_reported_not_raised(tmp_path, capsys):
    (tmp_path / "broken").mkdir()
    code = app.main(["--selftest", str(tmp_path / "missing.wav"),
                     "--model-dir", str(tmp_path / "broken")])
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["ok"] is False and report["error"]


def test_download_failure_exits_2_with_a_message(monkeypatch, capsys):
    def fail(*a, **k):
        raise models.DownloadError("The download failed (HTTP 503).")

    monkeypatch.setattr(models, "download", fail)
    assert app.main(["--download-model", "--model", "tiny.en"]) == 2
    assert "HTTP 503" in capsys.readouterr().err


def test_second_copy_exits_3(monkeypatch):
    held = single_instance.acquire(paths.data_dir())
    monkeypatch.setattr(models, "download", lambda *a, **k: pytest.fail("got past the guard"))
    try:
        assert app.main([]) == 3
    finally:
        held.release()


def test_first_run_download_failure_exits_2(monkeypatch, capsys):
    def fail(*a, **k):
        raise models.DownloadError("The download failed: offline.")

    monkeypatch.setattr(models, "download", fail)
    assert app.main(["--model", "tiny.en"]) == 2
    assert "offline" in capsys.readouterr().err
    single_instance.acquire(paths.data_dir()).release()  # the guard was released


def test_dictation_shim_runs_main():
    root = Path(transcription_flow.__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, str(root / "dictation.py"), "--version"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and transcription_flow.__version__ in out.stdout


AUDIT = """
import runpy, sys
def hook(event, args):
    if event.startswith("socket.") and event not in ("socket.__new__",):
        sys.stderr.write("NETWORK " + event + "\\n")
        raise SystemExit(99)
sys.addaudithook(hook)
sys.argv = ["transcription_flow"] + sys.argv[1:]
runpy.run_module("transcription_flow", run_name="__main__")
"""


@pytest.mark.slow
def test_real_selftest_with_tiny_en_and_no_network():
    models_dir = os.environ.get("TFLOW_TEST_MODELS_DIR")
    if not models_dir or not models.is_installed("tiny.en", Path(models_dir)):
        pytest.skip("set TFLOW_TEST_MODELS_DIR to a folder with tiny.en installed "
                    "(python -m transcription_flow --download-model --model tiny.en --models-dir DIR)")
    env = dict(os.environ, PYTHONPATH=str(Path(transcription_flow.__file__).parents[1]))
    out = subprocess.run(
        [sys.executable, "-c", AUDIT, "--selftest", str(HELLO), "--expect", "hello",
         "--model-dir", str(Path(models_dir) / "tiny.en")],
        capture_output=True, text=True, timeout=300, env=env)
    assert "NETWORK" not in out.stderr, out.stderr
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert out.returncode == 0 and report["ok"], (report, out.stderr[-2000:])
    assert "hello" in report["text"].lower()
