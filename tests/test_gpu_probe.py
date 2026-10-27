import json
import sys

import pytest

from transcription_flow import gpu_probe
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


def test_child_gets_the_privacy_env():
    code = "import os, json; print(json.dumps({'ok': os.environ.get('ORT_DISABLE_TELEMETRY') == '1'}))"
    assert probe(py(code)).ok


def test_child_command_runs_the_package(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = gpu_probe.child_command("models/x")
    assert cmd[:3] == [sys.executable, "-m", "transcription_flow"]
    assert cmd[3:] == ["--probe-gpu", "--model-dir", "models/x"]


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
