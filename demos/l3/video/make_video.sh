#!/usr/bin/env bash
# SentinelPulse — 90s demo render, one command.
#   capture real stdout -> compose timeline -> headless-chromium frames -> ffmpeg
set -euo pipefail
cd "$(dirname "$0")/../../.."
ROOT="$PWD"
PY="${ROOT}/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
# renderer needs playwright, which lives in the system interpreter, not the app venv
PYR="${PYR:-$(command -v python3)}"
"$PYR" -c "import playwright" 2>/dev/null || { echo "FATAL: playwright missing in $PYR"; exit 3; }
FPS="${FPS:-30}"
TOTAL=90
OUT="${ROOT}/demo-bundle/sentinelpulse-demo-$(date -u +%Y%m%d).mp4"

echo "== 1/4 capture terminal (real runs) =="
"$PY" -m demos.l3.video.capture_terminal

echo "== 2/4 compose timeline =="
"$PY" -m demos.l3.video.compose

echo "== 3/4 render frames (${FPS}fps, ${TOTAL}s) =="
rm -rf demos/l3/video/frames && mkdir -p demos/l3/video/frames
"$PYR" -m demos.l3.video.render_frames --fps "$FPS" --total "$TOTAL"

echo "== 4/4 encode + verify =="
mkdir -p demo-bundle
ffmpeg -y -hide_banner -loglevel warning -framerate "$FPS" \
  -i demos/l3/video/frames/%05d.jpg \
  -c:v libx264 -preset slow -crf 19 -pix_fmt yuv420p -profile:v high \
  -movflags +faststart -g "$((FPS*2))" "$OUT"

DUR="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT")"
RES="$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=x "$OUT")"
SIZE="$(du -h "$OUT" | cut -f1)"
echo "VIDEO ok: $OUT  ${DUR}s  ${RES}  ${SIZE}"
awk -v d="$DUR" 'BEGIN{ if (d > 90.5) { print "FATAL: over 90s"; exit 2 } }'
mkdir -p demo-bundle/thumbs
for t in 2 11 20 32 44 52 61 70 79 87; do
  ffmpeg -y -hide_banner -loglevel error -ss "$t" -i "$OUT" -frames:v 1 \
    "demo-bundle/thumbs/t${t}.png"
done
echo "thumbs: $(ls demo-bundle/thumbs | wc -l)"
