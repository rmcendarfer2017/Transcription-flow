"""Start with Windows: one per-user Run registry value (plan 3.6).

``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\TranscriptionFlow``
holds the command. The installer's checkbox and the settings toggle write
and delete the same value, so the registry is the single source of truth.
No admin rights are needed.

Task Manager's Startup tab can turn the entry off without deleting it: it
writes a value of the same name under ``StartupApproved\\Run`` whose first
byte is odd when disabled. ``is_enabled()`` honours that, and ``enable()``
removes it, so ticking the box really turns autostart back on (review-m2 F9).
"""

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
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
    def __init__(self, winreg_module=None, key_path: str = RUN_KEY, name: str = VALUE_NAME,
                 approved_path: str = APPROVED_KEY):
        if winreg_module is None and sys.platform == "win32":
            import winreg as winreg_module
        self._reg = winreg_module
        self._path = key_path
        self._name = name
        self._approved = approved_path

    @property
    def available(self) -> bool:
        return self._reg is not None

    def _query(self, path: str):
        reg = self._reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_QUERY_VALUE) as key:
                return reg.QueryValueEx(key, self._name)[0]
        except FileNotFoundError:
            return None

    def _delete(self, path: str) -> None:
        reg = self._reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, self._name)
        except FileNotFoundError:
            pass

    def command(self):
        """The current value, or None when there is none."""
        return None if self._reg is None else self._query(self._path)

    def disabled_in_task_manager(self) -> bool:
        flags = self._query(self._approved)
        return isinstance(flags, bytes) and len(flags) > 0 and bool(flags[0] & 1)

    def is_enabled(self) -> bool:
        return self.command() is not None and not self.disabled_in_task_manager()

    def enable(self, command: str) -> None:
        reg = self._reg
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, self._path, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, self._name, 0, reg.REG_SZ, command)
        self._delete(self._approved)  # no entry there means "enabled"

    def disable(self) -> None:
        self._delete(self._path)
