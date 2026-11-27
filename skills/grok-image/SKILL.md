---
name: grok-image
description: "Generate real raster images with xAI's Grok \"Imagine\" API. Signs in with the `grok` CLI's login, or else an xAI API key (`XAI_API_KEY`, `GROK_API_KEY` or `~/.omp/.env`); stdlib-only. Use when asked to generate/create an image, illustration, hero graphic, or photo — e.g. to embed in a slide/doc or deliver in chat."
---

# grok-image

Generate real raster images with xAI's Grok **Imagine** API. It signs in with the **`grok` CLI** login when that login is valid long enough, else with an xAI API key (`XAI_API_KEY` / `GROK_API_KEY` from the environment or `~/.omp/.env`). No third-party Python deps (stdlib `urllib` only).

## When to use
Whenever the user asks to generate / create an image, illustration, hero graphic, icon, or photo — including images to embed into a slide deck, doc, or webpage, or to deliver in chat.

## Prerequisites
- Either the [`grok` CLI](https://github.com/superagent-ai/grok-cli) logged in (it keeps `~/.grok/auth.json` fresh), or an xAI API key. A machine where nobody runs `grok` needs only the key.
- `python3` 3.9 or newer on PATH.
- Network access to `api.x.ai`.

## Key setup

Run the script and let it find the credential itself. It checks, in order, and the first source that works wins:

1. the `grok` CLI login in `~/.grok/auth.json`, if it stays valid for the whole run (see the minimum lifetime below)
2. environment `XAI_API_KEY`
3. environment `GROK_API_KEY`
4. `~/.omp/.env`: an `XAI_API_KEY` line, then a `GROK_API_KEY` line
5. last resort: a login that is valid but too short, used only when no key exists (the script prints a note)

The `.env` format is one `NAME=value` line. `export` and matching quotes are fine. If a name appears twice, the last line wins (as in OMP). Put no trailing `# comment` on the line: `XAI_API_KEY=xai-1 # note` sends `xai-1 # note` as the key. OMP does not export `~/.omp/.env` to tool shells, so the script reads it directly. A project `.env` is never read.

**Minimum login lifetime:** `--timeout` + 60 s, so 180 s by default. A login that expires sooner is passed over for a key when one exists, and the run prints `Note: the grok CLI login expires at <ts>, too soon for this run; using <source>.` To make the run use the login, refresh it (run `grok`) or lower `--timeout` so the login covers the run. With no key found, a too-short login is used anyway and the note reads `Note: the grok CLI login expires at <ts>, before this run may end; no API key found, so using it anyway.`

**Which credential, who pays:** every run prints `Key: <source>` on stderr before the first request (keep stderr to see it). Generation is billed to the xAI account behind the credential that line names; an API key bills that account's API credits. If that credential is rejected, the run stops, names its source and the fix, and no other source is tried.

Never read, print, export or paste a key or a credential file, and never put a key on a command line. If nothing is found, the script prints `ERROR: no xAI key found` and lists what it checked.

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
1. Picks the credential as described in Key setup, **fresh on every call**, so a refreshed `grok` login is picked up automatically. Prints `Key: <source>` (and a note, if any) on stderr.
2. `POST https://api.x.ai/v1/images/generations` with `{"model", "prompt", "n":1}` and `Authorization: Bearer <key>`.
3. Downloads the returned image URL (with a browser `User-Agent`) and saves it locally; prints the absolute path.

## Gotchas
- Use the `grok-imagine-*` models. The older `grok-2-image*` "console" models may reject the `grok` CLI login.
- The image URL host (`imgen.x.ai`) **403s the default `Python-urllib` User-Agent**, so the script downloads with a browser `User-Agent`. Don't remove it.
- A rejected credential (HTTP 401/403, or 400 "api key") stops the run with `ERROR: the key from <source> was rejected` plus a hint (run `grok` for the login; check the key at console.x.ai for a key). No other source is tried.
- Imagine models ignore a `size`/dimension argument (sending `size` returns `400 "Argument not supported: size"`); aspect ratio comes from the prompt.
- This targets Grok's **Imagine** endpoint (not an officially documented public API) and can change upstream. Generation is **billed to the xAI account** behind whichever credential the `Key:` line names.
