---
name: grok-video
description: "Generate real MP4 videos with xAI's Grok Imagine video API. Signs in with the `grok` CLI's login, or else an xAI API key (`XAI_API_KEY`, `GROK_API_KEY` or `~/.omp/.env`); stdlib-only. Use when asked to generate/create a video, short clip, or animated scene — e.g. a hero-loop, product b-roll, or motion test to embed or deliver in chat."
---

# grok-video

Generate real MP4 videos with xAI's Grok **Imagine** video API. It signs in with the **`grok` CLI** login when that login is valid long enough, else with an xAI API key (`XAI_API_KEY` / `GROK_API_KEY` from the environment or `~/.omp/.env`). No third-party Python deps (stdlib `urllib` only).

## When to use
Whenever the user asks to generate / create a video, short clip, or animated scene — including a hero-loop for a page, product b-roll, or a motion test to embed in a doc or deliver in chat. Also supports **image-to-video** (start from an image).

## Prerequisites
- Either the [`grok` CLI](https://github.com/superagent-ai/grok-cli) logged in (it keeps `~/.grok/auth.json` fresh), or an xAI API key. A machine where nobody runs `grok` needs only the key.
- `python3` 3.9 or newer on PATH.
- Network access to `api.x.ai` (generation is async: submit, then poll, then download).

## Key setup

Run the script and let it find the credential itself. It checks, in order, and the first source that has a credential wins (a rejected one is not followed by the next source):

1. the `grok` CLI login in `~/.grok/auth.json`, if it stays valid for the whole run (see the minimum lifetime below)
2. environment `XAI_API_KEY`
3. environment `GROK_API_KEY`
4. `~/.omp/.env`: an `XAI_API_KEY` line, then a `GROK_API_KEY` line
5. last resort: a login that is valid but too short, used only when no key exists (the script prints a note)

The `.env` format is one `NAME=value` line. `export` and matching quotes are fine. If a name appears twice, the last line wins (as in OMP). Put no trailing `# comment` on the line: `XAI_API_KEY=xai-1 # note` sends `xai-1 # note` as the key. OMP does not export `~/.omp/.env` to tool shells, so the script reads it directly. A project `.env` is never read.

**Minimum login lifetime:** `--max-wait` + `--timeout` + 60 s, so 780 s (13 minutes) by default. A login that expires sooner is passed over for a key when one exists, and the run prints `Note: the grok CLI login expires at <ts>, too soon for this run; using <source>.` To make the run use the login, refresh it (run `grok`) or lower `--max-wait` / `--timeout` so the login covers the run, but keep `--max-wait` above the generation time (about 30-90 s): a run that times out after the submit may still be billed. With no key found, a too-short login is used anyway and the note reads `Note: the grok CLI login expires at <ts>, before this run may end; no API key found, so using it anyway.`

**Which credential, who pays:** every run prints `Key: <source>` on stderr before the first request (keep stderr to see it). Generation is billed to the xAI account behind the credential that line names; an API key bills that account's API credits. If that credential is rejected, the run stops, names its source and the fix, and no other source is tried.

Never read, print, export or paste a key or a credential file, and never put a key on a command line. If nothing is found, the script prints `ERROR: no xAI key found` and lists what it checked.

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
- `--timeout 120` — per-request timeout (seconds). `--max-wait 600` — total wait in seconds, wall clock from the submit.

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
1. Picks the credential as described in Key setup, **fresh on every call**, so a refreshed `grok` login is picked up automatically. Prints `Key: <source>` (and a note, if any) on stderr.
2. `POST https://api.x.ai/v1/videos/generations` with `{"model", "prompt", "duration", "aspect_ratio", "resolution"}` (plus `image` for I2V) and `Authorization: Bearer <key>`; returns a `request_id`.
3. Polls `GET https://api.x.ai/v1/videos/{request_id}` until `status: "done"` (prints progress %), then downloads the returned `video.url` (with a browser `User-Agent`) and saves it locally; prints the absolute path.

## Gotchas
- Use the `grok-imagine-video*` models. Other "console" models may reject the `grok` CLI login.
- The video URL host (`vidgen.x.ai`) can **403 the default `Python-urllib` User-Agent**, so the script downloads with a browser `User-Agent`. Don't remove it.
- A rejected credential (HTTP 401/403, or 400 "api key") stops the run with `ERROR: the key from <source> was rejected` plus a hint (run `grok` for the login; check the key at console.x.ai for a key). No other source is tried.
- Generation is **asynchronous** — the script polls for up to `--max-wait` (default 600s); a 5–8s 720p clip typically takes ~30–90s.
- This targets Grok's **Imagine** endpoint (not an officially documented public API) and can change upstream. Generation is **billed to the xAI account** behind whichever credential the `Key:` line names.
- If the key is rejected while polling, or `--max-wait` runs out, the video was already submitted and may still be billed; its id is on the `Submitted generation request <id>` line on stdout, so keep stdout.
