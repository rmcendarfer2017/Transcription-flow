"""Privacy environment and import hygiene (review M4, L5)."""

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import transcription_flow
from helpers import child_env
from transcription_flow import _env

SRC = Path(transcription_flow.__file__).resolve().parent
HEAVY = ["faster_whisper", "ctranslate2", "onnxruntime", "huggingface_hub", "sounddevice",
         "keyboard", "tkinter", "av"]


def run_py(code, extra_path=None, **env):
    if extra_path:
        env["PYTHONPATH"] = str(extra_path)
    full_env = child_env(drop=[*_env.PRIVACY_ENV, "PYTHONPATH"], **env)
    out = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True,
                         text=True, timeout=60, env=full_env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_importing_the_package_sets_the_env_and_loads_nothing_heavy():
    mods = sorted(p.relative_to(SRC.parent).with_suffix("").as_posix().replace("/", ".")
                  for p in SRC.rglob("*.py")
                  if p.name != "__main__.py" and "ui" not in p.parts)
    result = run_py(f"""
        import importlib, json, os, sys
        for m in {mods!r}:
            importlib.import_module(m)
        print(json.dumps({{"heavy": [h for h in {HEAVY!r} if h in sys.modules],
                           "env": os.environ.get("ORT_DISABLE_TELEMETRY")}}))
    """)
    assert result == {"heavy": [], "env": "1"}  # the package __init__ applies _env first


def test_engine_sets_privacy_env_before_importing_faster_whisper(tmp_path):
    fake = tmp_path / "faster_whisper"
    fake.mkdir()
    (fake / "__init__.py").write_text(textwrap.dedent("""
        import os
        SEEN = {k: os.environ.get(k) for k in
                ("ORT_DISABLE_TELEMETRY", "HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY",
                 "HF_HUB_DISABLE_PROGRESS_BARS")}
        class WhisperModel:
            def __init__(self, path, **kw):
                self.kw = kw
    """))
    result = run_py("""
        import json
        from transcription_flow.engine import WhisperEngine
        engine = WhisperEngine("somewhere").load()
        import faster_whisper
        print(json.dumps({"seen": faster_whisper.SEEN, "kw": engine._model.kw}))
    """, extra_path=tmp_path)
    assert result["seen"] == {k: "1" for k in result["seen"]}
    assert result["kw"] == {"device": "cpu", "compute_type": "int8", "local_files_only": True}


def test_env_overrides_an_inherited_value_before_main_runs():
    result = run_py("""
        import json, os
        from transcription_flow.main import main
        try:
            main(["--version"])
        except SystemExit:
            pass
        print(json.dumps({k: os.environ.get(k) for k in
                          ("ORT_DISABLE_TELEMETRY", "HF_HUB_OFFLINE")}))
    """, ORT_DISABLE_TELEMETRY="0")
    assert result == {"ORT_DISABLE_TELEMETRY": "1", "HF_HUB_OFFLINE": "1"}


def test_apply_overrides_existing_values():
    env = {"ORT_DISABLE_TELEMETRY": "0"}
    _env.apply(env)
    assert env["ORT_DISABLE_TELEMETRY"] == "1" and env["HF_HUB_OFFLINE"] == "1"


def _imports(path):
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_no_clipboard_or_hf_hub_imports_anywhere():
    banned = ("pyperclip", "win32clipboard", "huggingface_hub", "requests")
    found = {(p.name, m) for p in SRC.rglob("*.py") for m in _imports(p)
             if m.split(".")[0] in banned}
    assert found == set()
