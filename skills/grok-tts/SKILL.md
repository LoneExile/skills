---
name: grok-tts
description: "Use when making a voiceover, narration or text-to-speech audio with a Grok (xAI) voice, including voice files and word timings for HyperFrames media-use recording lines."
---

# grok-tts

## What it does

`grok_tts.py` turns text into a Grok (xAI) voice file (WAV or MP3) plus word-level timings. Batch mode speaks a list of scenes and writes `lines.json`, ready to paste into a HyperFrames `media-use` `audio_request.json` as recording lines. It needs Python 3.9 or newer, uses only the standard library, and calls `https://api.x.ai`. Use the script. Do not call the xAI API by hand with curl or your own code, and do not rebuild the timing or request files yourself.

Run it as `python3 <skill-dir>/grok_tts.py`, never by bare path.

## Key setup

Run the script and let it find the key itself. It checks, in order, and the first source with a key wins:

1. environment `XAI_API_KEY`
2. environment `GROK_API_KEY`
3. `~/.omp/.env` (an `XAI_API_KEY` line, then a `GROK_API_KEY` line). OMP does not export this file to tool shells, so the script reads it directly. If a name appears twice, the last line wins (as in OMP).
4. the grok CLI login in `~/.grok/auth.json` (only while unexpired)

A project `.env` is never read. If a key is rejected the run stops; it does not try the next source. Each run that uses a key prints `Key: <source>` on stderr, naming which source paid. A run served fully from the cache needs no key and prints no `Key:` line.

Never read, print, export or paste a key or a credential file, and never put a key on a command line. If no key is found, the script prints `ERROR: no xAI key found` and lists what it checked.

## Quick reference

```bash
# One file, with word timings
python3 <skill-dir>/grok_tts.py "Meet Frame." -o voice.wav --words voice.words.json

# Batch: script.json is [{"id": "intro", "text": "..."}, ...]
python3 <skill-dir>/grok_tts.py --lines script.json --project .

# Voices
python3 <skill-dir>/grok_tts.py --list      # voice_id<TAB>name<TAB>gender
```

| Flag | Default | Meaning |
| ---- | ------- | ------- |
| `text` | | text to speak, or a path to a UTF-8 text file (a BOM is ignored) |
| `-o, --out` | | output file; must end in `.wav` or `.mp3`; missing folders are created |
| `--words` | | also write word timings (JSON) to this file; missing folders are created; must not be the same file as `-o` |
| `--lines` | | batch mode: UTF-8 JSON list of `{id, text}` |
| `--out-dir` | `voiceover` | batch output folder (inside `--project`); `<project>/assets/voice` is refused |
| `--project` | `.` | project folder for batch mode |
| `--voice` | `eve` | voice id |
| `--lang` | `en` | `auto` or a language code such as `en`, `pt-BR` |
| `--speed` | `1.0` | 0.7 to 1.5 |
| `--normalize` | off | ask xAI to normalize the text (numbers, symbols) |
| `--no-cache` | off | force a new API call |
| `--timeout` | `300` | seconds; must be above 0 |
| `--list` | | print the voices; cannot be combined with text, `-o`, `--words` or `--lines` |

Output:

- Single mode prints `Audio saved to: <abs path>` (plus ` (cached)` on a cache hit), `MEDIA:<abs path>`, and with `--words` `Words saved to: <abs path>`.
- Batch mode prints `<id>: <abs wav path>` per line (plus ` (cached)` on hits) and `Lines saved to: <abs lines.json>`. Ids must be plain file names (no `/`, `\`, `:`, no control characters, at most 200 bytes, not `.` or `..`) and unique ignoring case and Unicode form. All lines are checked before any request. The run stops at the first failure. `lines.json` is removed when a run starts and written only when every line succeeded, so a failed batch leaves no `lines.json`. Run it again and only the failed lines cost anything.
- Repeat runs of the same text, voice, language, speed and format come from the cache at `${XDG_CACHE_HOME:-~/.cache}/grok-tts/`. A paid result is cached before the output files are written, so if a write fails after a request, fix the problem and run again at no cost. The cache is best effort: if it cannot be written the run prints `WARNING: cache not updated (...)` on stderr and carries on.
- Text limit: 60,000 characters per request. A language code outside xAI's documented list prints `WARNING: ...` on stderr and is still sent.

## Speech tags

Put tags in the text. They are removed from the word timings. Text made only of tags is refused.

- Inline, written `[name]`: pause, long-pause, hum-tune, laugh, chuckle, giggle, cry, tsk, tongue-click, lip-smack, breath, inhale, exhale, sigh
- Wrapping, written `<name>text</name>`: soft, whisper, loud, build-intensity, decrease-intensity, higher-pitch, lower-pitch, slow, fast, sing-song, singing, emphasis

Tags are lowercase and exact.

## HyperFrames recipe

1. Write the script as `[{"id": ..., "text": ...}]`, one line per scene, in `script.json`.
2. Run `python3 <skill-dir>/grok_tts.py --lines script.json --project .` from the project folder. It writes `voiceover/<id>.wav`, `voiceover/<id>.words.json` and `voiceover/lines.json`.
3. Paste the contents of `voiceover/lines.json` into the `lines` of `audio_request.json`. Each entry already has `id`, `audio` (a path relative to the project) and `words`. Paste it again after every successful run, because the words change when the text does.
4. Leave the request `speed` out, or set it to 1. The script already applied the speed; a second one would make the audio and the timings disagree.
5. Keep the takes in `voiceover/`. Do not move them to `assets/voice/` (`--out-dir assets/voice` is refused): `media-use` writes its own converted copies there, and it leaves out the lines whose takes live there.
6. From the project folder, run the `media-use` audio engine: `node <media-use-skill-dir>/audio/scripts/audio.mjs --request ./audio_request.json --hyperframes . --out ./audio_meta.json --only tts`. `<media-use-skill-dir>` is where the `media-use` skill is installed.
7. Get music and sound effects through `media-use` separately.
8. Credit the voice as AI-generated by xAI Grok (for example "Voice: AI-generated by xAI Grok") wherever the video or its notes credit the voice. `media-use` labels recording lines as the person's own recording, so fix the credit.

## Stop rule

When the script exits non-zero, report its `ERROR:` line and stop. Never switch to another voice provider, another voice tool or another key, and never go looking for a key in files or the environment yourself.

## Errors

| Exit | Meaning | Example message |
| ---- | ------- | --------------- |
| 0 | success | |
| 2 | bad input, found before any API call | `ERROR: text is empty`; `ERROR: speed 2.0 is out of range; use 0.7 to 1.5`; `ERROR: -o must end in .wav or .mp3`; `ERROR: text has only speech tags and nothing to say`; `ERROR: -o and --words name the same file`; `ERROR: <file> is not readable UTF-8 text; save it as UTF-8`; `ERROR: line 'x': ...` |
| 1 | key, API, network or file failure | `ERROR: no xAI key found. Checked: ...`; `ERROR: the key from <source> was rejected: ...`; `ERROR: could not reach https://api.x.ai after 4 attempts: ...`; `ERROR: xAI API error <status>: ...` |

For an unknown voice the error says to run `--list`. Fixes: set `XAI_API_KEY`, add it to `~/.omp/.env`, or run `grok` to log in.
