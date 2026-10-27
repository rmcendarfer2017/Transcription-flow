"""QA M2: hand-edited config files must never stop the app (config.py docstring).
Each test is the only reason it fails; the controls show the neighbours pass."""

import json

import pytest

from transcription_flow import config


@pytest.mark.parametrize("key", ["hotkey", "output"])
@pytest.mark.parametrize("value", [["f9"], {"a": 1}])
def test_unhashable_value_falls_back_to_the_default(tmp_path, key, value):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"version": 1, key: value}))
    assert getattr(config.load(p), key) == getattr(config.Config(), key)


@pytest.mark.parametrize("key", ["hotkey", "output", "model"])
def test_control_scalar_junk_falls_back(tmp_path, key):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"version": 1, key: 5}))
    assert getattr(config.load(p), key) == getattr(config.Config(), key)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
def test_notepad_unicode_encoding_does_not_crash(tmp_path, encoding):
    """Windows Notepad's "Unicode" save is UTF-16 with a BOM."""
    p = tmp_path / "config.json"
    p.write_bytes(json.dumps({"version": 1, "hotkey": "f9"}).encode(encoding))
    config.load(p)  # any Config is fine; it must not raise


def test_control_utf8_with_bom_is_read(tmp_path):
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"version": 1, "hotkey": "f9"}).encode())
    assert config.load(p).hotkey == "f9"
