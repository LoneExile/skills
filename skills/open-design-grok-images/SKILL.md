---
name: open-design-grok-images
description: "Embed REAL Grok-generated images into ANY Open Design artifact — prototype, live artifact, slide deck, media, or app mockup (responsive/desktop web, iOS, Android, tablet, desktop app) — by combining the Open Design MCP with the grok-image skill. Use whenever a design made in Open Design needs actual images (hero, section art, backgrounds, app screens) instead of placeholders or hand-drawn SVG."
---

# open-design-grok-images

Generate a design in **Open Design** (via its MCP) and embed **real raster images** produced by **Grok Imagine** (via the sibling [`grok-image`](../grok-image/) skill) — instead of placeholders, stock URLs, or hand-drawn SVG.

## Why this skill
Open Design's built-in media/image generation needs image-model credentials that are often not configured on a daemon (it falls back to hand-drawn SVG or fails). The `grok-image` skill sidesteps that: it generates images from the **grok CLI's OAuth token** and writes real `.jpg`/`.png` files, which you drop into the Open Design project's `assets/` and reference from the artifact.

## When to use
Whenever a design being made in **Open Design** needs real images — regardless of the project's type or target platform:
- Any **project type**: Prototype, Live artifact, Slide deck, From template, Media, or Other.
- Any **target platform**: Responsive/Desktop web, iOS app, Android app, Tablet app, Desktop app.
- An Open Design run produced placeholders or SVG stand-ins and you want real generated art.
- You need art-directed hero/section/background/app-screen imagery in an Open Design artifact.

Open Design renders every artifact as web tech — its files are HTML / JSX / CSS / JSON / SVG (an "iOS app" or "Android app" target adds device frames + interaction rules over the same HTML/JSX, not a native binary). So a generated image embeds the same way — write it into the project folder (conventionally an `assets/` subdir) and reference it with a relative path — whether the artifact is a landing page, a deck, or an app mockup.

## Prerequisites
- The **Open Design MCP** connected (tools: `create_project`, `start_run`, `get_run`, `get_project`, `list_files`, `get_file`, `write_file`, `get_artifact`).
- The **`grok-image`** skill installed (same repo — step 2 discovers its script across agent layouts). Install both:
  `npx skills add LoneExile/skills --skill grok-image --skill open-design-grok-images`
  It requires the [`grok` CLI](https://github.com/superagent-ai/grok-cli) logged in (xAI OAuth token at `~/.grok/auth.json`) and `python3`.

## Workflow (generate-first, single build — recommended)
Generating the images **before** the design run and having a **single** run embed them is simpler and cheaper than build-then-refine (one run, no placeholder round-trip). Verified end-to-end.

### 1. Create/select the project and locate its folder
- `create_project(name)` → note `id` (or use the active project).
- `get_project(project)` → `resolvedDir` is the on-disk folder; images go in `<resolvedDir>/assets/`. (Or read any file's `localPath` from `list_files`.)

### 2. Generate the images with grok-image
Find the `grok-image` script — the install layout varies by agent, so probe in order and use the first that exists; if none, install it (`npx skills add LoneExile/skills --skill grok-image`) and retry:

```bash
for c in \
  ~/.omp/agent/managed-skills/grok-image/grok_image.py \
  ~/.claude/skills/grok-image/grok_image.py \
  ~/.config/opencode/skills/grok-image/grok_image.py \
  ~/.agents/skills/grok-image/grok_image.py \
  ~/.pi/agent/skills/grok-image/grok_image.py; do
  [ -f "$c" ] && GROK_IMG="$c" && break
done
[ -z "$GROK_IMG" ] && GROK_IMG="$(find ~ -name grok_image.py -path '*grok-image*' 2>/dev/null | head -1)"
```

Then generate straight into the project's assets:

```bash
python3 "$GROK_IMG" "<art-directed prompt matching the design; say 'no text' for backdrops>" \
  --model grok-imagine-image-quality \
  --out "<resolvedDir>/assets/hero.jpg"
```

- `grok-imagine-image-quality` for hero art, `grok-imagine-image` for quick/section art.
- Imagine models take **no size arg**; Grok typically returns ~3:4 (864×1152) or ~4:3 (1152×864). Plan `object-fit: cover` frames for mixed aspects.
- Repeat per image (`hero.jpg`, `sample-1.jpg`, …).

### 3. Commission the design — embed in one pass
- `start_run(project, agent:"claude", plugin|skill, prompt)`. In the prompt, **name the exact files already in `assets/` and their aspect ratios**, and instruct: embed them with relative `src` + `object-fit: cover` framing; **do NOT generate new images, use placeholders, or external URLs.**
- Poll `get_run(runId)` every 30–90s until `status: succeeded` (~5–30 min). Empty text_delta = the inner agent thinking, not a hang.

### 4. Alternative — reserve-then-embed
If images can't be ready before the build: run the build with named `<img src="assets/<name>.jpg">` slots, generate (step 2), then embed via a **refine run** (`start_run(project, "Embed the images already in assets/ (…); no new generation, no external URLs")`) or directly with `get_file` → `write_file`.

### 5. Verify
- `list_files` + grep the entry HTML for each `assets/*.jpg` ref and the slide/screen count.
- Open `previewUrl` in a browser and screenshot. **Verify at a real 1920×1080 viewport** — Open Design's PNG export can clip at odd widths even when the live layout is correct.

## Gotchas
- **Relative `src` only.** Reference images as `assets/<name>.jpg` (relative to the entry HTML), never an absolute host path or `http(s)://` URL — keeps the artifact portable/exportable.
- **Don't rely on Open Design's own image generation** for this — it needs image-model credentials that are frequently absent (it will fall back to SVG or error). That is the whole reason to use `grok-image`.
- **grok-image needs the grok CLI logged in.** `400/401/403 "Incorrect API key"` from the generator ⇒ run `grok` to refresh; see the `grok-image` skill's own gotchas.
- **Aspect ratio** comes from the prompt (Imagine models take no size arg); Grok typically returns ~3:4 (864×1152) or ~4:3 (1152×864). Use `object-fit: cover` in fixed-size frames to fill without distortion (crops edges), or `contain` to letterbox without cropping.
- **Cost:** each image is billed to the xAI account behind the grok CLI token.
