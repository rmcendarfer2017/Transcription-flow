"""Start with Windows: one per-user Run registry value (plan 3.6).

``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\TranscriptionFlow``
holds the command. The installer's checkbox and the settings toggle write
and delete the same value, so the registry is the single source of truth.
No admin rights are needed.
"""

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TranscriptionFlow"


def _checkout_shim():
    """dictation.py when running from a source checkout, else None."""
    shim = Path(__file__).resolve().parents[2] / "dictation.py"
    return shim if shim.is_file() else None


def launch_command(executable: str = None, frozen: bool = None, shim=_checkout_shim) -> str:
    """The command Windows runs at sign-in: the packaged exe, or (from a dev
    venv) pythonw, so no console window opens."""
    executable = executable or sys.executable
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        return f'"{executable}" --tray'
    exe = Path(executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if exe.name.lower() == "python.exe" and pythonw.exists() else exe
    script = shim()
    target = f'"{script}"' if script else "-m transcription_flow"
    return f'"{runner}" -P {target} --tray'


class RunKey:
    def __init__(self, winreg_module=None, key_path: str = RUN_KEY, name: str = VALUE_NAME):
        if winreg_module is None and sys.platform == "win32":
            import winreg as winreg_module
        self._reg = winreg_module
        self._path = key_path
        self._name = name

    @property
    def available(self) -> bool:
        return self._reg is not None

    def command(self):
        """The current value, or None when autostart is off."""
        if self._reg is None:
            return None
        reg = self._reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, self._path, 0, reg.KEY_QUERY_VALUE) as key:
                value, _kind = reg.QueryValueEx(key, self._name)
        except FileNotFoundError:
            return None
        return value

    def is_enabled(self) -> bool:
        return self.command() is not None

    def enable(self, command: str) -> None:
        reg = self._reg
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, self._path, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, self._name, 0, reg.REG_SZ, command)

    def disable(self) -> None:
        reg = self._reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, self._path, 0, reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, self._name)
        except FileNotFoundError:
            pass
