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
        "output": "paste", "keep_mic_warm": True, "mic": "Headset mic",
        "paste_settle_seconds": 1.5}


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
    # review-m2 F1: a list or object where a name belongs (unhashable)
    ("hotkey", ["f9"]), ("hotkey", {"f9": 1}), ("output", ["paste"]), ("output", {"a": 1}),
    ("model", ["base.en"]), ("model", {"base.en": 1}), ("language", ["en"]),
    ("hotkey", "right_alt"), ("hotkey", "right_shift"),  # no longer offered (F4)
    ("paste_settle_seconds", 0.05), ("paste_settle_seconds", 6), ("paste_settle_seconds", True),
    ("paste_settle_seconds", "1"), ("paste_settle_seconds", [1]),
    ("mic", None), ("mic", 3), ("mic", ["Headset mic"]), ("mic", "m" * 257),
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


@pytest.mark.parametrize("name, raw", [
    ("notepad unicode (utf-16 with a bom)", json.dumps(GOOD).encode("utf-16")),
    ("utf-16-le without a bom", json.dumps(GOOD).encode("utf-16-le")),
    ("ansi with an accent", b'{"version": 1, "language": "fran\xe7ais"}'),
    ("nested too deeply", b"[" * 100_000),
])
def test_a_file_that_is_not_utf8_json_is_kept_aside(cfg_path, caplog, name, raw):
    """review-m2 F1 / QA B2, B6: each of these used to crash the start."""
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_bytes(raw)
    with caplog.at_level(logging.WARNING):
        assert config.load(cfg_path) == Config()
    assert not cfg_path.exists()
    assert cfg_path.with_name("config.json.bad").read_bytes() == raw
    assert any("kept as config.json.bad" in r.getMessage() for r in caplog.records)


def test_a_bad_file_that_cannot_be_renamed_is_not_called_kept(cfg_path, caplog, monkeypatch):
    """QA B6: the log line must say what really happened."""
    write(cfg_path, "{not json")

    def refuse(src, dst):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(config.os, "replace", refuse)
    with caplog.at_level(logging.WARNING):
        assert config.load(cfg_path) == Config()
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "could not be renamed to config.json.bad (PermissionError)" in messages
    assert "kept as" not in messages
    assert cfg_path.exists()


def test_a_temp_file_left_by_a_killed_save_is_removed_at_load(cfg_path):
    """QA B6: kill -9 mid-save leaves .config-XXXX.tmp behind."""
    config.save(config.from_dict(GOOD), cfg_path)
    folder = cfg_path.parent
    (folder / ".config-k1ll3d.tmp").write_text("{half")
    (folder / ".config-notes.txt").write_text("not ours")
    (folder / "other.tmp").write_text("not ours")
    assert config.load(cfg_path) == config.from_dict(GOOD)
    assert sorted(os.listdir(folder)) == [".config-notes.txt", "config.json", "other.tmp"]


def test_a_paste_settle_from_the_file_is_used(cfg_path):
    write(cfg_path, {**GOOD, "paste_settle_seconds": 3})
    assert config.load(cfg_path).paste_settle_seconds == 3
    assert Config().paste_settle_seconds == 0.75


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

    def approve(self, flags):
        """What Task Manager's Startup tab writes (REG_BINARY)."""
        self.keys.setdefault(("HKCU", autostart.APPROVED_KEY), {})["TranscriptionFlow"] = flags

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


@pytest.mark.parametrize("flags, enabled", [
    (b"\x03" + bytes(11), False), (b"\x07" + bytes(11), False),  # disabled in Task Manager
    (b"\x02" + bytes(11), True), (b"\x06" + bytes(11), True), (b"", True),
])
def test_run_key_honours_the_task_manager_startup_toggle(flags, enabled):
    """review-m2 F9: the box must not show ticked when Windows won't start it."""
    reg = FakeWinreg()
    run = autostart.RunKey(winreg_module=reg)
    run.enable('"tf.exe" --tray')
    reg.approve(flags)
    assert run.is_enabled() is enabled


def test_run_key_enable_clears_the_task_manager_disable():
    reg = FakeWinreg()
    run = autostart.RunKey(winreg_module=reg)
    run.enable('"tf.exe" --tray')
    reg.approve(b"\x03" + bytes(11))
    assert not run.is_enabled()
    run.enable('"tf.exe" --tray')  # the user ticks the box and saves
    assert run.is_enabled()
    assert "TranscriptionFlow" not in reg.keys[("HKCU", autostart.APPROVED_KEY)]


def test_run_key_without_a_run_value_is_off_whatever_task_manager_says():
    reg = FakeWinreg()
    reg.approve(b"\x02" + bytes(11))
    assert not autostart.RunKey(winreg_module=reg).is_enabled()


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
    approved = path + "Approved"  # never the real StartupApproved key
    run = autostart.RunKey(key_path=path, approved_path=approved)
    try:
        run.enable('"x.exe" --tray')
        assert run.command() == '"x.exe" --tray' and run.is_enabled()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, approved, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_BINARY,
                              b"\x03" + bytes(11))
        assert not run.is_enabled()
        run.enable('"x.exe" --tray')
        assert run.is_enabled()
        run.disable()
        assert not run.is_enabled()
    finally:
        for key_path in (path, approved):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            except OSError:
                pass
