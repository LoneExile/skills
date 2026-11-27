#!/usr/bin/env python3
"""
grok-tts skill (OMP).

Turn text into Grok (xAI) TTS voice files plus word timings for HyperFrames.
Stdlib-only (urllib) -- no `requests` and no third-party packages.

This module has no side effects at import time: no network, no file access.
"""
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
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

    Work files get per-call unique names next to their destination, so a
    user's own `.part`/`.bak` files are never touched. Existing destinations
    are snapshotted to .bak first; a snapshot counts only once it is complete.
    On any failure every destination goes back to its previous content (or is
    removed if it did not exist), leftover work files are deleted, and the
    error is re-raised. A snapshot whose restore failed is kept on disk and
    named in a note on the error.
    """
    dests = [Path(d) for d, _ in items]
    tag = os.urandom(4).hex()
    parts = [Path(f"{d}.{tag}.part") for d in dests]
    baks = [Path(f"{d}.{tag}.bak") for d in dests]
    existed = [d.exists() for d in dests]
    snapped = [False] * len(dests)
    try:
        for part, (_, payload) in zip(parts, items):
            part.write_bytes(payload)
        for i, (dest, bak) in enumerate(zip(dests, baks)):
            if not existed[i]:
                continue
            try:
                try:
                    os.link(dest, bak)
                except OSError:
                    bak.unlink(missing_ok=True)
                    shutil.copy2(dest, bak)
            except BaseException:
                bak.unlink(missing_ok=True)  # incomplete snapshot: never restore
                raise
            snapped[i] = True
        for part, dest in zip(parts, dests):
            os.replace(part, dest)
    except BaseException as exc:
        kept = set()
        for i, (dest, bak) in enumerate(zip(dests, baks)):
            try:
                if snapped[i]:
                    shutil.move(str(bak), str(dest))
                elif not existed[i]:
                    dest.unlink(missing_ok=True)
            except OSError as restore_err:
                kept.add(bak)
                exc.add_note(
                    f"could not restore {dest} ({restore_err}); "
                    f"its previous content is kept in {bak}"
                )
        for p in parts + baks:
            if p not in kept:
                p.unlink(missing_ok=True)
        raise
    for bak in baks:
        bak.unlink(missing_ok=True)


class KeyMissingError(GrokTTSError):
    """No usable xAI key; the message lists each source and why it missed."""


def read_dotenv_value(path: Path, name: str) -> str | None:
    """Value of `NAME=...` in a dotenv file; None if absent or empty."""
    pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(name) + r"\s*=\s*(.*)$")
    value = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line)
        if m:
            value = m.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            break
    return value or None


def _grok_login_key(path: Path, now: datetime) -> tuple[str | None, str]:
    """(key, reason): key is set on a hit, otherwise reason says why not."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "file missing"
    except (OSError, ValueError) as e:
        return None, f"unreadable ({type(e).__name__})"
    entries = [e for e in data.values() if isinstance(e, dict)] if isinstance(data, dict) else []
    expired = []
    for entry in entries:
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            continue
        written = entry.get("expires_at")
        try:
            expires = datetime.fromisoformat(written)
        except (TypeError, ValueError):
            return key, ""  # missing or unparseable expiry counts as usable
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > now:
            return key, ""
        expired.append(written)
    if expired:
        return None, f"expired at {expired[0]}"
    return None, "no login entry"


def resolve_key(env: Mapping[str, str], home: Path, now: datetime) -> tuple[str, str]:
    """Find the xAI key; the first source holding one wins. Returns (key, source label)."""
    misses = []
    for name in ("XAI_API_KEY", "GROK_API_KEY"):
        label = f"{name} (environment)"
        if env.get(name):
            return env[name], label
        misses.append((label, "unset"))

    omp_env = home / ".omp" / ".env"
    try:
        for name in ("XAI_API_KEY", "GROK_API_KEY"):
            value = read_dotenv_value(omp_env, name)
            if value:
                return value, f"~/.omp/.env ({name})"
        misses.append(("~/.omp/.env", "no XAI_API_KEY or GROK_API_KEY line"))
    except FileNotFoundError:
        misses.append(("~/.omp/.env", "file missing"))
    except (OSError, ValueError) as e:
        misses.append(("~/.omp/.env", f"unreadable ({type(e).__name__})"))

    label = "grok CLI login (~/.grok/auth.json)"
    key, reason = _grok_login_key(home / ".grok" / "auth.json", now)
    if key:
        return key, label
    misses.append((label, reason))

    lines = ["no xAI key found. Checked:"] + [f"  - {lbl}: {why}" for lbl, why in misses]
    lines.append("Set XAI_API_KEY, add it to ~/.omp/.env, or run `grok` to log in.")
    raise KeyMissingError("\n".join(lines))
