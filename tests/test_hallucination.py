"""The hallucination filter (review M5): one reason per case, plus keep controls."""

from types import SimpleNamespace

import pytest

from transcription_flow import hallucination
from transcription_flow.engine import WhisperEngine
from transcription_flow.hallucination import LITTLE_SPEECH_SECONDS, verdict

LITTLE, PLENTY = 0.4, 3.0


@pytest.mark.parametrize("text, speech, reason", [
    ("...", LITTLE, "no words"),
    (" ♪ ♪ ", PLENTY, "no words"),
    ("[Music]", PLENTY, "only sound tags"),
    ("(upbeat music) [BLANK_AUDIO]", PLENTY, "only sound tags"),
    ("Thanks for watching!", PLENTY, "a video outro"),
    ("Subtitles by the Amara.org community", PLENTY, "a video outro"),
    ("THANK YOU FOR WATCHING.", LITTLE, "a video outro"),
    ("Thank you.", LITTLE, "a stock phrase on little speech"),
    (" you", LITTLE, "a stock phrase on little speech"),
    ("Bye-bye.", LITTLE, "a stock phrase on little speech"),
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
])
def test_real_dictation_is_kept(text, speech):
    assert verdict(text, speech) is None


def test_clean_logs_the_reason_but_never_the_text(caplog):
    caplog.set_level("INFO")
    assert hallucination.clean("Thanks for watching!", PLENTY) == ""
    assert hallucination.clean("Hello world.", PLENTY) == "Hello world."
    assert "a video outro" in caplog.text and "watching" not in caplog.text.lower()


class FakeModel:
    def __init__(self, texts, speech):
        self.texts, self.speech = texts, speech

    def transcribe(self, audio, **kw):
        segments = [SimpleNamespace(text=t) for t in self.texts]
        return iter(segments), SimpleNamespace(duration=5.0, duration_after_vad=self.speech)


@pytest.mark.parametrize("texts, speech, expected", [
    ([" Thank", " you."], LITTLE, ""),
    ([" Thank", " you."], PLENTY, "Thank you."),
    ([], 0.0, ""),
    ([" Hello", " there."], LITTLE, "Hello there."),
])
def test_engine_filters_on_the_speech_the_vad_found(texts, speech, expected):
    engine = WhisperEngine("unused")
    engine._model = FakeModel(texts, speech)
    assert engine.transcribe(None) == expected
