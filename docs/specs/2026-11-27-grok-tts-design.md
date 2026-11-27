# grok-tts skill: design

Date: 2026-11-27 · Status: draft for approval · Repo: `LoneExile/skills`

## Intent

The owner wants to make narrated videos with HyperFrames using Grok (xAI) voices.
HyperFrames' `media-use` skill only knows HeyGen, ElevenLabs, local Kokoro and
Gemini voices. The owner already pays for xAI (Super plan, API usage included)
and has a Grok API key.

`grok-tts` is a standalone skill in this repo. It turns text into Grok voice
files plus word timings, in the exact shape HyperFrames already accepts from a
person's own recording. HyperFrames runs unchanged: the voice file goes in as a
"recording line", and its word timings drive scene timing and captions.

## Goals

- **G1** One command turns text, or one line per scene, into voice files via
  xAI `POST https://api.x.ai/v1/tts`.
- **G2** Word timings in HyperFrames' caption shape `[{id, text, start, end}]`,
  built from Grok's native character timestamps: no transcription pass.
- **G3** Batch output includes a ready-made `lines` array that the agent pastes
  into `media-use`'s `audio_request.json`; no field in it needs editing.
- **G4** Works inside OMP agent sessions and in plain shells.
- **G5** Fails loudly and specifically; never leaves a half-written file or a
  file whose header lies about its length; never prints the key.
- **G6** Re-running after editing one line only pays for that line.

## Non-goals

- Forking or changing HyperFrames or `media-use`.
- Music and sound effects (they stay with `media-use`).
- Streaming (WebSocket) TTS, voice cloning, the API's `replace` pronunciation map.
- Fixing token handling in `grok-image` / `grok-video` (separate change).
- Per-line voice or speed overrides; parallel batch requests.

## Anti-goals

Any of these is a failure even if a video comes out:

- Silently using another voice provider, or another account's key, when Grok fails.
- Captions that show speech tags or drift from the audio.
- An audio file whose header disagrees with its real length.
- The key in argv, stdout/stderr, a written file, or this public repo
  (including the owner's homelab token URL).

## Constraints

- Stdlib-only Python 3, like `grok-image` and `grok-video`. CI runs Python 3.12;
  the owner runs 3.14. No ffmpeg inside the skill (`media-use` itself needs
  ffmpeg for its conversion; the owner has 9.0.2).
- Repo CI (`.github/workflows/validate.yml`): `npx -y skills add . --list`
  validates frontmatter; `python3 -m py_compile skills/**/*.py`. SkillSpector
  scans `./skills` against `.skillspector-baseline.yaml` (reviewed findings only).
- `release.yml` zips each `skills/<name>/` directory, so tests and fixtures live
  outside `skills/` and do not ship.
- API limits: text ≤ 60,000 characters per request; speed 0.7–1.5; Tier 0 is
  50 requests/s and 100 concurrent sessions; a single request times out after
  15 minutes; price $15 per 1M characters.

## Measured API facts

Captured 2026-11-27 with the owner's key. The plan carries these responses as
test fixtures.

| Probe | Result |
|---|---|
| `with_timestamps: true`, WAV 44.1 kHz | 200, JSON `{audio (base64), content_type, duration, audio_timestamps: {graph_chars, graph_times}}` |
| WAV header | RIFF size 2,147,483,683 and data size 2,147,483,647 (streaming placeholders). Real PCM is 6.39 s; Python `wave` reads 24,347 s; ffprobe reads 6.39 s |
| Speech tags | `[pause]`, `<whisper>`, `</whisper>` come back as characters with their own times. Grouping words naively starts `this` at 3.57 s instead of 4.23 s and ends `quiet.` at 5.85 s instead of 5.39 s |
| `text_normalization: true` | `graph_chars` stay in written form (`2026`) |
| Unknown voice | 404 `{"error":"TTS synthesis failed: Voice 'nonexistent-voice-zz' not found"}` |
| Empty text | 400 `{"error":"Field \`text\` must not be empty"}` |
| `speed: 3.0` | 400 `{"error":"speed must be between 0.7 and 1.5"}` |
| `language: "xx-YY"` | **200: accepted and billed, no error** |
| Invalid API key | **400, not 401**: `{"code":"Client specified an invalid argument","error":"Incorrect API key provided. …"}` |
| Expired OAuth token | 403 `{"code":"The caller does not have permission …","error":"The OAuth2 access token could not be validated. …"}` |
| `GET /v1/tts/voices` | `{"voices":[{voice_id, name, language, gender}]}`, 28 built-in voices. Custom voices are not listed (xAI docs) |
| Valid OAuth token (grok CLI client, from the owner's refresher) | 200 on `/v1/tts` and `/v1/tts/voices` |
| `graph_chars` / `graph_times` | One entry per Unicode code point, spaces included, each with its own `[start, end]` pair in seconds; the joined characters equal the sent text |
| `speed: 1.4` | Timings already reflect the speed: `duration` 2.09, PCM 2.086 s, last character ends at 2.09 (speed 1.0: 2.87 / 2.87 / 2.87) |
| MP3 at 44.1 kHz / 192 kbps with timestamps | 200, `audio/mpeg`, starts with a frame sync (no ID3); ffprobe reads 2.90 s against `duration` 2.87 (encoder padding) |
| Thai text (`language: "th"`) with an emoji | 200; one entry per code point, combining marks included; the emoji is one entry with its own time |
| OMP tool shell | None of the 24 names in `~/.omp/.env` is set |

## HyperFrames contract

Read from `media-use` source on `main`, 2026-11-27 (`audio/scripts/audio.mjs`,
`audio/scripts/lib/recording.mjs`, `audio/references/tts.md`).

- A recording line in `audio_request.json` `lines` is `{ id, audio, words? }`.
- `audio` must be a relative path inside the project folder; an absolute path or
  one leading outside is an error.
- `words` is an array of `{text (or word), start, end}` in seconds. Non-empty
  means no transcription; empty or absent means the engine transcribes.
- The engine converts each recording with ffmpeg to 44.1 kHz mono at its own
  voice path (`assets/voice/<id>.wav`, or `-2` when that name is taken). Source
  takes therefore must not live at that path.
- The request's `speed` time-stretches recordings, so it must stay 1 when
  `grok-tts` already applied the speed.
- A request where every line is a recording uses provider `recording`: no HeyGen
  sign-in is needed for the voice.
- Line ids are plain file names: not empty, not `.` or `..`, no `/`, `\` or `:`.
- Captions and HeyGen's `--words` both use the flat `[{id, text, start, end}]` shape.

## Approaches

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | Standalone `grok-tts` skill; HyperFrames takes its files as recording lines | No HyperFrames changes; also usable outside video. The request has to name `grok-tts`. |
| B | Fork `media-use`, add Grok as a provider | Native feel, but `npx hyperframes skills update` and upstream updates overwrite the fork every release. |
| C | Wrapper skill bundling both | Duplicates `/hyperframes` routing; breaks when upstream renames things. |

The owner chose A.

## Design

### Files

- `skills/grok-tts/SKILL.md`: the agent-facing skill.
- `skills/grok-tts/grok_tts.py`: the CLI; stdlib only.
- `tests/grok_tts/`: offline unittest suite and fixtures (not shipped).
- `README.md`: skills table row and a section.
- `.github/workflows/validate.yml`: run the tests; trigger on `tests/**`.
- `.skillspector-baseline.yaml`: reviewed entries for the new skill's findings.

### Command line

```text
Single:  python3 grok_tts.py [options] [--] TEXT_OR_FILE -o OUT.wav|OUT.mp3 [--words OUT.words.json]
Batch:   python3 grok_tts.py [options] --lines SCRIPT.json [--out-dir voiceover] [--project .]
Voices:  python3 grok_tts.py --list
```

- `TEXT_OR_FILE`: an existing file is read as UTF-8; anything else is the text.
  `--` ends options, for text that starts with `-`.
- `SCRIPT.json`: `[{"id": "intro", "text": "…"}, …]`.
- Batch writes `<out-dir>/<id>.wav` and `<out-dir>/<id>.words.json` for each line,
  then `<out-dir>/lines.json`:
  `[{"id": "intro", "audio": "voiceover/intro.wav", "words": [...]}, …]`.
  `audio` is relative to `--project` with forward slashes. `--out-dir` must be
  inside `--project`. Batch always writes WAV.
- Options: `--voice` (default `eve`; custom voice ids allowed), `--lang` (default
  `en`), `--speed` (default `1.0`), `--normalize` (sends `text_normalization`),
  `--no-cache`, `--timeout` (default 300 s).
- Output. Single: `Audio saved to: <abs path>`, `MEDIA:<abs path>`, plus
  `Words saved to: <abs path>` with `--words`. Batch: one line per id
  (marked `(cached)` when no API call was made), then `Lines saved to: <abs path>`.
- Exit codes: 0 success; 2 bad input, detected before any API call; 1 API,
  network, key or file failure.

### Request

- Always `with_timestamps: true`. This gives one code path, the `duration` used
  to validate the file, and the words.
- `output_format`: WAV is `{"codec": "wav", "sample_rate": 44100}`; MP3 is
  `{"codec": "mp3", "sample_rate": 44100, "bit_rate": 192000}`. The format
  follows the `-o` extension.

### Checks before any API call (exit 2)

- Text is non-empty after trimming and at most 60,000 characters.
- Speed is within 0.7–1.5.
- Language is `auto` or BCP-47-shaped (`^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$`). A
  well-formed code missing from xAI's documented list prints a warning and
  continues: the API accepts any code silently, and the docs say other languages
  "may work". A typo like `english` is refused.
- Batch ids are valid file names and unique; the output extension is `.wav` or `.mp3`.
- Text that has no words left once speech tags are removed (for example only
  `[laugh]`) is refused. Its word list would be empty, and `media-use` would
  then transcribe the file and could invent words.
- The voice is not pre-checked, because custom voices are absent from the voice
  list. A 404 says the voice was not found and to run `--list`.

### Key lookup

The first source that holds a key wins:

1. environment `XAI_API_KEY`
2. environment `GROK_API_KEY`
3. `~/.omp/.env`: the `XAI_API_KEY` line, then the `GROK_API_KEY` line. Dotenv
   rules: optional `export `, optional matching quotes, nothing else is read.
4. the grok CLI login `~/.grok/auth.json`: an entry with `key` whose
   `expires_at` is still in the future

If no source holds a key, exit 1 and list each source with its reason (unset,
file missing, no such line, expired at `<time>`). A key the API rejects stops
the run with "the key from `<source>` was rejected". The API rejects a key with
401, 403, or a 400 whose `error` contains `API key` (case-insensitive; measured
text: "Incorrect API key provided"). Other 400s show the API's own `error`. The
run does **not** fall through to the next key source: that could silently bill
a different account. A project `.env` is never read. The key is
never printed, accepted on argv, or written anywhere; printed API errors are the
JSON `error` field only.

### Network

- urllib with the `--timeout`.
- Retries 429, 500, 502, 503, 504 and connection errors or timeouts up to 3
  times, after 2, 4 and 8 s plus 0–1 s jitter. A numeric `Retry-After` header
  (capped at 60 s) replaces the computed wait.
- Never retries any other 4xx.

### Audio integrity

- **WAV:** after the 12-byte RIFF header, walk the chunks to `data`; everything
  from there to the end of the file is audio (a trailing odd byte is dropped).
  Rewrite the data size and the RIFF size (`file length − 8`). The PCM length
  must be within 0.05 s of the response's `duration`, or the run fails and
  writes nothing.
- **MP3:** must start with `ID3` or an MPEG frame sync. If ffprobe is on PATH,
  its duration must be within 0.1 s of `duration`; without ffprobe that check
  is skipped.
- **Atomic writes:** write `<dest>.part`, then `os.replace`. Audio and words land
  together or not at all; leftover `.part` files are removed on failure. Batch
  writes `lines.json` only when every line succeeded. Lines that succeeded stay
  on disk and in the cache, so a re-run pays only for the failed ones.

### Word timings

- Input: `graph_chars` and `graph_times`. They must have equal length, finite
  times and `end ≥ start`; otherwise fail.
- Speech tags are xAI's documented vocabulary, matched exactly (lowercase):
  - inline: `[pause] [long-pause] [hum-tune] [laugh] [chuckle] [giggle] [cry] [tsk] [tongue-click] [lip-smack] [breath] [inhale] [exhale] [sigh]`
  - wrapping, opening and closing: `<soft> <whisper> <loud> <build-intensity> <decrease-intensity> <higher-pitch> <lower-pitch> <slow> <fast> <sing-song> <singing> <emphasis>`
- Tag characters are removed and also separate words. Whitespace separates
  words, and punctuation stays attached. A word's start is its first
  character's start and its end is its last character's end. Empty words are
  dropped; ids run `w0`, `w1`, … without gaps.
- Other bracketed text (`[1]`, `[sic]`) stays in the captions.
- Whitespace is the only word boundary. Thai separates phrases with spaces, so
  Thai gets phrase-level timings, which matches how Thai captions break.
  Languages written with no spaces at all (Chinese, Japanese) get one timing per
  sentence; finer splitting for them is a non-goal.
- On the measured fixture the result is: `It's` 0.08–0.22, `2026,` 0.30–1.62,
  `Dr.` 1.70–2.03, `Smith` 2.06–2.40, `and` 3.31–3.57, `this` 4.23–4.41,
  `part` 4.49–4.73, `is` 4.77–4.87, `quiet.` 4.93–5.39, `Done!` 5.91–6.39.

### Cache

- Key: sha256 of canonical JSON `{text, voice (lowercase), lang (lowercase), speed, normalize, format, "v": 1}`.
- Entries: `<hash>.<ext>` and `<hash>.words.json` under
  `${XDG_CACHE_HOME:-~/.cache}/grok-tts/`, written atomically.
- A hit copies the files into place with no API call. `--no-cache` skips the
  read but still refreshes the entry.

### SKILL.md

- The description says only when to use it (starts "Use when…"): Grok or xAI
  voice, Grok voiceover or narration, text to speech for a HyperFrames video with
  a Grok voice.
- The body covers:
  - a quick reference
  - `--list`
  - the speech tags
  - key setup
  - the HyperFrames recipe: batch, paste `lines.json` into `audio_request.json`
    `lines`, keep the request `speed` at 1, run the engine with `--only tts`;
    music and sound effects go through `media-use` separately
  - the stop rule: if Grok fails, report it and stop, never switch provider

### Testing

- An offline unittest suite under `tests/grok-tts/`, discovered per skill folder
  (`python3 -m unittest discover -s tests/grok-tts -t tests/grok-tts`) so a test
  package never shadows the `grok_tts` module. It runs a stub HTTP server on
  127.0.0.1, passed in through the module's `api_base` parameter. No
  environment override for the API host ships.
- Fixtures come from the measured responses above, plus a synthetic WAV with
  the streaming header.
- Each test is shown able to fail by breaking the code it guards.
- CI runs the suite. A live smoke test runs only when `GROK_TTS_LIVE=1`.
- The skill text is tested the writing-skills way: a fresh agent asked for "a
  two-line Grok voiceover for a HyperFrames video", without the skill
  (baseline) and then with it.

## Calls made for the owner

Each can be overridden.

1. Key lookup reads `~/.omp/.env` (only the two key names) and not a project
   `.env`, because a received project's `.env` could route script text to
   someone else's account and request logs.
2. A rejected key stops the run instead of trying the next source.
3. Every request uses `with_timestamps`.
4. Language codes get a shape check plus a warning, not a strict list, so Thai
   and other undocumented languages still work.
5. The cache is global, in `~/.cache/grok-tts/`, not stored next to project files.
6. Batch runs lines one at a time and writes `lines.json` only when all lines succeed.
7. Tests live in `tests/`, outside the shipped skill folder.
8. Defaults: voice `eve`, language `en`, output folder `voiceover/`.
9. Speech tags are matched from xAI's documented list exactly. A new tag xAI
   adds later shows up in captions until the list is updated.
10. Text with nothing to say once tags are removed is refused before the API
    call, so captions never fall back to transcription.

## Left to the builder

Internal helper names beyond the plan's interfaces, message wording, how the
jitter is generated, and README prose.
