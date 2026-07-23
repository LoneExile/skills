#!/usr/bin/env python3
"""
grok-image skill (OMP).

Generate images via the xAI Imagine API using the xAI OAuth token from the
`grok` CLI (~/.grok/auth.json). Stdlib-only (urllib) -- no `requests` and no
`xai-...` console API key required.

The token is read FRESH on every invocation, so refreshes performed by the
grok CLI are picked up automatically.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.x.ai/v1/images/generations"
GROK_AUTH_JSON = Path.home() / ".grok" / "auth.json"
DEFAULT_OUT = Path.home() / "generated-images"
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _expired(iso):
    """Best-effort: True if an ISO-8601 timestamp is in the past."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001
        return False


def _token_from_grok_cli():
    """xAI access token from the grok CLI store (~/.grok/auth.json).

    Entries are keyed by "<issuer>::<client_id>"; the access token is under "key".
    Prefer an unexpired entry, else fall back to any entry with a token.
    """
    if not GROK_AUTH_JSON.exists():
        return None
    try:
        data = json.loads(GROK_AUTH_JSON.read_text())
    except Exception:  # noqa: BLE001
        return None
    fallback = None
    for entry in (data.values() if isinstance(data, dict) else []):
        if isinstance(entry, dict) and entry.get("key"):
            exp = entry.get("expires_at")
            if not exp or not _expired(exp):
                return entry["key"]
            fallback = fallback or entry["key"]
    return fallback


def load_xai_token():
    """Load the xAI OAuth token from the grok CLI store (~/.grok/auth.json), fresh each call."""
    tok = _token_from_grok_cli()
    if tok:
        return tok
    print(
        "ERROR: no xAI OAuth token found in ~/.grok/auth.json.\n"
        "       Log in with the grok CLI (run `grok`), then retry.",
        file=sys.stderr,
    )
    return None


def _post_json(url, token, payload, timeout):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


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


def generate_image(prompt, model="grok-imagine-image", out=None, timeout=120):
    """Generate an image; return the absolute saved path, or None on failure."""
    token = load_xai_token()
    if not token:
        return None

    payload = {"model": model, "prompt": prompt, "n": 1}

    try:
        data = _post_json(API_URL, token, payload, timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        print(f"ERROR: xAI API HTTP {e.code}: {detail}", file=sys.stderr)
        if e.code in (400, 401, 403) and "api key" in detail.lower():
            print("HINT: the OAuth token is missing/expired -- refresh the grok CLI (run `grok`) and retry.",
                  file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"ERROR calling xAI API: {e}", file=sys.stderr)
        return None

    try:
        image_url = data["data"][0]["url"]
    except (KeyError, IndexError, TypeError):
        print(f"ERROR: unexpected API response: {json.dumps(data)[:400]}", file=sys.stderr)
        return None

    dest = _resolve_dest(out, prompt)
    try:
        dl_req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(dl_req, timeout=60) as r:
            dest.write_bytes(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"ERROR downloading image: {e}", file=sys.stderr)
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
