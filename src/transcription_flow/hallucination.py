"""Drop Whisper's stock hallucinations (review M5).

On a clip with little or no speech (a cough, a fan, a key click), Whisper
can "hear" lines from the video subtitles it was trained on, such as
"Thanks for watching!" or "Thank you.", and the app would type them into
the user's document. ``verdict`` looks at the whole transcript only, so a
sentence that merely contains one of these phrases is always kept.

Speech is measured with the VAD and no padding (review-m3 F1): faster-whisper's
own ``duration_after_vad`` adds 0.4 s on each side of every span and runs a
span on to the clip's end, so it measured the clip, not the speech.
"""

import logging
import re
import unicodedata

from .recorder import TARGET_RATE

log = logging.getLogger(__name__)

# Seconds of unpadded speech. A stock phrase on less than this is taken as
# noise; said for longer, it is taken as meant. Measured with tiny.en's VAD
# on cuts of tests/fixtures/hello.wav: a 0.35 s word gives 0.29-0.45 s, a
# 0.1-0.2 s burst (a cough or a click) gives 0.06-0.19 s.
LITTLE_SPEECH_SECONDS = 0.25
# The VAD settings for that measure: no padding, and spans split at a
# tenth of a second of silence, so silence around a word isn't counted.
VAD_SPEECH_PAD_MS = 0
VAD_MIN_SILENCE_MS = 100

# Video outros a dictation user doesn't say on their own: always dropped
# when they are the whole transcript.
OUTROS = frozenset({
    "thanks for watching", "thank you for watching", "thank you so much for watching",
    "thanks for watching and see you next time", "please subscribe",
    "like and subscribe", "please like and subscribe", "see you in the next video",
    "subtitles by the amara org community", "transcription by castingwords",
})
# Short phrases people do dictate, so they are dropped only on little speech.
SHORT_PHRASES = frozenset({"thank you", "thanks", "you", "bye", "bye bye", "thank you very much"})
LITTLE_SPEECH = "a stock phrase on little speech"
SHORT_PHRASE_HINT = ("Nothing typed: a very short \"Thank you\" or \"Bye\" can be noise that "
                     "Whisper misheard. Say it a little longer to type it.")

_TAG = re.compile(r"\s*(\[[^\[\]]{0,40}\]|\([^()]{0,40}\))\s*")


class ShortPhraseDropped(Exception):
    """A short stock phrase on little speech was dropped. Not an error;
    ``str(err)`` is a hint for the user (QA B4). Holds no transcript."""

    def __init__(self):
        super().__init__(SHORT_PHRASE_HINT)


def normalize(text: str) -> str:
    """Lower case, punctuation as spaces, single spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def speech_seconds(audio) -> float:
    """Seconds of speech in 16 kHz mono ``audio``, without VAD padding."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    spans = get_speech_timestamps(audio, VadOptions(speech_pad_ms=VAD_SPEECH_PAD_MS,
                                                    min_silence_duration_ms=VAD_MIN_SILENCE_MS))
    return sum(span["end"] - span["start"] for span in spans) / TARGET_RATE


def verdict(text: str, speech):
    """(why ``text`` is a hallucination or None to keep it, the seconds of
    speech or None when not measured). ``speech()`` gives the seconds; it
    is asked only for a short phrase. Sound tags are taken off first (QA
    B3): "[Music] Thanks for watching!" is an outro too."""
    if not normalize(text):
        return "no words", None
    words = normalize(_TAG.sub(" ", text))
    if not words:
        return "only sound tags", None  # "[Music]", "(upbeat music)", "[BLANK_AUDIO]"
    if words in OUTROS:
        return "a video outro", None
    if words in SHORT_PHRASES:
        seconds = speech()
        return (LITTLE_SPEECH if seconds < LITTLE_SPEECH_SECONDS else None), seconds
    return None, None


def clean(text: str, speech) -> str:
    """``text``, or "" when it is a hallucination. Logs the reason, never
    the text. A short phrase dropped on its measured speech raises
    ShortPhraseDropped, so the user hears why nothing was typed."""
    reason, seconds = verdict(text, speech)
    if reason is None:
        return text
    if seconds is None:
        log.info("transcript dropped: %s", reason)
        return ""
    log.info("transcript dropped: %s (%.2f s of speech)", reason, seconds)
    raise ShortPhraseDropped()
