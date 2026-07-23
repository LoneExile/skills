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
- The sibling **`grok-image`** skill installed (same repo). Install both:
  `npx skills add LoneExile/skills --skill grok-image --skill open-design-grok-images`
  It requires the [`grok` CLI](https://github.com/superagent-ai/grok-cli) logged in (xAI OAuth token at `~/.grok/auth.json`) and `python3`.

## Workflow

### 1. Create or select the project
- `create_project(name)` → note the returned `id`, or use the active project.

### 2. Commission the design
- `start_run(project, prompt, plugin|skill, agent)` — pick a slide/page plugin (e.g. `example-simple-deck`) or describe the artifact. In the prompt, **reserve image slots**: tell the agent to leave a clearly-named `<img src="assets/<name>.jpg">` slot (or a positioned container) for each image, rather than generating art itself.
- Poll `get_run(runId)` every 30–60s until `status: succeeded` (runs take ~5–30 min).

### 3. Locate the project directory
- `get_project(project)` → `resolvedDir` is the on-disk project folder. (Or read any file's `localPath` from `list_files(project)`.) Images go in `<resolvedDir>/assets/`.

### 4. Generate each image with grok-image
Locate the `grok-image` script — it is a **sibling skill**, installed in the same skills directory as this one, at `../grok-image/grok_image.py` relative to this skill's folder (e.g. `~/.claude/skills/grok-image/grok_image.py`). If it is missing, the dependency was not installed — install it and retry:

```bash
npx skills add LoneExile/skills --skill grok-image
```

Then run it, writing straight into the project's assets:

```bash
python3 "<this-skill-dir>/../grok-image/grok_image.py" \
  "<art-directed prompt matching the design's palette/aspect>" \
  --model grok-imagine-image-quality \
  --out "<resolvedDir>/assets/hero.jpg"
```

- Use `--model grok-imagine-image-quality` for hero art, `grok-imagine-image` for quick/section art.
- Match the design: describe palette, style, and "no text" for backdrops. Imagine models ignore `size`; state aspect intent in the prompt.
- Repeat per image slot (`hero.jpg`, `section-1.jpg`, …).

### 5. Embed the images
Two options:
- **Direct edit (fast, precise):** `get_file` the entry HTML, then `write_file` it back with each slot pointing at its relative asset (`src="assets/hero.jpg"`), adding `object-fit`/sizing CSS so images fit without clipping.
- **Refine run (agent does the layout):** `start_run(project, "Embed the already-generated local images in assets/ (hero.jpg, section-1.jpg, …) into the artifact with tasteful sizing; do not generate new images or use external URLs.")` and poll.

### 6. Verify
- `get_artifact(project)` and open the `previewUrl` from the succeeded run. Confirm each `assets/*.jpg` renders and nothing clips at target sizes.

## Gotchas
- **Relative `src` only.** Reference images as `assets/<name>.jpg` (relative to the entry HTML), never an absolute host path or `http(s)://` URL — keeps the artifact portable/exportable.
- **Don't rely on Open Design's own image generation** for this — it needs image-model credentials that are frequently absent (it will fall back to SVG or error). That is the whole reason to use `grok-image`.
- **grok-image needs the grok CLI logged in.** `400/401/403 "Incorrect API key"` from the generator ⇒ run `grok` to refresh; see the `grok-image` skill's own gotchas.
- **Aspect ratio** comes from the prompt (Imagine models take no size arg); Grok often returns portrait/near-square, so add sizing CSS (`object-fit: contain`) to slots that expect landscape.
- **Cost:** each image is billed to the xAI account behind the grok CLI token.
