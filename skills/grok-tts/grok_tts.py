#!/usr/bin/env python3
"""
grok-tts skill (OMP).

Turn text into Grok (xAI) TTS voice files plus word timings for HyperFrames.
Stdlib-only (urllib) -- no `requests` and no third-party packages.

This module has no side effects at import time: no network, no file access.
"""
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path


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


class AudioError(GrokTTSError):
    """The returned audio is missing, malformed or the wrong length."""


def repair_wav(data: bytes, expected_duration: float) -> bytes:
    """Rewrite a streaming WAV header with real sizes and check the length."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioError("audio is not a WAV file")
    fmt = None
    pos = 12
    while True:
        if pos + 8 > len(data):
            raise AudioError("WAV file has no data chunk")
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if cid == b"data":
            break
        if cid == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise AudioError("WAV fmt chunk is truncated")
            _, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", data, body)
            fmt = (channels, rate, bits)
        pos = body + size + (size & 1)
    if fmt is None:
        raise AudioError("WAV file has no fmt chunk")
    channels, rate, bits = fmt
    if channels < 1 or rate < 1 or bits < 8:
        raise AudioError("WAV fmt chunk is invalid")
    audio = data[body:]
    if len(audio) % 2:
        audio = audio[:-1]
    actual = len(audio) / (rate * channels * (bits / 8))
    if abs(actual - expected_duration) > 0.05:
        raise AudioError(
            f"audio is {actual:g} s but the timestamps say {expected_duration:g} s"
        )
    header = bytearray(data[:body])
    struct.pack_into("<I", header, 4, len(header) - 8 + len(audio))
    struct.pack_into("<I", header, body - 4, len(audio))
    return bytes(header) + audio


def check_mp3(data: bytes, expected_duration: float, ffprobe: str | None) -> None:
    """Check the MP3 header and, when ffprobe is given, the duration."""
    head = data[:3]
    if not (head == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)):
        raise AudioError("audio is not an MP3 file")
    if ffprobe is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "audio.mp3")
        with open(path, "wb") as fh:
            fh.write(data)
        try:
            res = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=60,
            )
            actual = float(res.stdout.strip())
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise AudioError(f"could not read MP3 duration with ffprobe: {exc}") from exc
    if abs(actual - expected_duration) > 0.1:
        raise AudioError(
            f"audio is {actual:.2f} s but the timestamps say {expected_duration:.2f} s"
        )


def atomic_write_many(items: list) -> None:
    """Write all files via .part files, then replace each destination.

    Existing destinations are snapshotted to .bak first. On any failure every
    destination goes back to its previous content (or is removed if it did not
    exist), leftover .part and .bak files are deleted, and the error is
    re-raised.
    """
    dests = [Path(d) for d, _ in items]
    parts = [Path(str(d) + ".part") for d in dests]
    baks = [Path(str(d) + ".bak") for d in dests]
    existed = [d.exists() for d in dests]
    try:
        for part, (_, payload) in zip(parts, items):
            part.write_bytes(payload)
        for dest, bak, had in zip(dests, baks, existed):
            bak.unlink(missing_ok=True)
            if had:
                try:
                    os.link(dest, bak)
                except OSError:
                    shutil.copy2(dest, bak)
        for part, dest in zip(parts, dests):
            os.replace(part, dest)
    except BaseException:
        # Rollback is driven by the snapshot record, not by progress
        # bookkeeping, so an interrupt at any point is undone.
        for dest, bak, had in zip(dests, baks, existed):
            try:
                if bak.exists():
                    shutil.move(str(bak), str(dest))
                elif not had:
                    dest.unlink(missing_ok=True)
            except OSError:
                pass
        for p in parts + baks:
            p.unlink(missing_ok=True)
        raise
    for bak in baks:
        bak.unlink(missing_ok=True)
