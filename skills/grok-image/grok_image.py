#!/usr/bin/env python3
"""
grok-image skill (OMP).

Generate images via the xAI Imagine API. The key is the xAI OAuth token from
the `grok` CLI (~/.grok/auth.json) when it is valid long enough, else
XAI_API_KEY / GROK_API_KEY from the environment or ~/.omp/.env. Stdlib-only.

The key is read FRESH on every invocation, so refreshes performed by the
grok CLI are picked up automatically.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

API_URL = "https://api.x.ai/v1/images/generations"
DEFAULT_OUT = Path.home() / "generated-images"
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


# --- key lookup (keep identical in grok_image.py and grok_video.py) ---
LOGIN_LABEL = "grok CLI login (~/.grok/auth.json)"


class KeyMissingError(Exception):
    """No usable xAI credential; the message lists each source and why it missed."""


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


def _login_entries(path: Path, now: datetime, min_valid_seconds: float):
    """(entries, reason): entries are (key, written, kind) in file order.

    kind is "long", "short" or "expired"; reason says why there are none.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], "file missing"
    except (OSError, ValueError) as e:
        return [], f"unreadable ({type(e).__name__})"
    values = data.values() if isinstance(data, dict) else []
    entries = []
    for entry in values:
        key = entry.get("key") if isinstance(entry, dict) else None
        if not isinstance(key, str) or not key:
            continue
        written = entry.get("expires_at")
        stamp = written
        if isinstance(written, str) and written.endswith("Z"):
            stamp = written[:-1] + "+00:00"  # fromisoformat reads Z only from Python 3.11
        try:
            expires = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            entries.append((key, written, "long"))  # missing or unparseable counts as usable
            continue
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > now + timedelta(seconds=min_valid_seconds):
            kind = "long"
        elif expires > now:
            kind = "short"
        else:
            kind = "expired"
        entries.append((key, written, kind))
    return entries, "no login entry"


def resolve_key(env: Mapping[str, str], home: Path, now: datetime,
                min_valid_seconds: float) -> tuple[str, str, str | None]:
    """(key, label, note) for the first usable credential; KeyMissingError if none."""
    entries, login_reason = _login_entries(
        Path(home) / ".grok" / "auth.json", now, min_valid_seconds)
    for key, _, kind in entries:
        if kind == "long":
            return key, LOGIN_LABEL, None
    short = next(((k, w) for k, w, kind in entries if kind == "short"), None)
    expired = next((w for _, w, kind in entries if kind == "expired"), None)
    if expired is not None:
        login_reason = f"expired at {expired}"

    def passed_over(label: str) -> str | None:
        if short is None:
            return None
        return (f"Note: the grok CLI login expires at {short[1]}, too soon for this run; "
                f"using {label}.")

    for name in ("XAI_API_KEY", "GROK_API_KEY"):
        value = env.get(name)
        if value:
            label = f"{name} (environment)"
            return value, label, passed_over(label)
    dotenv_reason = "no XAI_API_KEY or GROK_API_KEY line"
    dotenv = Path(home) / ".omp" / ".env"
    for name in ("XAI_API_KEY", "GROK_API_KEY"):
        try:
            value = read_dotenv_value(dotenv, name)
        except FileNotFoundError:
            dotenv_reason = "file missing"
            break
        except (OSError, ValueError) as e:
            dotenv_reason = f"unreadable ({type(e).__name__})"
            break
        if value:
            label = f"~/.omp/.env ({name})"
            return value, label, passed_over(label)
    if short is not None:
        return short[0], LOGIN_LABEL, (
            f"Note: the grok CLI login expires at {short[1]}, before this run may end; "
            "no API key found, so using it anyway.")
    raise KeyMissingError(
        "no xAI key found. Checked:\n"
        f"  - {LOGIN_LABEL}: {login_reason}\n"
        "  - XAI_API_KEY (environment): unset\n"
        "  - GROK_API_KEY (environment): unset\n"
        f"  - ~/.omp/.env: {dotenv_reason}\n"
        "Run `grok` to log in, set XAI_API_KEY, or add it to ~/.omp/.env.")
# --- end key lookup ---


# --- request safety (keep identical in grok_image.py and grok_video.py) ---
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


def _redact(text: str, key: str) -> str:
    return text.replace(key, "[redacted]") if key else text


def _error_text(raw: bytes, key: str) -> str:
    return _redact(raw.decode("utf-8", errors="replace"), key)[:400]


def _rejected(status: int, raw: bytes) -> bool:
    if status in (401, 403):
        return True
    return status == 400 and "api key" in raw.decode("utf-8", errors="replace").lower()


def _hint(label: str) -> str:
    if label == LOGIN_LABEL:
        return ("HINT: Run `grok` to log in again, or move ~/.grok/auth.json aside "
                "to use your API key.")
    return "HINT: Check the key at https://console.x.ai."


def _read_error(e: urllib.error.HTTPError) -> bytes:
    try:
        return e.read()
    except (OSError, http.client.HTTPException, ValueError):
        return b""
    finally:
        e.close()
# --- end request safety ---


def _resolve_dest(out, prompt):
    if out:
        p = Path(out).expanduser()
        if p.suffix.lower() in IMG_EXTS:
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        base = p
    else:
        base = DEFAULT_OUT
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() else "_" for c in prompt[:40]).strip("_") or "image"
    return base / f"{ts}_{safe}.jpg"


def generate_image(prompt, model="grok-imagine-image", out=None, timeout=120,
                   *, env=None, home=None, now=None, api_url=API_URL):
    """Generate an image; return the absolute saved path, or None on failure."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    now = datetime.now(timezone.utc) if now is None else now
    try:
        key, label, note = resolve_key(env, home, now, timeout + 60)
    except KeyMissingError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return None
    if note:
        print(note, file=sys.stderr)
    print(f"Key: {label}", file=sys.stderr)
    if not _usable_header_value(key):
        print(f"ERROR: the key from {label} is not a valid header value", file=sys.stderr)
        return None

    try:
        dest = _resolve_dest(out, prompt)
    except (OSError, RuntimeError) as e:
        print(f"ERROR: cannot write the output: {e}", file=sys.stderr)
        return None

    payload = {"model": model, "prompt": prompt, "n": 1}
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = _read_error(e)
        text = _error_text(raw, key)
        if _rejected(e.code, raw):
            print(f"ERROR: the key from {label} was rejected (HTTP {e.code}): {text}",
                  file=sys.stderr)
            print(_hint(label), file=sys.stderr)
        else:
            print(f"ERROR: xAI API HTTP {e.code}: {text}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"ERROR calling xAI API: {_redact(str(e), key)}", file=sys.stderr)
        return None

    try:
        image_url = data["data"][0]["url"]
    except (KeyError, IndexError, TypeError):
        print(f"ERROR: unexpected API response: {_redact(json.dumps(data), key)[:400]}", file=sys.stderr)
        return None

    try:
        dl_req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(dl_req, timeout=60) as r:
            dest.write_bytes(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"ERROR downloading image: {_redact(str(e), key)}", file=sys.stderr)
        return None

    return str(dest.resolve())


def main():
    ap = argparse.ArgumentParser(
        description="Generate an image via the xAI Imagine API (OAuth token).")
    ap.add_argument("prompt", nargs="+", help="image prompt")
    ap.add_argument("--model", default="grok-imagine-image",
                    choices=["grok-imagine-image", "grok-imagine-image-quality"],
                    help="grok-imagine-image (fast) | grok-imagine-image-quality (best)")
    ap.add_argument("--out", default=None,
                    help="output dir or file (.jpg/.png). Default ~/generated-images/")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    path = generate_image(" ".join(args.prompt), model=args.model,
                          out=args.out, timeout=args.timeout)
    if path:
        print(f"Image saved to: {path}")
        print(f"MEDIA:{path}")
    else:
        print("Failed to generate image.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
