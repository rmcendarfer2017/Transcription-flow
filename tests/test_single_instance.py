import subprocess
import sys
import textwrap
import uuid

import pytest

from helpers import child_env
from transcription_flow.single_instance import AlreadyRunning, acquire


@pytest.fixture
def name():
    return f"Local\\TranscriptionFlow.Test.{uuid.uuid4().hex}"


def test_second_acquire_is_refused(tmp_path, name):
    first = acquire(tmp_path, name)
    try:
        with pytest.raises(AlreadyRunning):
            acquire(tmp_path, name)
    finally:
        first.release()


def test_release_lets_the_next_copy_start(tmp_path, name):
    acquire(tmp_path, name).release()
    acquire(tmp_path, name).release()


def test_another_process_is_refused_while_held(tmp_path, name):
    held = acquire(tmp_path, name)
    code = textwrap.dedent(f"""
        import sys
        from transcription_flow.single_instance import acquire, AlreadyRunning
        try:
            acquire({str(tmp_path)!r}, {name!r})
        except AlreadyRunning:
            sys.exit(3)
        sys.exit(0)
    """)
    run = lambda: subprocess.run([sys.executable, "-c", code], timeout=30,  # noqa: E731
                                 env=child_env()).returncode
    try:
        assert run() == 3
    finally:
        held.release()
    assert run() == 0
