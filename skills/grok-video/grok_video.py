#!/usr/bin/env python3
"""
grok-video skill (OMP).

Generate videos via the xAI Imagine video API using the xAI OAuth token from
the `grok` CLI (~/.grok/auth.json). Stdlib-only (urllib) -- no `requests` and
no `xai-...` console API key required.

The token is read FRESH on every invocation, so refreshes performed by the
grok CLI are picked up automatically.

Flow: POST /v1/videos/generations -> request_id -> poll GET /v1/videos/{id}
until status "done" -> download the resulting .mp4.
"""
import argparse
import base64
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.x.ai/v1/videos/generations"
STATUS_URL = "https://api.x.ai/v1/videos/{request_id}"
GROK_AUTH_JSON = Path.home() / ".grok" / "auth.json"
DEFAULT_OUT = Path.home() / "generated-videos"
VIDEO_EXT = ".mp4"
ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
RESOLUTIONS = ("480p", "720p", "1080p")
MODELS = ("grok-imagine-video-1.5", "grok-imagine-video")


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


def _request(url, token=None, payload=None, method=None, timeout=120):
    """urllib request helper; JSON body when payload is given, JSON response otherwise."""
    headers = {"User-Agent": "Mozilla/5.0"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        method = method or "POST"
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        return json.loads(body) if body else None


def _image_data_url(image):
    """Turn a local image path into a base64 data URL; pass URLs/data-URLs through."""
    if image.startswith(("http://", "https://", "data:")):
        return image
    p = Path(image).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"image file not found: {p}")
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _resolve_dest(out, prompt):
    if out:
        p = Path(out).expanduser()
        if p.suffix.lower() == VIDEO_EXT:
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        base = p
    else:
        base = DEFAULT_OUT
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() else "_" for c in prompt[:40]).strip("_") or "video"
    return base / f"{ts}_{safe}{VIDEO_EXT}"


def generate_video(prompt, model="grok-imagine-video-1.5", duration=8,
                   aspect_ratio="16:9", resolution="720p", image=None,
                   out=None, timeout=120, poll_interval=5, max_wait=600):
    """Generate a video; return the absolute saved path, or None on failure."""
    token = load_xai_token()
    if not token:
        return None

    payload = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    if image:
        try:
            payload["image"] = {"url": _image_data_url(image)}
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: {e}", file=sys.stderr)
            return None

    try:
        data = _request(API_URL, token=token, payload=payload, timeout=timeout)
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

    request_id = data.get("request_id") if isinstance(data, dict) else None
    if not request_id:
        print(f"ERROR: unexpected API response: {json.dumps(data)[:400]}", file=sys.stderr)
        return None
    print(f"Submitted generation request {request_id} (this can take a minute)...")

    url = STATUS_URL.format(request_id=request_id)
    last_progress = -1
    waited = 0
    while waited < max_wait:
        try:
            result = _request(url, token=token, timeout=timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            print(f"ERROR: poll HTTP {e.code}: {detail}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            print(f"ERROR polling: {e}", file=sys.stderr)
            return None

        status = result.get("status") if isinstance(result, dict) else None
        progress = result.get("progress")
        if isinstance(progress, int) and progress != last_progress:
            last_progress = progress
            print(f"  progress: {progress}%")

        if status == "done":
            video_url = (result.get("video") or {}).get("url")
            if not video_url:
                print(f"ERROR: done but no video url: {json.dumps(result)[:400]}", file=sys.stderr)
                return None
            break
        if status == "failed":
            err = result.get("error") or {}
            print(f"ERROR: generation failed: {err.get('code', '?')}: {err.get('message', '?')}",
                  file=sys.stderr)
            return None
        if status not in ("pending", "processing", None):
            print(f"ERROR: unexpected status {status!r}: {json.dumps(result)[:400]}", file=sys.stderr)
            return None

        time.sleep(poll_interval)
        waited += poll_interval
    else:
        print(f"ERROR: timed out after {max_wait}s waiting for generation.", file=sys.stderr)
        return None

    dest = _resolve_dest(out, prompt)
    try:
        dl_req = urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(dl_req, timeout=timeout) as r:
            dest.write_bytes(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"ERROR downloading video: {e}", file=sys.stderr)
        return None

    return str(dest.resolve())


def main():
    ap = argparse.ArgumentParser(
        description="Generate a video via the xAI Imagine API (OAuth token).")
    ap.add_argument("prompt", nargs="*", help="video prompt (optional with --image)")
    ap.add_argument("--model", default="grok-imagine-video-1.5", choices=MODELS,
                    help="grok-imagine-video-1.5 (default) | grok-imagine-video")
    ap.add_argument("--duration", type=int, default=8, choices=range(1, 16),
                    help="video length in seconds, 1-15 (default 8)")
    ap.add_argument("--aspect-ratio", default="16:9", choices=ASPECT_RATIOS,
                    help="default 16:9")
    ap.add_argument("--resolution", default="720p", choices=RESOLUTIONS,
                    help="480p | 720p (default) | 1080p")
    ap.add_argument("--image", default=None,
                    help="optional start image (local path, http(s) URL, or data URL) for image-to-video")
    ap.add_argument("--out", default=None,
                    help="output dir or file (.mp4). Default ~/generated-videos/")
    ap.add_argument("--timeout", type=int, default=120,
                    help="per-HTTP-request timeout in seconds (default 120)")
    ap.add_argument("--max-wait", type=int, default=600,
                    help="total poll budget in seconds (default 600)")
    args = ap.parse_args()

    prompt = " ".join(args.prompt).strip()
    if not prompt and not args.image:
        ap.error("a prompt is required unless --image is given")

    path = generate_video(prompt, model=args.model, duration=args.duration,
                          aspect_ratio=args.aspect_ratio, resolution=args.resolution,
                          image=args.image, out=args.out, timeout=args.timeout,
                          max_wait=args.max_wait)
    if path:
        print(f"Video saved to: {path}")
        print(f"MEDIA:{path}")
    else:
        print("Failed to generate video.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
