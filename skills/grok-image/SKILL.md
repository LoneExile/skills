---
name: grok-image
description: "Generate real raster images with xAI's Grok \"Imagine\" API over the xAI OAuth token from the grok CLI — no console API key, stdlib-only. Use when asked to generate/create an image, illustration, hero graphic, or photo — e.g. to embed in a slide/doc or deliver in chat."
---

# grok-image

Generate real raster images with xAI's Grok **Imagine** API using the xAI **OAuth** token from the **`grok` CLI** — no `xai-...` console API key, no third-party Python deps (stdlib `urllib` only).

## When to use
Whenever the user asks to generate / create an image, illustration, hero graphic, icon, or photo — including images to embed into a slide deck, doc, or webpage, or to deliver in chat.

## Prerequisites
- The [`grok` CLI](https://github.com/superagent-ai/grok-cli) (or any tool that writes `~/.grok/auth.json`) logged in, so a valid xAI **OAuth** token exists at `~/.grok/auth.json` (entries keyed `"<issuer>::<client_id>"`, access token under `key`). The `grok` CLI keeps it refreshed; the script reads it fresh on every call and prefers an unexpired entry.
- `python3` on PATH.
- Network access to `api.x.ai`.

## Usage
The script `grok_image.py` is bundled in this skill's directory. Run it with `python3`, using the path where the skill is installed (e.g. `~/.claude/skills/grok-image/grok_image.py`, `.agents/skills/grok-image/grok_image.py`, etc.):

```bash
python3 <this-skill-dir>/grok_image.py "A cozy architect office in New York at golden hour"
```

Options:
- `--model grok-imagine-image` — default, fast/cheap. `grok-imagine-image-quality` — best quality.
- `--out <dir-or-file>` — a directory (a timestamped filename is generated) **or** an explicit `.jpg`/`.jpeg`/`.png`/`.webp` path. Default: `~/generated-images/`.
- `--timeout 120` (seconds).

Embed straight into a project (e.g. put a Grok hero on a slide):
```bash
python3 <this-skill-dir>/grok_image.py \
  "minimal abstract tech nodes connected by flowing lines, single blue accent, no text" \
  --model grok-imagine-image-quality \
  --out ./assets/hero.jpg
```

On success it prints two lines — the human path and the delivery line:
```
Image saved to: /abs/path/to/image.jpg
MEDIA:/abs/path/to/image.jpg
```

## How it works
1. Loads the xAI OAuth access token from the grok CLI store (`~/.grok/auth.json`) **fresh on every call**, so token refreshes by the `grok` CLI are picked up automatically.
2. `POST https://api.x.ai/v1/images/generations` with `{"model", "prompt", "n":1}` and `Authorization: Bearer <token>`.
3. Downloads the returned image URL (with a browser `User-Agent`) and saves it locally; prints the absolute path.

## Gotchas
- Use the `grok-imagine-*` models. The OAuth token is **rejected** by the `grok-2-image*` "console" models — those require an `xai-...` key from console.x.ai.
- The image URL host (`imgen.x.ai`) **403s the default `Python-urllib` User-Agent**, so the script downloads with a browser `User-Agent`. Don't remove it.
- The token comes only from the **grok CLI** store (`~/.grok/auth.json` → `key`); there is no fallback. The `grok` CLI keeps it refreshed.
- `400/401/403 "Incorrect API key"` ⇒ no unexpired OAuth token in `~/.grok/auth.json`. Fix by logging in / refreshing the grok CLI (run `grok`), then retry.
- Imagine models ignore a `size`/dimension argument (sending `size` returns `400 "Argument not supported: size"`); aspect ratio comes from the prompt.
- This targets Grok's **Imagine** endpoint via the grok CLI's OAuth session (not an officially documented public API). It can change upstream, and generation is **billed to the xAI account** behind that token.
