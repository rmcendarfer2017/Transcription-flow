import json
import subprocess
import sys

import pytest

from transcription_flow import _env, gpu_probe
from transcription_flow.gpu_probe import ProbeResult, choose_device, probe


def py(code):
    return [sys.executable, "-c", code]


def test_ok_report_passes():
    assert probe(py('print(\'{"ok": true, "reason": "fine"}\')')) == ProbeResult(True, "fine")


def test_ok_is_the_last_line_after_library_noise():
    code = 'print("cuda init chatter"); print(\'{"ok": true}\')'
    assert probe(py(code)).ok


def test_native_abort_falls_back():
    result = probe(py("import os; os.abort()"))
    assert not result.ok and "exited with code" in result.reason


def test_nonzero_exit_falls_back_even_with_ok_output():
    result = probe(py('import sys; print(\'{"ok": true}\'); sys.exit(1)'))
    assert not result.ok


def test_timeout_falls_back():
    result = probe(py("import time; time.sleep(30)"), timeout=0.5)
    assert not result.ok and "timed out" in result.reason


@pytest.mark.parametrize("out", ["", "not json", '{"reason": "x"}', '{"ok": "yes"}', "[1]"])
def test_unreadable_output_falls_back(out):
    assert not probe(py(f"print({out!r})")).ok


def test_missing_executable_falls_back(tmp_path):
    assert not probe([str(tmp_path / "missing.exe")]).ok


def test_child_importing_the_package_gets_the_privacy_env(monkeypatch):
    for name in _env.PRIVACY_ENV:
        monkeypatch.delenv(name, raising=False)
    code = ("import os, json, transcription_flow; "
            "print(json.dumps({'ok': os.environ.get('ORT_DISABLE_TELEMETRY') == '1'}))")
    assert probe(py(code)).ok


def test_child_command_runs_the_package(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = gpu_probe.child_command("models/x")
    assert cmd[:4] == [sys.executable, "-P", "-m", "transcription_flow"]  # -P: no cwd on path
    assert cmd[4:] == ["--probe-gpu", "--model-dir", "models/x"]


def test_child_command_frozen_runs_the_exe(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert gpu_probe.child_command("m") == [sys.executable, "--probe-gpu", "--model-dir", "m"]


def test_cpu_never_probes(tmp_path):
    def boom(cmd):
        raise AssertionError("probe ran")

    assert choose_device("cpu", tmp_path / "small.en", tmp_path / "c.json", run_probe=boom) == "cpu"
    assert not (tmp_path / "c.json").exists()


def test_auto_uses_gpu_when_probe_passes_and_caches(tmp_path):
    calls = []
    run = lambda cmd: calls.append(cmd) or ProbeResult(True, "ok")  # noqa: E731
    cache = tmp_path / "c.json"
    assert choose_device("auto", tmp_path / "small.en", cache, run_probe=run) == "cuda"
    assert choose_device("auto", tmp_path / "small.en", cache, run_probe=run) == "cuda"
    assert len(calls) == 1
    assert json.loads(cache.read_text())["ok"] is True


def test_auto_cached_failure_stays_cpu(tmp_path):
    cache = tmp_path / "c.json"
    choose_device("auto", tmp_path / "m", cache, run_probe=lambda c: ProbeResult(False, "x"))
    assert choose_device("auto", tmp_path / "m", cache,
                         run_probe=lambda c: ProbeResult(True, "")) == "cpu"


def test_cache_for_another_model_is_ignored(tmp_path):
    cache = tmp_path / "c.json"
    choose_device("auto", tmp_path / "a", cache, run_probe=lambda c: ProbeResult(False, "x"))
    assert choose_device("auto", tmp_path / "b", cache,
                         run_probe=lambda c: ProbeResult(True, "")) == "cuda"


def test_probe_exception_and_corrupt_cache_fail_safe(tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text("{not json")

    def boom(cmd):
        raise RuntimeError("bad")

    assert choose_device("auto", tmp_path / "m", cache, run_probe=boom) == "cpu"


def test_unwritable_cache_still_returns(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert choose_device("auto", tmp_path / "m", blocker / "c.json",
                         run_probe=lambda c: ProbeResult(True, "")) == "cuda"


@pytest.mark.slow
def test_real_probe_child_answers_cleanly(tmp_path):
    """The real child (ctranslate2 import, CUDA device count) must not crash.
    CI runners and the dev box have no NVIDIA GPU, so the answer is 'no CUDA device'."""
    result = probe(gpu_probe.child_command(tmp_path / "no-model"), timeout=120)
    assert result.ok or result.reason == "no CUDA device", result


@pytest.mark.slow
def test_probe_child_ignores_modules_in_the_working_directory(tmp_path):
    """-P: a stray json.py in the folder the app was started from must not be imported."""
    (tmp_path / "json.py").write_text("import sys\nprint('SHADOWED')\nsys.exit(99)\n")
    done = subprocess.run(gpu_probe.child_command(tmp_path / "no-model"), cwd=tmp_path,
                          capture_output=True, text=True, timeout=120,
                          env=gpu_probe._child_env(), stdin=subprocess.DEVNULL)
    assert "SHADOWED" not in done.stdout and done.returncode != 99, done.stderr[-2000:]


def test_the_probe_child_counts_a_dropped_stock_phrase_as_a_working_gpu(monkeypatch, capsys):
    """Whisper may "hear" a "you" in the probe's silence; the filter then
    drops it with ShortPhraseDropped, and CUDA still worked."""
    import ctranslate2

    from transcription_flow import engine
    from transcription_flow.hallucination import ShortPhraseDropped

    class Hears:
        def __init__(self, model_dir, device):
            assert device == "cuda"

        def load(self):
            return self

        def transcribe(self, audio):
            raise ShortPhraseDropped()

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    monkeypatch.setattr(engine, "WhisperEngine", Hears)
    assert gpu_probe.run_child("m") == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
