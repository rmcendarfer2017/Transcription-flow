import logging
import sys
from pathlib import Path

import pytest

from helpers import FakeSoundDevice, TimerFactory

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _scratch_home(tmp_path, monkeypatch):
    """Keep every test away from the real data folder."""
    monkeypatch.setenv("TFLOW_HOME", str(tmp_path / "tflow-home"))


@pytest.fixture(autouse=True)
def _restore_logging():
    """logsetup.setup() (called by main) changes the root logger and may swap
    None streams; undo it so log files close (Windows) and tests stay apart."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level, sys.stdout, sys.stderr)
    yield
    for handler in root.handlers[:]:
        if getattr(handler, "_tflow", False):
            root.removeHandler(handler)
            handler.close()
    root.handlers[:], root.level = saved[0], saved[1]
    sys.stdout, sys.stderr = saved[2], saved[3]


@pytest.fixture
def fake_sd():
    return FakeSoundDevice()


@pytest.fixture
def timers():
    return TimerFactory()
