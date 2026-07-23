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

### grok-image

Generates images with `grok-imagine-image` / `grok-imagine-image-quality` and saves a local file, printing a `MEDIA:<path>` line for native delivery/embedding.

- **Requires:** the [`grok` CLI](https://github.com/superagent-ai/grok-cli) logged in (it stores an xAI OAuth token at `~/.grok/auth.json`, which the skill reads fresh each call), plus `python3`.
- **Note:** this targets Grok's Imagine endpoint via the grok CLI's OAuth session — not an officially documented public API. It can change upstream, and image generation is **billed to the xAI account** behind that token.

```bash
python3 <skill-dir>/grok_image.py "a minimal isometric server room, blue accent" --out ./assets/hero.jpg
```

## Layout

```
skills/
  grok-image/
    SKILL.md        # instructions + frontmatter (name, description)
    grok_image.py   # bundled helper script
```

Each skill is a directory under `skills/` containing a `SKILL.md`; supporting files live alongside it and are installed with the skill.

## License

[MIT](LICENSE)
