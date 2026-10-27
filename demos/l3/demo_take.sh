#!/usr/bin/env bash
# L3 demo preflight — one command, must exit 0 before the recorder is started.
# Freeze check -> rebuild bundle -> re-render report -> show the dry live shape.
# Refuses to continue if the evidence has drifted (a video that disagrees with the
# freeze manifest is worse than no video).
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 9
ROOT="$PWD"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
t0=$(date +%s)

say "1/4  freeze --check"
if ! out=$("$PY" -m demos.l3.freeze_evidence --check 2>&1); then
  printf '%s\n' "$out"
  say "ABORT: evidence drift — do not record. Re-freeze only after reviewing the diff."
  exit 2
fi
printf '%s\n' "$out"

say "2/4  build_bundle (replay, 0 model calls)"
"$PY" -m demos.l3.build_bundle

say "3/4  render_report"
"$PY" -m demos.l3.render_report

say "4/4  live path — dry shape (0 credits, no network)"
"$PY" -m demos.l3.run_live --dry --calls 1

say "CLAIM LINES (speak only what is on screen)"
grep -E '^- \*\*C[0-9]' -m 12 "$ROOT/demo-bundle/report.md" 2>/dev/null \
  || head -n 20 "$ROOT/demo-bundle/report.md"

say "ARTIFACTS"
ls -l "$ROOT/demo-bundle/report.html" "$ROOT/demo-bundle/bundle.json" "$ROOT/demo-bundle/report.md" \
  | awk '{print $5" bytes  "$9}'

t1=$(date +%s)
say "RECORDING READY in $((t1 - t0))s — report: $ROOT/demo-bundle/report.html"
if [[ -n "${DISPLAY:-}" ]] && command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$ROOT/demo-bundle/report.html" >/dev/null 2>&1 || true
fi
