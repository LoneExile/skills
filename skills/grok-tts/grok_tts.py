#!/usr/bin/env python3
"""
grok-tts skill (OMP).

Turn text into Grok (xAI) TTS voice files plus word timings for HyperFrames.
Stdlib-only (urllib) -- no `requests` and no third-party packages.

This module has no side effects at import time: no network, no file access.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import http.client
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


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
    if not abs(actual - expected_duration) <= 0.05:  # NaN fails the check, never passes it
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
    if not abs(actual - expected_duration) <= 0.1:  # NaN fails the check, never passes it
        raise AudioError(
            f"audio is {actual:.2f} s but the timestamps say {expected_duration:.2f} s"
        )


def _add_note(exc: BaseException, note: str) -> None:
    """Attach a note to an exception. `add_note` exists only from Python 3.11."""
    add = getattr(exc, "add_note", None)
    if add is not None:
        add(note)
        return
    notes = getattr(exc, "__notes__", None)
    if not isinstance(notes, list):
        notes = []
        exc.__notes__ = notes
    notes.append(note)


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
                _add_note(
                    exc,
                    f"could not restore {dest} ({restore_err}); "
                    f"its previous content is kept in {bak}",
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
    """Value of `NAME=...` in a dotenv file; None if absent or empty.

    As in OMP, the last matching line wins. A BOM is dropped, and an
    undecodable byte becomes U+FFFD instead of hiding the whole file.
    """
    pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(name) + r"\s*=\s*(.*)$")
    value = None
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = pattern.match(line)
        if m:
            value = m.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
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
        stamp = written
        if isinstance(written, str) and written.endswith("Z"):
            stamp = written[:-1] + "+00:00"  # fromisoformat reads Z only from Python 3.11
        try:
            expires = datetime.fromisoformat(stamp)
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
    except OSError as e:
        misses.append(("~/.omp/.env", f"unreadable ({type(e).__name__})"))

    label = "grok CLI login (~/.grok/auth.json)"
    key, reason = _grok_login_key(home / ".grok" / "auth.json", now)
    if key:
        return key, label
    misses.append((label, reason))

    lines = ["no xAI key found. Checked:"] + [f"  - {lbl}: {why}" for lbl, why in misses]
    lines.append("Set XAI_API_KEY, add it to ~/.omp/.env, or run `grok` to log in.")
    raise KeyMissingError("\n".join(lines))


def _key_provider(env: Mapping[str, str], home: Path, now: datetime) -> Callable[[], tuple]:
    """A key lookup that runs once per run and names the source on stderr."""
    found = []

    def provider():
        if not found:
            key, source = resolve_key(env, home, now)
            print(f"Key: {source}", file=sys.stderr)
            found.append((key, source))
        return found[0]

    return provider


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


def _positive_finite(value) -> bool:
    try:
        number = float(value)
    except OverflowError:  # an integer too large for a float
        return False
    return math.isfinite(number) and number > 0


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
    if not _positive_finite(duration):
        raise ApiError(
            f"unexpected response shape: duration {str(duration)[:30]} "
            "is not a finite number above 0")
    try:
        audio = base64.b64decode(resp["audio"], validate=True)
    except (binascii.Error, ValueError):
        raise ApiError("unexpected response shape: audio is not valid base64") from None
    if fmt == "mp3":
        check_mp3(audio, duration, ffprobe)
    else:
        audio = repair_wav(audio, duration)
    try:
        words = build_words(chars, times)
    except (TypeError, ValueError, OverflowError):
        words = None  # raised below, outside the except block, so nothing is chained
    if words is None:
        raise ApiError("unexpected response shape: timestamps") from None
    if not words:
        raise ApiError("unexpected response shape: no word timings")
    return Synthesis(audio=audio, words=words, duration=float(duration))


def list_voices(*, key: str, source: str, api_base: str = API_BASE,
                timeout: float = 300, sleep=time.sleep, rand=random.random) -> list:
    """The voices the API offers."""
    resp = request_json("GET", "/v1/tts/voices", key=key, source=source, body=None,
                        api_base=api_base, timeout=timeout, sleep=sleep, rand=rand)
    voices = resp.get("voices")
    if not isinstance(voices, list) or not all(isinstance(v, dict) for v in voices):
        raise ApiError("unexpected response shape: voices is not a list of objects")
    return voices


DOCUMENTED_LANGS = frozenset({
    "auto", "en", "ar-eg", "ar-sa", "ar-ae", "bn", "zh", "fr", "de", "hi", "id",
    "it", "ja", "ko", "pt-br", "pt-pt", "ru", "es-mx", "es-es", "tr", "vi",
})
MAX_TEXT_CHARS = 60000
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$")


def validate_request(text: str, lang: str, speed: float) -> str | None:
    """Raise UsageError on bad input; return a warning for an undocumented language."""
    stripped = text.strip()
    if not stripped:
        raise UsageError("text is empty")
    if len(stripped) > MAX_TEXT_CHARS:
        raise UsageError(
            f"text is {len(stripped)} characters; the limit is {MAX_TEXT_CHARS} per request")
    if strip_tags(stripped).strip() == "":
        raise UsageError("text has only speech tags and nothing to say")
    if not 0.7 <= speed <= 1.5:
        raise UsageError(f"speed {speed} is out of range; use 0.7 to 1.5")
    if lang.lower() != "auto" and not _LANG_RE.fullmatch(lang):
        raise UsageError(
            f"language {lang!r} is not a language code; use auto or a code like en or pt-BR")
    if lang.lower() not in DOCUMENTED_LANGS:
        return f"language {lang!r} is not in the documented list; sending it anyway"
    return None


def cache_key(text: str, voice: str, lang: str, speed: float,
              normalize: bool, fmt: str) -> str:
    """sha256 over everything that changes the audio."""
    canonical = json.dumps(
        {"v": 1, "text": text, "voice": voice.lower(), "lang": lang.lower(),
         "speed": speed, "normalize": normalize, "fmt": fmt},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_cache_dir(env: Mapping[str, str], home: Path) -> Path:
    base = env.get("XDG_CACHE_HOME")
    return (Path(base) if base else home / ".cache") / "grok-tts"


def _words_json(words: list) -> bytes:
    return json.dumps(words, indent=1, ensure_ascii=False).encode("utf-8")


def _write_cache(cache_dir: Path, entries: list) -> None:
    """Best effort: a cache that cannot be written never fails a run that has its audio."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_many(entries)
    except OSError as exc:
        print(f"WARNING: cache not updated ({exc})", file=sys.stderr)


def produce(text: str, dest: Path, words_dest: Path | None, *, voice: str,
            lang: str, speed: float, normalize: bool, cache_dir: Path,
            use_cache: bool, key_provider: Callable[[], tuple],
            api_base: str = API_BASE, timeout: float = 300, ffprobe=None,
            sleep=time.sleep, rand=random.random) -> tuple:
    """Write `dest` (and `words_dest`); return (words, cached)."""
    fmt = dest.suffix.lower().lstrip(".")
    digest = cache_key(text, voice, lang, speed, normalize, fmt)
    audio_cache = cache_dir / f"{digest}.{fmt}"
    words_cache = cache_dir / f"{digest}.words.json"
    if use_cache and audio_cache.is_file() and words_cache.is_file():
        try:
            audio = audio_cache.read_bytes()
            words_raw = words_cache.read_bytes()
            words = json.loads(words_raw)
        except (OSError, ValueError):
            words = None
        if isinstance(words, list):
            items = [(dest, audio)]
            if words_dest is not None:
                items.append((words_dest, words_raw))
            atomic_write_many(items)
            return words, True
    key, source = key_provider()
    result = synthesize(text, voice=voice, lang=lang, speed=speed,
                        normalize=normalize, fmt=fmt, key=key, source=source,
                        api_base=api_base, timeout=timeout, ffprobe=ffprobe,
                        sleep=sleep, rand=rand)
    words_raw = _words_json(result.words)
    # The cache goes first, so a paid result survives a failed output write.
    _write_cache(cache_dir, [(audio_cache, result.audio), (words_cache, words_raw)])
    items = [(dest, result.audio)]
    if words_dest is not None:
        items.append((words_dest, words_raw))
    atomic_write_many(items)
    return result.words, False


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="grok_tts.py",
                description="Turn text into Grok (xAI) TTS audio with word timings.")
    p.add_argument("text", nargs="?", help="text to speak, or a path to a text file")
    p.add_argument("-o", "--out", help="output audio file (.wav or .mp3)")
    p.add_argument("--words", help="write word timings to this JSON file")
    p.add_argument("--lines", help="batch mode: JSON file of lines")
    p.add_argument("--out-dir", default="voiceover", help="batch output folder")
    p.add_argument("--project", default=".", help="project folder for batch mode")
    p.add_argument("--voice", default="eve")
    p.add_argument("--lang", default="en")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--normalize", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--list", action="store_true", help="list the available voices")
    return p


def _read_text(arg: str) -> str:
    """The text itself, or the contents of the UTF-8 file it names."""
    try:
        is_file = Path(arg).is_file()
    except OSError:  # e.g. text too long to be a file name
        return arg
    if not is_file:
        return arg
    try:
        return Path(arg).read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        raise UsageError(f"{arg} is not readable UTF-8 text; save it as UTF-8") from None


def _load_script(script: Path) -> list:
    try:
        data = json.loads(script.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UsageError(f"cannot read the lines file {script}: {exc.strerror or exc}") from None
    except ValueError as exc:
        raise UsageError(f"the lines file {script} is not valid JSON: {exc}") from None
    if not isinstance(data, list) or not data:
        raise UsageError("the lines file must be a non-empty JSON list")
    for i, item in enumerate(data):
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not isinstance(item.get("text"), str)):
            raise UsageError(
                f"line {i + 1} must be an object with a string \"id\" and a string \"text\"")
    return data


def _same_file(a: Path, b: Path) -> bool:
    """Whether two paths name one file or folder; case is ignored, as some folders ignore it."""
    return str(a.resolve()).casefold() == str(b.resolve()).casefold()


MAX_ID_BYTES = 200


def _check_line_id(line_id: str) -> None:
    plain = (line_id and line_id not in (".", "..")
             and not any(c in line_id for c in "/\\:")
             and not any(unicodedata.category(c) in ("Cc", "Cs") for c in line_id)
             and len(line_id.encode("utf-8")) <= MAX_ID_BYTES)
    if not plain:
        raise UsageError(
            f"line id {line_id!r} is not a plain file name (not empty, not . or .., "
            f"no / \\ or :, no control characters, at most {MAX_ID_BYTES} bytes)")


def run_batch(script: Path, out_dir: Path, project: Path, *, voice: str,
              lang: str, speed: float, normalize: bool, cache_dir: Path,
              use_cache: bool, key_provider: Callable[[], tuple],
              api_base: str = API_BASE, timeout: float = 300,
              sleep=time.sleep, rand=random.random) -> Path:
    """Speak every line of `script`; write `lines.json`; return its path."""
    data = _load_script(script)
    seen = {}
    texts = []
    for item in data:
        line_id = item["id"]
        _check_line_id(line_id)
        folded = unicodedata.normalize("NFC", line_id).casefold()
        if folded in seen:
            raise UsageError(
                f"line ids {seen[folded]!r} and {line_id!r} name the same file "
                "(ids must differ by more than case or Unicode form)")
        seen[folded] = line_id
        text = item["text"].strip()
        try:
            warning = validate_request(text, lang, speed)
        except UsageError as exc:
            raise UsageError(f"line {line_id!r}: {exc}") from None
        if warning:
            print(f"WARNING: {warning}", file=sys.stderr)
        texts.append(text)
    project_abs = project.resolve()
    out_abs = out_dir.resolve()
    try:
        rel = PurePosixPath(out_abs.relative_to(project_abs).as_posix())
    except ValueError:
        raise UsageError(
            f"--out-dir {out_dir} is outside the project folder {project}") from None
    if _same_file(out_abs, project_abs / "assets" / "voice"):
        raise UsageError(
            f"--out-dir {out_dir} is where media-use writes its own copies of the voice "
            "files, and it drops the lines whose takes live there; use the default voiceover/")
    out_abs.mkdir(parents=True, exist_ok=True)
    # A lines.json on disk always comes from a run in which every line succeeded.
    (out_abs / "lines.json").unlink(missing_ok=True)
    lines = []
    for item, text in zip(data, texts):
        line_id = item["id"]
        wav = out_abs / f"{line_id}.wav"
        words, cached = produce(
            text, wav, out_abs / f"{line_id}.words.json", voice=voice, lang=lang,
            speed=speed, normalize=normalize, cache_dir=cache_dir,
            use_cache=use_cache, key_provider=key_provider, api_base=api_base,
            timeout=timeout, ffprobe=None, sleep=sleep, rand=rand)
        print(f"{line_id}: {wav}" + (" (cached)" if cached else ""))
        lines.append({"id": line_id, "audio": str(rel / f"{line_id}.wav"),
                      "words": words})
    lines_path = out_abs / "lines.json"
    atomic_write_many([(lines_path, json.dumps(
        lines, indent=1, ensure_ascii=False).encode("utf-8"))])
    print(f"Lines saved to: {lines_path}")
    return lines_path


def _run(args, *, api_base, env, home, now, sleep, rand) -> int:
    if not (math.isfinite(args.timeout) and args.timeout > 0):
        raise UsageError(f"--timeout must be a number of seconds above 0, not {args.timeout:g}")
    key_provider = _key_provider(env, home, now)
    if args.list:
        if any([args.text, args.out, args.words, args.lines]):
            raise UsageError("--list cannot be combined with text, -o, --words or --lines")
        key, source = key_provider()
        for v in list_voices(key=key, source=source, api_base=api_base,
                             timeout=args.timeout, sleep=sleep, rand=rand):
            print(f"{v.get('voice_id', '')}\t{v.get('name', '')}\t{v.get('gender', '')}")
        return 0
    if args.lines is not None:
        if args.text or args.out or args.words:
            raise UsageError("--lines cannot be combined with text, -o or --words")
        project = Path(args.project)
        out_dir = Path(args.out_dir)
        run_batch(
            Path(args.lines), out_dir if out_dir.is_absolute() else project / out_dir,
            project, voice=args.voice, lang=args.lang, speed=args.speed,
            normalize=args.normalize, cache_dir=default_cache_dir(env, home),
            use_cache=not args.no_cache, key_provider=key_provider,
            api_base=api_base, timeout=args.timeout, sleep=sleep, rand=rand)
        return 0
    if not args.text or not args.out:
        raise UsageError("give the text and -o OUT.wav, or use --lines or --list")
    dest = Path(args.out)
    if dest.suffix.lower() not in (".wav", ".mp3"):
        raise UsageError("-o must end in .wav or .mp3")
    text = _read_text(args.text).strip()
    warning = validate_request(text, args.lang, args.speed)
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
    words_dest = Path(args.words) if args.words else None
    if words_dest is not None and _same_file(dest, words_dest):
        raise UsageError("-o and --words name the same file")
    for target in (dest, words_dest):
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
    _, cached = produce(
        text, dest, words_dest, voice=args.voice, lang=args.lang,
        speed=args.speed, normalize=args.normalize,
        cache_dir=default_cache_dir(env, home), use_cache=not args.no_cache,
        key_provider=key_provider, api_base=api_base, timeout=args.timeout,
        ffprobe=shutil.which("ffprobe"), sleep=sleep, rand=rand)
    absolute = dest.resolve()
    print(f"Audio saved to: {absolute}" + (" (cached)" if cached else ""))
    print(f"MEDIA:{absolute}")
    if words_dest is not None:
        print(f"Words saved to: {words_dest.resolve()}")
    return 0


def _report(exc: BaseException) -> None:
    """Print `ERROR: <message>`, then each note attached to the exception."""
    print(f"ERROR: {exc}", file=sys.stderr)
    for note in getattr(exc, "__notes__", ()):
        print(note, file=sys.stderr)


def main(argv: list | None = None, *, api_base: str = API_BASE,
         env: Mapping | None = None, home: Path | None = None,
         now: datetime | None = None, sleep=time.sleep,
         rand=random.random) -> int:
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    now = datetime.now(timezone.utc) if now is None else now
    try:
        args = _build_parser().parse_args(argv)
        return _run(args, api_base=api_base, env=env, home=home, now=now,
                    sleep=sleep, rand=rand)
    except GrokTTSError as exc:
        _report(exc)
        return exc.exit_code
    except OSError as exc:
        _report(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
