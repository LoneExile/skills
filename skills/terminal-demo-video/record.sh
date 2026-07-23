#!/usr/bin/env bash
# terminal-demo-video — record a REAL terminal session to GIF + MP4.
#
# Drives a real program inside tmux, so full-screen TUIs and modifier-key chords
# (Alt/Ctrl/Meta) are captured faithfully — things VHS records inside
# ttyd/xterm.js silently drop. Records with asciinema, renders with agg (+ffmpeg
# for the mp4). Idle gaps are compressed on render so slow startups stay watchable.
#
# Usage:
#   record.sh --cmd "<program>" --out <path/basename> [options] < actions.txt
#   record.sh --cmd "htop"      --out ./demo --actions demo.actions
#
# Actions (one per line, from --actions FILE or stdin):
#   type <text>    type text one character at a time, like a person
#   paste <text>   send text instantly (no per-char animation)
#   key  <keys>    send tmux key(s):  key Enter   key M-g (=alt+g)   key C-u (=ctrl+u)
#   sleep <secs>   wait, e.g.  sleep 2   (lets viewers read; capped by --idle on render)
#   # ...          comment / blank line — ignored
#
# Options:
#   --cmd STR        program to run and record                              (required)
#   --out PATH       output basename; writes PATH.gif and PATH.mp4          (required)
#   --actions FILE   actions file (default: read actions from stdin)
#   --cwd DIR        run the program in DIR (default: current directory)
#   --size WxH       terminal columns x rows                     (default 110x16)
#   --boot SECS      wait after launch before running actions    (default 3; slow TUIs need more)
#   --font NAME      monospace font family    (default "JetBrainsMono Nerd Font Mono")
#   --font-size PX                                                (default 18)
#   --theme NAME     agg theme: asciinema|dracula|monokai|nord|github-dark|...  (default dracula)
#   --speed N        playback speed multiplier                   (default 1)
#   --idle SECS      cap idle gaps to N seconds on render         (default 2)
#   --type-delay S   seconds between typed characters             (default 0.09)
#   --gif-only       skip the mp4
#   --keep-cast      keep the intermediate asciicast (.cast) file
set -euo pipefail

cmd="" out="" actions="" cwd="." size="110x16" boot=3
font="JetBrainsMono Nerd Font Mono" font_size=18 theme="dracula"
speed=1 idle=2 type_delay=0.09 gif_only=0 keep_cast=0

while [ $# -gt 0 ]; do
  case "$1" in
    --cmd) cmd="$2"; shift 2;;
    --out) out="$2"; shift 2;;
    --actions) actions="$2"; shift 2;;
    --cwd) cwd="$2"; shift 2;;
    --size) size="$2"; shift 2;;
    --boot) boot="$2"; shift 2;;
    --font) font="$2"; shift 2;;
    --font-size) font_size="$2"; shift 2;;
    --theme) theme="$2"; shift 2;;
    --speed) speed="$2"; shift 2;;
    --idle) idle="$2"; shift 2;;
    --type-delay) type_delay="$2"; shift 2;;
    --gif-only) gif_only=1; shift;;
    --keep-cast) keep_cast=1; shift;;
    -h|--help) sed -n '2,44p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

[ -n "$cmd" ] || { echo "error: --cmd is required" >&2; exit 2; }
[ -n "$out" ] || { echo "error: --out is required" >&2; exit 2; }

for bin in tmux asciinema agg; do
  command -v "$bin" >/dev/null 2>&1 || { echo "error: '$bin' not found on PATH" >&2; exit 1; }
done
if [ "$gif_only" -eq 0 ]; then
  command -v ffmpeg >/dev/null 2>&1 || { echo "error: 'ffmpeg' not found (needed for mp4; pass --gif-only to skip)" >&2; exit 1; }
fi

cols="${size%x*}"; rows="${size#*x}"
gif="${out}.gif"; mp4="${out}.mp4"
outdir="$(dirname "$out")"; mkdir -p "$outdir"
cast="$(mktemp -t tdv).cast"
sess="tdv_$$"

cleanup() { tmux kill-session -t "$sess" 2>/dev/null || true; }
trap cleanup EXIT

# Read actions up front (file or stdin) so stdin is not later consumed elsewhere.
if [ -n "$actions" ]; then
  [ -f "$actions" ] || { echo "error: actions file not found: $actions" >&2; exit 1; }
  mapfile -t ACTIONS < "$actions"
elif [ ! -t 0 ]; then
  mapfile -t ACTIONS
else
  ACTIONS=()
fi

tmux kill-session -t "$sess" 2>/dev/null || true
tmux new-session -d -s "$sess" -x "$cols" -y "$rows"

launch="asciinema rec --overwrite --window-size ${cols}x${rows} $(printf %q "$cast") -c "
if [ "$cwd" != "." ]; then
  launch+="$(printf %q "cd $(printf %q "$cwd") && $cmd")"
else
  launch+="$(printf %q "$cmd")"
fi
tmux send-keys -t "$sess" "$launch" Enter
sleep "$boot"

for line in "${ACTIONS[@]}"; do
  verb="${line%%[[:space:]]*}"
  rest="${line#"$verb"}"; rest="${rest# }"
  case "$verb" in
    ""|\#*) : ;;
    type)
      for (( i=0; i<${#rest}; i++ )); do
        tmux send-keys -t "$sess" -l "${rest:i:1}"
        sleep "$type_delay"
      done ;;
    paste) tmux send-keys -t "$sess" -l "$rest" ;;
    key)   # shellcheck disable=SC2086 -- tmux parses space-separated key names
           tmux send-keys -t "$sess" $rest ;;
    sleep) sleep "$rest" ;;
    *) echo "warning: unknown action '$verb'" >&2 ;;
  esac
done

# Stop recording on the current frame. SIGINT lets asciinema finalize the cast
# cleanly and also ends the recorded program, so no quit keystroke (which would
# clear the input) is needed.
pid="$(pgrep -f "asciinema rec .*$(basename "$cast")" | head -1 || true)"
if [ -n "$pid" ]; then kill -INT "$pid" 2>/dev/null || true; fi
for _ in $(seq 1 20); do [ -s "$cast" ] && break; sleep 0.5; done
sleep 1

agg --font-family "$font" --font-size "$font_size" --theme "$theme" \
    --idle-time-limit "$idle" --speed "$speed" "$cast" "$gif"
echo "wrote $gif"
if [ "$gif_only" -eq 0 ]; then
  ffmpeg -y -i "$gif" -movflags +faststart -pix_fmt yuv420p \
    -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" "$mp4" >/dev/null 2>&1
  echo "wrote $mp4"
fi
if [ "$keep_cast" -eq 1 ]; then echo "cast: $cast"; else rm -f "$cast"; fi
