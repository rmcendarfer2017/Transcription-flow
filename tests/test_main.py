import json
import os
import re
import struct
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

import transcription_flow
from conftest import FIXTURES
from helpers import child_env, tone
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
        elif width == 3:  # the low 3 bytes of each little-endian int32
            wide = (samples * (2 ** 23 - 1)).astype("<i4")
            data = wide.view(np.uint8).reshape(-1, 4)[:, :3]
        else:
            data = (samples * (2 ** (8 * width - 1) - 1)).astype(f"<i{width}")
        w.writeframes(data.tobytes())


def test_read_fixture_wav():
    audio = read_wav(HELLO)
    assert audio.dtype == np.float32 and 1.5 < len(audio) / 16000 < 3.5
    assert 0.5 < np.abs(audio).max() <= 1.0


def test_read_stereo_44k_wav(tmp_path):
    stereo = np.repeat(tone(1.0, rate=44100, amp=0.5), 2)
    write_wav(tmp_path / "s.wav", stereo, 44100, channels=2)
    audio = read_wav(tmp_path / "s.wav")
    assert len(audio) == 16000 and abs(np.abs(audio).max() - 0.5) < 0.01


def test_read_8bit_wav(tmp_path):
    write_wav(tmp_path / "8.wav", np.full(16000, 0.5), 16000, width=1)
    assert abs(read_wav(tmp_path / "8.wav").mean() - 0.5) < 0.02


def write_raw_wav(path, tag, channels, rate, width, data, extensible=False):
    """A WAV file built by hand, for formats the wave module can't write."""
    block = channels * width
    fmt = struct.pack("<HHIIHH", 0xFFFE if extensible else tag, channels, rate, rate * block,
                      block, 8 * width)
    if extensible:
        fmt += struct.pack("<HHI", 22, 8 * width, 0) + struct.pack("<H", tag) + bytes(14)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_read_24bit_wav(tmp_path):
    write_wav(tmp_path / "24.wav", tone(1.0, amp=0.5), 16000, width=3)
    audio = read_wav(tmp_path / "24.wav")
    assert len(audio) == 16000
    assert np.allclose(audio, tone(1.0, amp=0.5), atol=1e-4)


def test_read_float32_wav(tmp_path):
    stereo = np.repeat(tone(1.0, amp=0.25), 2).astype("<f4")
    write_raw_wav(tmp_path / "f.wav", 3, 2, 16000, 4, stereo.tobytes())
    assert np.allclose(read_wav(tmp_path / "f.wav"), tone(1.0, amp=0.25), atol=1e-6)


def test_read_extensible_float_wav(tmp_path):
    write_raw_wav(tmp_path / "x.wav", 3, 1, 16000, 4, tone(0.5).astype("<f4").tobytes(),
                  extensible=True)
    assert np.allclose(read_wav(tmp_path / "x.wav"), tone(0.5), atol=1e-6)


def test_read_truncated_wav_keeps_what_is_there(tmp_path):
    write_wav(tmp_path / "t.wav", tone(1.0), 16000)
    raw = (tmp_path / "t.wav").read_bytes()
    (tmp_path / "t.wav").write_bytes(raw[:44 + 16001])  # half a sample at the end
    assert len(read_wav(tmp_path / "t.wav")) == 8000


def test_read_unsupported_format(tmp_path):
    write_raw_wav(tmp_path / "adpcm.wav", 2, 1, 16000, 2, b"\0" * 300)
    with pytest.raises(ValueError, match="unsupported WAV format"):
        read_wav(tmp_path / "adpcm.wav")


@pytest.mark.parametrize("rate", [0, 1, 999, 768001, 0xFFFFFFFF])
def test_read_rejects_sample_rates_out_of_range(tmp_path, rate):
    write_raw_wav(tmp_path / "r.wav", 1, 1, rate, 1, b"\x80" * 32000)  # 8-bit: byte rate fits
    with pytest.raises(ValueError, match="sample rate"):
        read_wav(tmp_path / "r.wav")


@pytest.mark.parametrize("rate", [1000, 768000])
def test_read_accepts_the_edge_sample_rates(tmp_path, rate):
    write_raw_wav(tmp_path / "r.wav", 1, 1, rate, 2, b"\0" * 2000)
    assert len(read_wav(tmp_path / "r.wav")) == round(1000 * 16000 / rate)


def test_read_block_align_zero_is_a_broken_format(tmp_path):
    write_raw_wav(tmp_path / "z.wav", 1, 1, 16000, 0, b"\0" * 100)
    with pytest.raises(ValueError, match="broken format chunk"):
        read_wav(tmp_path / "z.wav")


def test_read_not_a_wav(tmp_path):
    (tmp_path / "n.wav").write_bytes(b"ID3 not audio at all")
    with pytest.raises(ValueError, match="not a WAV"):
        read_wav(tmp_path / "n.wav")


class FakeEngine:
    text = ""

    def __init__(self, model_dir, device):
        pass

    def load(self):
        return self

    def transcribe(self, audio):
        return self.text


@pytest.mark.parametrize("text, expect, ok, reason", [
    ("", None, False, "no speech detected"),
    ("Hello there.", "goodbye", False, "the expected text was not found"),
    ("Hello there.", "hello", True, None),
])
def test_selftest_says_why_it_failed(tmp_path, monkeypatch, text, expect, ok, reason):
    from transcription_flow import selftest

    monkeypatch.setattr(FakeEngine, "text", text)
    monkeypatch.setattr(selftest, "WhisperEngine", FakeEngine)
    report = selftest.run(HELLO, tmp_path, expect=expect)
    assert report["ok"] is ok and report.get("reason") == reason and "error" not in report


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


def test_download_failure_is_also_in_the_log(monkeypatch, capsys):
    def fail(*a, **k):
        raise models.DownloadError("The download failed (HTTP 503).")

    monkeypatch.setattr(models, "download", fail)
    app.main(["--download-model", "--model", "tiny.en"])
    assert "HTTP 503" in (paths.logs_dir() / "app.log").read_text("utf-8")  # review F9


def test_error_after_a_progress_line_starts_on_a_new_line(monkeypatch, capsys):
    def half_then_fail(name, models_dir, progress, verify):
        progress(50 << 20, 100 << 20)
        raise models.DownloadError("The download was interrupted.")

    monkeypatch.setattr(models, "download", half_then_fail)
    assert app.main(["--download-model", "--model", "tiny.en"]) == 2
    err = capsys.readouterr().err
    assert "50% (50 of 100 MB)\n[!] Model download failed: The download was interrupted." in err


def test_ctrl_c_during_a_download_gives_one_line_and_exit_130(monkeypatch, capsys):
    def interrupted(name, models_dir, progress, verify):
        progress(10 << 20, 100 << 20)
        raise KeyboardInterrupt

    monkeypatch.setattr(models, "download", interrupted)
    assert app.main(["--download-model", "--model", "tiny.en"]) == 130
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert err.endswith("MB)\n[!] Cancelled. A model download that was cut short resumes next time.\n")


def test_ctrl_c_elsewhere_exits_130_without_a_traceback(monkeypatch, capsys):
    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "_selftest", interrupted)
    assert app.main(["--selftest", str(HELLO)]) == 130
    err = capsys.readouterr().err
    assert "Traceback" not in err and "Cancelled." in err


def test_unknown_model_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        app.main(["--model", "small"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err  # review F2


def test_verify_flag_rehashes(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(models, "download",
                        lambda name, models_dir, progress, verify: calls.append(verify) or tmp_path)
    assert app.main(["--verify", "--model", "tiny.en"]) == 0
    assert app.main(["--download-model", "--model", "tiny.en"]) == 0
    assert calls == [True, False]


def test_second_copy_exits_3(monkeypatch):
    held = single_instance.acquire(paths.data_dir())
    monkeypatch.setattr(models, "download", lambda *a, **k: pytest.fail("got past the guard"))
    try:
        assert app.main([]) == 3
    finally:
        held.release()


def test_second_copy_takes_the_guard_before_opening_the_log(monkeypatch, capsys):
    held = single_instance.acquire(paths.data_dir())
    try:
        assert app.main(["--download-model", "--model", "tiny.en"]) == 3
    finally:
        held.release()
    assert not paths.logs_dir().exists()  # review-m1: no second writer of app.log
    assert "already running" in capsys.readouterr().err


def test_download_model_defaults_to_the_model_in_settings(monkeypatch, capsys, tmp_path):
    from transcription_flow import config

    config.save(config.Config(model="base.en"), paths.config_file())
    names = []
    monkeypatch.setattr(models, "download",
                        lambda name, models_dir, progress, verify: names.append(name) or tmp_path)
    assert app.main(["--download-model"]) == 0
    assert app.main(["--download-model", "--model", "tiny.en"]) == 0  # the flag wins
    assert names == ["base.en", "tiny.en"]


def test_reset_config_writes_the_defaults(capsys):
    from transcription_flow import config

    config.save(config.Config(hotkey="f9", output="paste"), paths.config_file())
    assert app.main(["--reset-config"]) == 0
    assert config.load(paths.config_file()) == config.Config()
    assert "Settings reset" in capsys.readouterr().out


def test_tray_flag_is_accepted():
    assert app._parser().parse_args(["--tray"]).tray is True


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
        pytest.skip("set TFLOW_TEST_MODELS_DIR to a folder with tiny.en installed (see README)")
    out = subprocess.run(
        [sys.executable, "-c", AUDIT, "--selftest", str(HELLO), "--expect", "hello",
         "--model-dir", str(Path(models_dir) / "tiny.en")],
        capture_output=True, text=True, timeout=300, env=child_env())
    assert "NETWORK" not in out.stderr, out.stderr
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert out.returncode == 0 and report["ok"], (report, out.stderr[-2000:])
    assert "hello" in report["text"].lower()
