"""tools/render_pill.py: the README pill renderer's pure helpers (the Tk and
X11 part runs only under Xvfb, see the script's docstring)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "render_pill.py"


@pytest.fixture(scope="module")
def render_pill():
    spec = importlib.util.spec_from_file_location("render_pill", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_key_out_makes_only_the_exact_key_colour_transparent(render_pill):
    pytest.importorskip("PIL")
    from PIL import Image

    near_key = (254, 0, 255)
    grab = Image.new("RGB", (3, 1))
    grab.putpixel((0, 0), render_pill.KEY)
    grab.putpixel((1, 0), near_key)
    grab.putpixel((2, 0), (0, 0, 0))

    out = render_pill.key_out(grab, (2, 1))

    assert out.size == (7, 3)
    assert out.getpixel((2, 1)) == render_pill.BACKGROUND
    assert out.getpixel((3, 1)) == near_key  # a colour key is exact, as on Windows
    assert out.getpixel((4, 1)) == (0, 0, 0)
    assert out.getpixel((0, 0)) == render_pill.BACKGROUND


def test_speech_levels_are_repeatable_meter_values_with_pauses(render_pill):
    levels = render_pill.speech_levels(75)

    assert levels == render_pill.speech_levels(75)
    assert len(levels) == 75
    assert all(0.0 <= v <= 1.0 for v in levels)
    assert min(levels) == 0.0  # word gaps and syllable troughs
    assert max(levels) > 0.5  # speech fills most of the meter
