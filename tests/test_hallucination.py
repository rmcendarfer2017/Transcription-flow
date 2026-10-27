"""The hallucination filter (review M5): one reason per case, plus keep controls."""

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from transcription_flow import hallucination, models
from transcription_flow.engine import WhisperEngine
from transcription_flow.recorder import TARGET_RATE
from transcription_flow.selftest import read_wav
from transcription_flow.hallucination import (LITTLE_SPEECH, LITTLE_SPEECH_SECONDS,
                                              ShortPhraseDropped)

LITTLE, PLENTY = 0.1, 3.0
HELLO = Path(__file__).parent / "fixtures" / "hello.wav"


def verdict(text, seconds):
    return hallucination.verdict(text, lambda: seconds)[0]


@pytest.mark.parametrize("text, speech, reason", [
    ("...", LITTLE, "no words"),
    (" ♪ ♪ ", PLENTY, "no words"),
    ("[Music]", PLENTY, "only sound tags"),
    ("(upbeat music) [BLANK_AUDIO]", PLENTY, "only sound tags"),
    ("Thanks for watching!", PLENTY, "a video outro"),
    ("Subtitles by the Amara.org community", PLENTY, "a video outro"),
    ("THANK YOU FOR WATCHING.", LITTLE, "a video outro"),
    ("[Music] Thanks for watching!", PLENTY, "a video outro"),  # tags off first (QA B3)
    ("Thank you.", LITTLE, LITTLE_SPEECH),
    (" you", LITTLE, LITTLE_SPEECH),
    ("Bye-bye.", LITTLE, LITTLE_SPEECH),
    ("Thank you. [Music]", LITTLE, LITTLE_SPEECH),  # QA B3
])
def test_each_hallucination_is_dropped_for_its_own_reason(text, speech, reason):
    assert verdict(text, speech) == reason


@pytest.mark.parametrize("text, speech", [
    ("Thank you.", PLENTY),  # said on purpose, over plenty of speech
    ("Thank you.", LITTLE_SPEECH_SECONDS),  # the bound itself counts as enough
    ("Thanks for watching the kids tonight.", LITTLE),  # contains a phrase, isn't one
    ("Please send the report [draft] today.", LITTLE),  # a tag inside real words
    ("Hello world.", LITTLE),
    ("Music", LITTLE),  # the word, not a tag
    ("Thank you. [Music]", PLENTY),  # a tag doesn't make a meant phrase junk
])
def test_real_dictation_is_kept(text, speech):
    assert verdict(text, speech) is None


def test_speech_is_measured_only_for_a_short_phrase():
    def never():
        raise AssertionError("measured speech for a transcript that doesn't need it")

    for text in ("Hello world.", "Thanks for watching!", "[Music]", "..."):
        hallucination.verdict(text, never)


def test_clean_logs_the_reason_but_never_the_text(caplog):
    caplog.set_level("INFO")
    assert hallucination.clean("Thanks for watching!", lambda: PLENTY) == ""
    assert hallucination.clean("Hello world.", lambda: PLENTY) == "Hello world."
    assert "a video outro" in caplog.text and "watching" not in caplog.text.lower()


def test_a_dropped_short_phrase_raises_a_hint_without_the_text(caplog):
    """QA B4: the user hears why nothing was typed; the log has the seconds."""
    caplog.set_level("INFO")
    with pytest.raises(ShortPhraseDropped) as caught:
        hallucination.clean("Thank you.", lambda: 0.12)
    assert str(caught.value) == hallucination.SHORT_PHRASE_HINT
    assert "(0.12 s of speech)" in caplog.text and "thank" not in caplog.text.lower()


class FakeModel:
    """faster-whisper's transcribe: the texts, and a padded VAD duration
    that the filter must not use (review-m3 F1)."""

    def __init__(self, texts):
        self.texts = texts

    def transcribe(self, audio, **kw):
        segments = [SimpleNamespace(text=t) for t in self.texts]
        return iter(segments), SimpleNamespace(duration=5.0, duration_after_vad=5.0)


@pytest.mark.parametrize("texts, speech, expected", [
    ([" Thank", " you."], PLENTY, "Thank you."),
    ([], LITTLE, ""),
    ([" Hello", " there."], LITTLE, "Hello there."),
])
def test_engine_filters_on_the_unpadded_speech_in_the_audio(texts, speech, expected,
                                                            monkeypatch):
    audio = np.zeros(10, np.float32)
    monkeypatch.setattr(hallucination, "speech_seconds",
                        lambda a: speech if a is audio else pytest.fail("other audio"))
    engine = WhisperEngine("unused")
    engine._model = FakeModel(texts)
    assert engine.transcribe(audio) == expected


def test_engine_drops_a_stock_phrase_on_little_speech_with_a_hint(monkeypatch):
    monkeypatch.setattr(hallucination, "speech_seconds", lambda a: LITTLE)
    engine = WhisperEngine("unused")
    engine._model = FakeModel([" Thank", " you."])
    with pytest.raises(ShortPhraseDropped):
        engine.transcribe(np.zeros(10, np.float32))


# -- the real VAD on the review's probe clips (review-m3 F1) -----------------------

def _hello():
    return read_wav(HELLO)


def _clip(sound, lead, total, seed):
    """``sound`` placed ``lead`` s into ``total`` s of faint noise."""
    clip = np.zeros(int(total * TARGET_RATE), np.float32)
    start = int(lead * TARGET_RATE)
    clip[start:start + len(sound)] = sound
    return clip + np.random.default_rng(seed).normal(0, 0.002, len(clip)).astype(np.float32)


WORD_START = 0.352  # where the VAD finds the speech in hello.wav


def word_clip(word, lead, tail):
    """The first ``word`` s of hello.wav, as a quick "Thanks." would be said."""
    s0 = int(WORD_START * TARGET_RATE)
    return _clip(_hello()[s0:s0 + int(word * TARGET_RATE)], lead, lead + word + tail, seed=1)


def burst_clip(burst, total):
    """A ``burst`` s noise-like click or cough in a ``total`` s hold."""
    s0 = int(WORD_START * TARGET_RATE)
    return _clip(_hello()[s0:s0 + int(burst * TARGET_RATE)], 0.6, total, seed=2)


@pytest.mark.parametrize("word, lead, tail", [
    (0.35, 0.3, 0.15), (0.35, 0.3, 0.4), (0.35, 0.5, 0.6), (0.5, 0.3, 0.15), (0.7, 0.5, 0.6)])
def test_a_quick_word_measures_as_enough_speech(word, lead, tail):
    assert hallucination.speech_seconds(word_clip(word, lead, tail)) >= LITTLE_SPEECH_SECONDS


@pytest.mark.parametrize("burst, total", [(0.1, 1.5), (0.1, 3.0), (0.2, 1.5), (0.2, 2.0),
                                          (0.2, 3.0)])
def test_a_burst_in_a_long_hold_measures_as_little_speech(burst, total):
    assert hallucination.speech_seconds(burst_clip(burst, total)) < LITTLE_SPEECH_SECONDS


@pytest.fixture(scope="module")
def tiny_en():
    models_dir = os.environ.get("TFLOW_TEST_MODELS_DIR")
    if not models_dir or not models.is_installed("tiny.en", Path(models_dir)):
        pytest.skip("set TFLOW_TEST_MODELS_DIR to a folder with tiny.en installed (see README)")
    return WhisperEngine(Path(models_dir) / "tiny.en").load()


class SaysThankYou:
    """The real model runs (so faster-whisper's own VAD and info are real);
    only its words are swapped for the stock phrase under test."""

    def __init__(self, model):
        self.model = model

    def transcribe(self, audio, **kw):
        segments, info = self.model.transcribe(audio, **kw)
        list(segments)
        return iter([SimpleNamespace(text=" Thank you.")]), info


@pytest.mark.slow
def test_real_tiny_en_keeps_a_quick_thanks_and_drops_one_on_a_burst(tiny_en, monkeypatch):
    """review-m3 F1: a 0.35 s word in a 0.8 s clip (padded VAD time 0.80 s,
    under the old 1 s bound) keeps its text; a 0.2 s burst in a 2 s hold
    (padded 1.76 s, over the old bound) counts as little speech."""
    word, burst = word_clip(0.35, 0.3, 0.15), burst_clip(0.2, 2.0)
    assert tiny_en.transcribe(word)  # the real model hears the word
    monkeypatch.setattr(tiny_en, "_model", SaysThankYou(tiny_en._model))
    assert tiny_en.transcribe(word) == "Thank you."
    with pytest.raises(ShortPhraseDropped):
        tiny_en.transcribe(burst)
