"""Drop Whisper's stock hallucinations (review M5).

On a clip with little or no speech (a cough, a fan, a key click), Whisper
can "hear" lines from the video subtitles it was trained on, such as
"Thanks for watching!" or "Thank you.", and the app would type them into
the user's document. ``verdict`` looks at the whole transcript only, so a
sentence that merely contains one of these phrases is always kept.
"""

import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# Seconds of speech the VAD found. A stock phrase on less than this is
# taken as noise; said for longer, it is taken as meant.
LITTLE_SPEECH_SECONDS = 1.0

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

_TAG = re.compile(r"\s*(\[[^\[\]]{0,40}\]|\([^()]{0,40}\))\s*")


def normalize(text: str) -> str:
    """Lower case, punctuation as spaces, single spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def verdict(text: str, speech_seconds: float):
    """Why ``text`` is a hallucination, or None to keep it."""
    if not normalize(text):
        return "no words"
    if not _TAG.sub("", text).strip():
        return "only sound tags"  # "[Music]", "(upbeat music)", "[BLANK_AUDIO]"
    words = normalize(text)
    if words in OUTROS:
        return "a video outro"
    if words in SHORT_PHRASES and speech_seconds < LITTLE_SPEECH_SECONDS:
        return "a stock phrase on little speech"
    return None


def clean(text: str, speech_seconds: float) -> str:
    """``text``, or "" when it is a hallucination. Logs the reason, never the text."""
    reason = verdict(text, speech_seconds)
    if reason is None:
        return text
    log.info("transcript dropped: %s (%.1f s of speech)", reason, speech_seconds)
    return ""
