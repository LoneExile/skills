#!/usr/bin/env python3
"""
grok-tts skill (OMP).

Turn text into Grok (xAI) TTS voice files plus word timings for HyperFrames.
Stdlib-only (urllib) -- no `requests` and no third-party packages.

This module has no side effects at import time: no network, no file access.
"""
import base64
import binascii
import http.client
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
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


API_BASE = "https://api.x.ai"
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_BACKOFF = (2, 4, 8)


class ApiError(GrokTTSError):
    """The API or the network failed; `status` is the HTTP status, if any."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Synthesis:
    audio: bytes
    words: list
    duration: float


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the Authorization header must stay on one host."""

    def redirect_request(self, *args, **kwargs):
        return None


def _usable_header_value(key: str) -> bool:
    try:
        key.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return "\r" not in key and "\n" not in key


def _error_text(raw: bytes, key: str) -> str:
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    found = None
    if isinstance(data, dict):
        for field in ("error", "code"):
            if isinstance(data.get(field), str) and data[field]:
                found = data[field]
                break
    if found is None:
        found = text[:200]
    if key:
        found = found.replace(key, "[redacted]")
    return found


def _retry_wait(attempt: int, retry_after, rand) -> float:
    if retry_after is not None:
        try:
            wait = float(retry_after)
        except ValueError:
            wait = None
        if wait is not None and math.isfinite(wait) and wait >= 0:
            return min(wait, 60.0)
    return _BACKOFF[attempt] + rand()


def request_json(method: str, path: str, *, key: str, source: str,
                 body: dict | None, api_base: str, timeout: float,
                 sleep=time.sleep, rand=random.random) -> dict:
    """Send one API request with retries; return the decoded JSON object.

    The key goes only into the Authorization header. No message, note or
    chained exception carries it.
    """
    if not _usable_header_value(key):
        raise ApiError(f"the key from {source} is not a valid header value") from None
    data = None if body is None else json.dumps(body).encode("utf-8")
    opener = urllib.request.build_opener(_NoRedirect)
    url = api_base.rstrip("/") + path
    attempts = len(_BACKOFF) + 1
    for attempt in range(attempts):
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        status = None
        raw = b""
        retry_after = None
        reason = None
        bad_header = False
        resp = None
        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            resp = e
        except (ValueError, UnicodeError):
            bad_header = True
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            reason = getattr(e, "reason", None) or e
        if bad_header:
            # Raised outside the except block so no exception is chained to it.
            raise ApiError(f"the key from {source} is not a valid header value") from None
        if resp is not None:
            try:
                with resp:
                    raw = resp.read()
                status = resp.status
                if isinstance(resp, urllib.error.HTTPError):
                    retry_after = resp.headers.get("Retry-After") if resp.headers else None
            except (http.client.HTTPException, OSError, ValueError) as e:
                status = None
                reason = getattr(e, "reason", None) or e
        if reason is not None:
            reason = str(reason).replace(key, "[redacted]") if key else str(reason)
        if status is None:
            if attempt < attempts - 1:
                sleep(_retry_wait(attempt, None, rand))
                continue
            raise ApiError(f"could not reach {api_base} after {attempts} attempts: {reason}")
        if 200 <= status < 300:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                raise ApiError("unexpected response shape: not a JSON object", status)
            return parsed
        message = _error_text(raw, key)
        if status in _RETRY_STATUSES:
            if attempt < attempts - 1:
                sleep(_retry_wait(attempt, retry_after, rand))
                continue
            raise ApiError(
                f"xAI API error {status} after {attempts} attempts: {message}", status)
        if status in (401, 403) or (status == 400 and "api key" in message.lower()):
            raise ApiError(f"the key from {source} was rejected: {message}", status)
        if status == 404 and "voice" in message.lower():
            raise ApiError(
                f"xAI API error {status}: {message}; run --list to see voices", status)
        raise ApiError(f"xAI API error {status}: {message}", status)
    raise AssertionError("unreachable")  # pragma: no cover


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def synthesize(text: str, *, voice: str, lang: str, speed: float, normalize: bool,
               fmt: str, key: str, source: str, api_base: str = API_BASE,
               timeout: float = 300, ffprobe: str | None = None,
               sleep=time.sleep, rand=random.random) -> Synthesis:
    """Turn text into audio plus word timings."""
    if fmt == "mp3":
        output = {"codec": "mp3", "sample_rate": 44100, "bit_rate": 192000}
    else:
        output = {"codec": "wav", "sample_rate": 44100}
    body = {
        "text": text, "voice_id": voice, "language": lang, "speed": speed,
        "with_timestamps": True, "output_format": output,
    }
    if normalize:
        body["text_normalization"] = True
    resp = request_json("POST", "/v1/tts", key=key, source=source, body=body,
                        api_base=api_base, timeout=timeout, sleep=sleep, rand=rand)
    stamps = resp.get("audio_timestamps")
    chars = stamps.get("graph_chars") if isinstance(stamps, dict) else None
    times = stamps.get("graph_times") if isinstance(stamps, dict) else None
    duration = resp.get("duration")
    if (not isinstance(resp.get("audio"), str) or not _number(duration)
            or not isinstance(chars, list) or not isinstance(times, list)):
        raise ApiError("unexpected response shape: missing audio, duration or timestamps")
    try:
        audio = base64.b64decode(resp["audio"], validate=True)
    except (binascii.Error, ValueError):
        raise ApiError("unexpected response shape: audio is not valid base64") from None
    if fmt == "mp3":
        check_mp3(audio, duration, ffprobe)
    else:
        audio = repair_wav(audio, duration)
    return Synthesis(audio=audio, words=build_words(chars, times), duration=float(duration))


def list_voices(*, key: str, source: str, api_base: str = API_BASE,
                timeout: float = 300, sleep=time.sleep, rand=random.random) -> list:
    """The voices the API offers."""
    resp = request_json("GET", "/v1/tts/voices", key=key, source=source, body=None,
                        api_base=api_base, timeout=timeout, sleep=sleep, rand=rand)
    voices = resp.get("voices")
    if not isinstance(voices, list):
        raise ApiError("unexpected response shape: no voices list")
    return voices
