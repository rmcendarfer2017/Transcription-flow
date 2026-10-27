"""Settings file, paths and autostart (Run key)."""

import json
import logging
import os
import sys

import pytest

from transcription_flow import autostart, config, paths
from transcription_flow.config import Config


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "cfg" / "config.json"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


GOOD = {"version": 1, "hotkey": "f9", "model": "base.en", "language": "en",
        "output": "paste", "keep_mic_warm": True}


def test_missing_file_gives_defaults(cfg_path):
    c = config.load(cfg_path)
    assert c == Config()
    assert (c.hotkey, c.model, c.language, c.output, c.keep_mic_warm) == (
        "right_ctrl", "small.en", "en", "type", False)
    assert not cfg_path.exists()  # loading never writes


def test_save_then_load_round_trips(cfg_path):
    c = config.from_dict(GOOD)
    config.save(c, cfg_path)
    assert json.loads(cfg_path.read_text("utf-8")) == GOOD
    assert config.load(cfg_path) == c


def test_a_good_file_loads_without_warnings(cfg_path, caplog):  # the PASS control
    write(cfg_path, GOOD)
    with caplog.at_level(logging.WARNING):
        c = config.load(cfg_path)
    assert caplog.records == [] and c.hotkey == "f9" and c.output == "paste"


@pytest.mark.parametrize("key, bad", [
    ("hotkey", "pause"), ("hotkey", 5), ("model", "large-v3"), ("output", "clipboard"),
    ("keep_mic_warm", "yes"), ("keep_mic_warm", 1), ("language", 7),
])
def test_each_invalid_value_alone_falls_back_to_its_default(cfg_path, caplog, key, bad):
    write(cfg_path, {**GOOD, key: bad})
    with caplog.at_level(logging.WARNING):
        c = config.load(cfg_path)
    assert getattr(c, key) == getattr(Config(), key)
    others = {k: v for k, v in GOOD.items() if k not in (key, "version")}
    assert all(getattr(c, k) == v for k, v in others.items())  # the rest survives
    assert any(f"invalid {key}" in r.getMessage() for r in caplog.records)


def test_language_that_does_not_suit_the_model_is_reset(cfg_path, caplog):
    write(cfg_path, {**GOOD, "language": "de"})  # base.en knows only English
    with caplog.at_level(logging.WARNING):
        assert config.load(cfg_path).language == "en"
    assert any("does not suit" in r.getMessage() for r in caplog.records)


def test_languages_for_models():
    assert config.languages_for("small.en") == {"en": "English"}
    multi = config.languages_for("small")
    assert next(iter(multi)) == "auto" and "de" in multi


def test_unknown_setting_is_ignored_with_a_log_line(cfg_path, caplog):
    write(cfg_path, {**GOOD, "theme": "dark"})
    with caplog.at_level(logging.WARNING):
        c = config.load(cfg_path)
    assert c == config.from_dict(GOOD)
    assert any("unknown setting 'theme'" in r.getMessage() for r in caplog.records)


def test_other_version_keeps_the_settings_it_shares(cfg_path, caplog):
    write(cfg_path, {**GOOD, "version": 2})
    with caplog.at_level(logging.WARNING):
        c = config.load(cfg_path)
    assert c.hotkey == "f9" and any("version 2" in r.getMessage() for r in caplog.records)


def test_a_file_saved_with_a_bom_by_notepad_still_loads(cfg_path):
    write(cfg_path, "\ufeff" + json.dumps(GOOD))
    assert config.load(cfg_path) == config.from_dict(GOOD)


@pytest.mark.parametrize("text", ["{not json", "", '{"hotkey": "f9",}'])
def test_broken_json_is_kept_aside_and_defaults_used(cfg_path, text):
    write(cfg_path, text)
    assert config.load(cfg_path) == Config()
    assert not cfg_path.exists()
    assert cfg_path.with_name("config.json.bad").read_text("utf-8") == text


@pytest.mark.parametrize("data", [[1, 2], "just a string", None, 3])
def test_json_that_is_not_an_object_gives_defaults(cfg_path, data):
    write(cfg_path, json.dumps(data))
    assert config.load(cfg_path) == Config()


def test_a_crash_while_saving_keeps_the_old_file(cfg_path, monkeypatch):
    config.save(config.from_dict(GOOD), cfg_path)
    before = cfg_path.read_bytes()

    def crash(src, dst):
        raise OSError("power cut")

    monkeypatch.setattr(config.os, "replace", crash)
    with pytest.raises(OSError):
        config.save(Config(), cfg_path)
    assert cfg_path.read_bytes() == before
    assert os.listdir(cfg_path.parent) == ["config.json"]  # no temp file left


def test_unreadable_file_gives_defaults(cfg_path):
    cfg_path.mkdir(parents=True)  # a folder where the file should be
    assert config.load(cfg_path) == Config()


def test_config_file_location(monkeypatch, tmp_path):
    assert paths.config_file() == paths.data_dir() / "config.json"  # TFLOW_HOME: one folder
    monkeypatch.delenv("TFLOW_HOME")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert paths.config_file() == tmp_path / "Roaming" / "TranscriptionFlow" / "config.json"
    assert paths.data_dir() == tmp_path / "Local" / "TranscriptionFlow"


# -- autostart ------------------------------------------------------------------

class FakeKey:
    def __init__(self, reg, path):
        self.reg, self.path = reg, path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_QUERY_VALUE, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self):
        self.keys = {}

    def OpenKey(self, root, path, reserved, access):  # noqa: N802
        if (root, path) not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(self, (root, path))

    def CreateKeyEx(self, root, path, reserved, access):  # noqa: N802
        self.keys.setdefault((root, path), {})
        return FakeKey(self, (root, path))

    def QueryValueEx(self, key, name):  # noqa: N802
        values = self.keys[key.path]
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        assert kind == self.REG_SZ
        self.keys[key.path][name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.keys[key.path]:
            raise FileNotFoundError(name)
        del self.keys[key.path][name]


def test_run_key_enable_disable_round_trip():
    reg = FakeWinreg()
    run = autostart.RunKey(winreg_module=reg)
    assert run.available and not run.is_enabled()
    run.disable()  # nothing to delete: fine
    run.enable('"C:\\Apps\\TF\\TranscriptionFlow.exe" --tray')
    assert reg.keys[("HKCU", autostart.RUN_KEY)] == {
        "TranscriptionFlow": '"C:\\Apps\\TF\\TranscriptionFlow.exe" --tray'}
    assert run.is_enabled()
    run.disable()
    assert not run.is_enabled() and reg.keys[("HKCU", autostart.RUN_KEY)] == {}


def test_run_key_is_unavailable_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    run = autostart.RunKey()
    assert not run.available and not run.is_enabled()


def test_launch_command_for_the_packaged_exe():
    cmd = autostart.launch_command("C:\\Program Files\\TF\\TranscriptionFlow.exe", frozen=True)
    assert cmd == '"C:\\Program Files\\TF\\TranscriptionFlow.exe" --tray'


def test_launch_command_from_a_venv_uses_pythonw(tmp_path):
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    cmd = autostart.launch_command(str(tmp_path / "python.exe"), frozen=False,
                                   shim=lambda: None)
    assert cmd == f'"{tmp_path / "pythonw.exe"}" -P -m transcription_flow --tray'


def test_launch_command_without_pythonw_keeps_the_interpreter(tmp_path):
    cmd = autostart.launch_command(str(tmp_path / "python3"), frozen=False, shim=lambda: None)
    assert cmd == f'"{tmp_path / "python3"}" -P -m transcription_flow --tray'


def test_launch_command_from_a_source_checkout_runs_the_shim(tmp_path):
    shim = autostart._checkout_shim()
    assert shim is not None and shim.name == "dictation.py"  # this repo is a checkout
    cmd = autostart.launch_command(str(tmp_path / "python3"), frozen=False)
    assert cmd == f'"{tmp_path / "python3"}" -P "{shim}" --tray'


@pytest.mark.skipif(sys.platform != "win32", reason="real registry")
def test_real_registry_round_trip_on_a_test_key():
    import winreg

    path = rf"Software\TranscriptionFlowTest\Run{os.getpid()}"
    run = autostart.RunKey(key_path=path)
    try:
        run.enable('"x.exe" --tray')
        assert run.command() == '"x.exe" --tray'
        run.disable()
        assert not run.is_enabled()
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass
