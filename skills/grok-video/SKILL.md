---
name: grok-video
description: "Generate real MP4 videos with xAI's Grok Imagine video API over the xAI OAuth token from the grok CLI — no console API key, stdlib-only. Use when asked to generate/create a video, short clip, or animated scene — e.g. a hero-loop, product b-roll, or motion test to embed or deliver in chat."
---

# grok-video

Generate real MP4 videos with xAI's Grok **Imagine** video API using the xAI **OAuth** token from the **`grok` CLI** — no `xai-...` console API key, no third-party Python deps (stdlib `urllib` only).

## When to use
Whenever the user asks to generate / create a video, short clip, or animated scene — including a hero-loop for a page, product b-roll, or a motion test to embed in a doc or deliver in chat. Also supports **image-to-video** (start from an image).

## Prerequisites
- The [`grok` CLI](https://github.com/superagent-ai/grok-cli) (or any tool that writes `~/.grok/auth.json`) logged in, so a valid xAI **OAuth** token exists at `~/.grok/auth.json` (entries keyed `"<issuer>::<client_id>"`, access token under `key`). The `grok` CLI keeps it refreshed; the script reads it fresh on every call and prefers an unexpired entry.
- `python3` on PATH.
- Network access to `api.x.ai` (generation is async: submit, then poll, then download).

## Usage
The script `grok_video.py` is bundled in this skill's directory. Run it with `python3`, using the path where the skill is installed (e.g. `~/.claude/skills/grok-video/grok_video.py`, `.agents/skills/grok-video/grok_video.py`, etc.):

```bash
python3 <this-skill-dir>/grok_video.py "A red apple rolling across a wooden table, soft morning light"
```

Options:
- `--model grok-imagine-video-1.5` — default. `grok-imagine-video` — alternate.
- `--duration 8` — seconds, 1–15 (default 8).
- `--aspect-ratio 16:9` — also `1:1`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`.
- `--resolution 720p` — `480p` (fastest/cheapest), `720p` (default), `1080p`.
- `--image <path-or-url>` — optional start image for image-to-video (local file, http(s) URL, or data URL). Prompt becomes optional when an image is given.
- `--out <dir-or-file>` — a directory (a timestamped filename is generated) **or** an explicit `.mp4` path. Default: `~/generated-videos/`.
- `--timeout 120` — per-request timeout (seconds). `--max-wait 600` — total poll budget.

Image-to-video (start from a local image):
```bash
python3 <this-skill-dir>/grok_video.py "the apple starts rolling" --image ./apple.jpg --duration 5 --resolution 480p
```

On success it prints two lines — the human path and the delivery line:
```
Video saved to: /abs/path/to/video.mp4
MEDIA:/abs/path/to/video.mp4
```

## How it works
1. Loads the xAI OAuth access token from the grok CLI store (`~/.grok/auth.json`) **fresh on every call**, so token refreshes by the `grok` CLI are picked up automatically.
2. `POST https://api.x.ai/v1/videos/generations` with `{"model", "prompt", "duration", "aspect_ratio", "resolution"}` (plus `image` for I2V) and `Authorization: Bearer <token>`; returns a `request_id`.
3. Polls `GET https://api.x.ai/v1/videos/{request_id}` until `status: "done"` (prints progress %), then downloads the returned `video.url` (with a browser `User-Agent`) and saves it locally; prints the absolute path.

## Gotchas
- Use the `grok-imagine-video*` models. Like the image endpoint, the OAuth token may be **rejected** by other "console" models — those require an `xai-...` key from console.x.ai.
- The video URL host (`vidgen.x.ai`) can **403 the default `Python-urllib` User-Agent**, so the script downloads with a browser `User-Agent`. Don't remove it.
- The token comes only from the **grok CLI** store (`~/.grok/auth.json` → `key`); there is no fallback. The `grok` CLI keeps it refreshed.
- `400/401/403 "Incorrect API key"` ⇒ no unexpired OAuth token in `~/.grok/auth.json`. Fix by logging in / refreshing the grok CLI (run `grok`), then retry.
- Generation is **asynchronous** — the script polls for up to `--max-wait` (default 600s); a 5–8s 720p clip typically takes ~30–90s.
- This targets Grok's **Imagine** endpoint via the grok CLI's OAuth session (not an officially documented public API). It can change upstream, and generation is **billed to the xAI account** behind that token.
