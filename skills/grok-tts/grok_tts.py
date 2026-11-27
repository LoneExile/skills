#!/usr/bin/env python3
"""
grok-tts skill (OMP).

Turn text into Grok (xAI) TTS voice files plus word timings for HyperFrames.
Stdlib-only (urllib) -- no `requests` and no third-party packages.

This module has no side effects at import time: no network, no file access.
"""
import math
import re


class GrokTTSError(Exception):
    """Base error; `exit_code` is the process exit status for the CLI."""

    exit_code = 1


class UsageError(GrokTTSError):
    """Bad input, detected before any API call."""

    exit_code = 2


class TimingError(GrokTTSError):
    """The API's character timestamps are unusable."""


# Speech tags, without brackets.
INLINE_TAGS = [
    "pause", "long-pause", "hum-tune", "laugh", "chuckle", "giggle", "cry",
    "tsk", "tongue-click", "lip-smack", "breath", "inhale", "exhale", "sigh",
]
WRAP_TAGS = [
    "soft", "whisper", "loud", "build-intensity", "decrease-intensity",
    "higher-pitch", "lower-pitch", "slow", "fast", "sing-song", "singing",
    "emphasis",
]

TAG_RE = re.compile(
    r"\[(?:" + "|".join(map(re.escape, INLINE_TAGS)) + r")\]"
    r"|</?(?:" + "|".join(map(re.escape, WRAP_TAGS)) + r")>"
)


def strip_tags(text: str) -> str:
    """Remove speech tags only; all other text is kept."""
    return TAG_RE.sub("", text)


def build_words(graph_chars: list, graph_times: list) -> list:
    """Group per-character timestamps into words.

    Words split at whitespace and at speech tags. A word runs from the start
    of its first character to the end of its last. Times are kept as given.
    """
    if len(graph_chars) != len(graph_times):
        raise TimingError("timestamp characters and times differ in length")
    for pair in graph_times:
        if len(pair) != 2:
            raise TimingError("each timestamp needs a start and an end")
        start, end = pair
        if not (math.isfinite(start) and math.isfinite(end)):
            raise TimingError("timestamp is not a finite number")
        if end < start:
            raise TimingError("timestamp ends before it starts")

    tagged = set()
    for m in TAG_RE.finditer("".join(graph_chars)):
        tagged.update(range(m.start(), m.end()))

    words = []
    run = []  # indices of the current word

    def flush():
        if run:
            words.append({
                "id": f"w{len(words)}",
                "text": "".join(graph_chars[i] for i in run),
                "start": graph_times[run[0]][0],
                "end": graph_times[run[-1]][1],
            })
            run.clear()

    for i, ch in enumerate(graph_chars):
        if i in tagged or ch.isspace():
            flush()
        else:
            run.append(i)
    flush()
    return words
