# skills

[![Validate](https://github.com/LoneExile/skills/actions/workflows/validate.yml/badge.svg)](https://github.com/LoneExile/skills/actions/workflows/validate.yml)
[![SkillSpector](https://github.com/LoneExile/skills/actions/workflows/skillspector.yml/badge.svg)](https://github.com/LoneExile/skills/actions/workflows/skillspector.yml)
[![Security: SkillSpector](https://img.shields.io/badge/security-SkillSpector-76B900?logo=nvidia&logoColor=white)](https://github.com/NVIDIA/SkillSpector)
[![Latest release](https://img.shields.io/github/v/release/LoneExile/skills?sort=semver&display_name=tag)](https://github.com/LoneExile/skills/releases/latest)
[![License: MIT](https://img.shields.io/github/license/LoneExile/skills)](LICENSE)
[![Install](https://img.shields.io/badge/install-npx%20skills%20add-CB3837?logo=npm&logoColor=white)](https://github.com/vercel-labs/skills)

Reusable [Agent Skills](https://agentskills.io) for AI coding agents (Claude Code, Cursor, Codex, OpenCode, Pi, and [70+ more](https://github.com/vercel-labs/skills#supported-agents)).

Install any skill here with the [`skills` CLI](https://github.com/vercel-labs/skills):

```bash
# install everything in this repo
npx skills add LoneExile/skills

# list what's available first
npx skills add LoneExile/skills --list

# install one skill
npx skills add LoneExile/skills --skill grok-image

# install to a specific agent (e.g. Claude Code), globally
npx skills add LoneExile/skills --skill grok-image -a claude-code -g
```

No npm publish or registry step is involved — this GitHub repo *is* the source. `npx skills update` pulls the latest.

## Skills

| Skill | Description |
| ----- | ----------- |
| [`grok-image`](skills/grok-image/) | Generate real raster images via xAI's Grok **Imagine** API using the `grok` CLI's OAuth token — no console API key, stdlib-only Python. |
| [`grok-video`](skills/grok-video/) | Generate real MP4 videos via xAI's Grok **Imagine** video API using the `grok` CLI's OAuth token — no console API key, stdlib-only Python. |
| [`highlighted-source-reference-cards`](skills/highlighted-source-reference-cards/) | Turn vendor docs and PDF pages into browser-style **reference cards** with pink highlights on the exact lines that back a number, plus the URL bar and capture date. Zero-dependency Node + headless Chrome; poppler for PDFs. |
| [`terminal-demo-video`](skills/terminal-demo-video/) | Record a real terminal session (CLI/TUI) to a looping GIF + MP4 via tmux + asciinema + agg — captures real keystrokes and Alt/Ctrl chords that VHS drops. |

### grok-image

Generates images with `grok-imagine-image` / `grok-imagine-image-quality` and saves a local file, printing a `MEDIA:<path>` line for native delivery/embedding.

- **Requires:** the [`grok` CLI](https://github.com/superagent-ai/grok-cli) logged in (it stores an xAI OAuth token at `~/.grok/auth.json`, which the skill reads fresh each call), plus `python3`.
- **Note:** this targets Grok's Imagine endpoint via the grok CLI's OAuth session — not an officially documented public API. It can change upstream, and image generation is **billed to the xAI account** behind that token.

```bash
python3 <skill-dir>/grok_image.py "a minimal isometric server room, blue accent" --out ./assets/hero.jpg
```

### grok-video

Generates videos with `grok-imagine-video-1.5` / `grok-imagine-video` and saves a local MP4, printing a `MEDIA:<path>` line for native delivery/embedding. Also supports **image-to-video** via `--image`.

- **Requires:** the [`grok` CLI](https://github.com/superagent-ai/grok-cli) logged in (it stores an xAI OAuth token at `~/.grok/auth.json`, which the skill reads fresh each call), plus `python3`.
- **Note:** this targets Grok's Imagine endpoint via the grok CLI's OAuth session — not an officially documented public API. It can change upstream, and video generation is **billed to the xAI account** behind that token. Generation is asynchronous (submit → poll → download).

```bash
python3 <skill-dir>/grok_video.py "A red apple rolling across a wooden table, soft light" \
  --duration 5 --resolution 480p --out ./assets/demo.mp4
```

### highlighted-source-reference-cards

Turns the sources behind a bid, BOM or diagram into one PNG per source: a header (Reference N of M, capture date, what the source supports), a browser-style URL bar showing the address that was actually loaded, the page crop with **pink highlights on the exact phrases**, and a footer saying the highlights were added. Works on web pages and PDF pages, including PDF tables.

- **Requires:** Node 20+, Google Chrome / Chromium / Edge / Brave (driven headless over a pipe, so no debugging port is opened), plus [poppler](https://poppler.freedesktop.org/) for PDFs (macOS: `brew install poppler`).
- **Spec-driven:** a JSON spec lists each source with its exact phrases and crop anchors. `probe` checks that every highlight matches before `build` writes the cards, and a card with a missing highlight is never written. `example.json` covers two web pages and a PDF table.
- **One card at a time:** `--only KEY` probes or builds a single card, and `probe --lines` prints a PDF page's text lines with their coordinates. PDF crops can start or end at a phrase (`{ "text": "Table 65" }`) and never cut through a line of text. The probe flags redirects and PDF text that appears on more than one page.

```bash
node <skill-dir>/refcards.mjs probe spec.json                     # check every highlight, write nothing
node <skill-dir>/refcards.mjs probe spec.json --only KEY --lines  # one card; for a PDF, print its text lines
node <skill-dir>/refcards.mjs build spec.json --out ./references
```

### terminal-demo-video

Records a real terminal session to a looping **GIF** + **MP4**. A bundled `record.sh` drives the program inside **tmux** (so real keystrokes and modifier chords like **`Alt+G`** actually reach it — the ones VHS/ttyd drop), records with **asciinema**, and renders with **agg** + **ffmpeg**. Human-paced typing, readable pauses, and a clean final frame are built in.

- **Requires:** `tmux`, `asciinema`, `agg`, `ffmpeg` (macOS: `brew install tmux asciinema agg ffmpeg`), plus a monospace/Nerd font for rendering.
- **Actions** (`type` / `paste` / `key` / `sleep`) come from a file or stdin, so a demo is fully scripted and reproducible.

```bash
<skill-dir>/record.sh --cmd "omp --no-session --no-title" \
  --out ./assets/demo --boot 16 --actions demo.actions
```

## Layout

```
skills/
  grok-image/
    SKILL.md        # instructions + frontmatter (name, description)
    grok_image.py   # bundled helper script
  grok-video/
    SKILL.md        # instructions + frontmatter (name, description)
    grok_video.py   # bundled helper script (async submit + poll + download)
  highlighted-source-reference-cards/
    SKILL.md        # instructions + frontmatter (name, description)
    refcards.mjs    # bundled capture + card script (Node, headless Chrome, poppler)
    example.json    # example spec: two web pages and a PDF table
  terminal-demo-video/
    SKILL.md        # instructions + frontmatter (name, description)
    record.sh       # bundled recorder (tmux + asciinema + agg)
```

Each skill is a directory under `skills/` containing a `SKILL.md`; supporting files live alongside it and are installed with the skill.

## License

[MIT](LICENSE)
