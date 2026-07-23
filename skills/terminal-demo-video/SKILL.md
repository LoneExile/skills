---
name: terminal-demo-video
description: "Record a real terminal session (CLI or full-screen TUI) to a polished, looping GIF + MP4, driven through tmux so real keystrokes and modifier chords (Enter, Ctrl+_, Alt+_) land in the program — the ones VHS/ttyd silently drop. Use when asked to make a terminal demo, screencast, or README/package-gallery GIF, especially one that presses hotkeys or drives an interactive app."
---

# terminal-demo-video

Record a **real** terminal session to a clean GIF **and** MP4. A bundled script drives the program inside **tmux** (so genuine keystrokes and modifier chords reach it), records with **asciinema**, and renders with **agg** (+ **ffmpeg** for the MP4). Idle gaps are compressed on render, so slow startups stay watchable.

## When to use

- You need a demo GIF/MP4 of a CLI or TUI for a README, docs, or a package gallery (npm, pi.dev, etc.).
- **Especially** when the demo must press real keys — `Enter`, `Ctrl+U`, **`Alt+G`**, arrows — or drive an interactive full-screen app. VHS records inside ttyd/xterm.js, which **drops the Alt/Meta modifier and can't reproduce those chords**; a `send-keys M-g` here delivers a true `alt+g` the program actually receives.
- You want one tool that yields both a looping GIF (for READMEs) and an MP4 (for galleries that autoplay video).

## When NOT to use

- A purely non-interactive, scripted command sequence with no modifier chords — plain [VHS](https://github.com/charmbracelet/vhs) `.tape` is simpler. Reach for this skill when you need real keystroke/TUI fidelity.

## Prerequisites

- `tmux`, `asciinema`, `agg`, `ffmpeg` on `PATH`. macOS: `brew install tmux asciinema agg ffmpeg`.
- A monospace font. If the recorded app draws glyph icons (powerline / devicons / Nerd-Font symbols), install and pass a **Nerd Font**, e.g. `"JetBrainsMono Nerd Font Mono"`; otherwise any installed monospace works via `--font`.

## Usage

`record.sh` is bundled in this skill's directory. Point it at a program and an actions list; it writes `<out>.gif` and `<out>.mp4`:

```bash
<skill-dir>/record.sh --cmd "<program>" --out ./demo --actions demo.actions
# e.g. <skill-dir> = ~/.claude/skills/terminal-demo-video, .agents/skills/terminal-demo-video, ...
```

Actions may also be piped on stdin: `... --cmd "htop" --out ./demo < demo.actions`.

### Actions (one per line)

| Action | Effect |
| --- | --- |
| `type <text>` | Type the text **one character at a time**, like a person. |
| `paste <text>` | Send the text instantly (no per-char animation). |
| `key <keys>` | Send tmux key(s): `Enter`, `M-g` (=alt+g), `C-u` (=ctrl+u), `Escape`, `Up`, … |
| `sleep <secs>` | Pause — a readable beat (e.g. `sleep 2`). |
| `# …` / blank | Comment / ignored. |

### Options

`--size WxH` (default `110x16`) · `--boot SECS` (wait for the program to start, default `3` — slow TUIs need more) · `--cwd DIR` · `--font NAME` · `--font-size PX` · `--theme` (`asciinema`\|`dracula`\|`monokai`\|`nord`\|`github-dark`\|…) · `--speed N` · `--idle SECS` (cap idle gaps on render, default `2`) · `--type-delay S` (default `0.09`) · `--gif-only` · `--keep-cast`. Run `record.sh --help` for the full list.

## Example — an OMP/Pi extension demo (human typing, then a hotkey)

`demo.actions`:

```
# type a messy sentence, let the live grammar widget appear, then press alt+g to fix it
type i dont think its right
sleep 2.4
key M-g
sleep 2.6
```

Record it:

```bash
<skill-dir>/record.sh \
  --cmd "omp --no-session --no-title" --cwd /tmp/demo \
  --out ./assets/demo --size 110x16 --boot 16 \
  --font "JetBrainsMono Nerd Font Mono" --actions demo.actions
```

Produces `./assets/demo.gif` + `./assets/demo.mp4`: the message types out, the widget shows the issues, `alt+g` applies the fixes, and the clip rests on the corrected result.

## How it works & tips

- **Real terminal, real keys.** tmux delivers actual key events (incl. Alt/Ctrl chords), so interactive apps behave exactly as for a user. This is the whole reason to prefer it over VHS for hotkey demos.
- **Clean ending.** Recording stops by sending asciinema `SIGINT`, which finalizes the cast on the **current frame** and ends the program — so the clip rests on the final state, with no quit keystroke clearing the screen. Add a trailing `sleep` so an app's async refresh (e.g. a debounced widget) reaches its final state before the stop.
- **Pacing.** `--idle` caps *every* idle gap uniformly on render, so a long `--boot` and your read `sleep`s both compress to it; `1.5`–`2`s reads well. `--type-delay` sets typing speed; `--speed` scales the whole clip.
- **MP4** is padded to even dimensions with `yuv420p` for broad player support; the **GIF** loops.
- **Hosting.** Prefer committing the small `.actions` file (reproducible) and publishing the binaries as **GitHub Release assets** (stable URLs, no git-history bloat) rather than committing GIF/MP4. Reference those URLs from the README or gallery fields (e.g. a pi package's `pi.video`/`pi.image`).
- **Reproducible.** Keep the `.actions` file next to your project; re-run `record.sh` to refresh the media deterministically.
