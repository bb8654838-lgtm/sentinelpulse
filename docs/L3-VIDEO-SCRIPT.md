# L3 — 90-SECOND DEMO SCRIPT (SentinelPulse · Track: Indie Developer)

**Locked 2026-10-27** against the accepted kickoff rules. Target **90s**, hard ceiling 120s
(portal cap unstated → shorter is never penalised). Screen recording only, no talking head,
captions burned in, VO in English.

Text-linkage rule for the whole cut: **no number is spoken that is not visible on screen at that
moment.** Every figure in this script exists in `demo-bundle/report.html`, which is generated from
frozen evidence.

---

## 0. What the judges score (and where this cut pays it)

| weight | criterion | beat that pays it |
|---|---|---|
| 30% | Impact | 0:05–0:15 — the noise/cost problem + the two numbers |
| 25% | Use of Meta AI Tooling | 1:06–1:15 — model id, call signature, gate decision made by it |
| 25% | Demo | 0:15–1:06 — unedited terminal → browser run, hashes shown |
| 20% | Technical Depth & Execution | 0:58–1:06 + 1:15–1:24 — freeze semantics, budget cap |

Compliance beat (new rule, kickoff slide): **0:00–0:05 states the work window out loud.** Only
10-27 in-window artifacts appear; the 10-20 pre-window reading is never shown (see architecture §9).

---

## 1. Preflight — one command, must be green

```bash
cd ~/meta-hack && bash demos/l3/demo_take.sh
```

`demo_take.sh` refuses to continue unless the freeze verifies, then runs
freeze `--check` → `build_bundle` → `render_report` → opens the report. If it exits non-zero,
**do not record** — a video that disagrees with the freeze manifest is worse than no video.

Manual equivalent:

```bash
.venv/bin/python -m demos.l3.freeze_evidence --check   # must print "freeze intact"
.venv/bin/python -m demos.l3.build_bundle              # regenerate from frozen evidence
.venv/bin/python -m demos.l3.render_report             # rebuild report.html
```

Pre-record environment (also the exact state the video shows):

```bash
export MODEL_API_KEY=...        # portal contract name; legacy name still honoured
export PYTHONUNBUFFERED=1
# terminal: 1280x900, 16pt mono, dark theme, prompt = "meta-hack $"
# browser:  1280x900, 100% zoom, single tab, extensions off, notifications off
```

---

## 2. Beat sheet — 10 shots, 90 seconds

| # | time | picture (what is on screen) | VO (spoken) | caption (burned) |
|---|---|---|---|---|
| 1 | 0:00–0:05 | Title card over report top edge | "This is SentinelPulse. Everything you'll see was built between October 26th and November 4th." | built inside the hackathon window · Indie Dev track |
| 2 | 0:05–0:15 | Terminal: `python -m demos.l3.build_bundle` scrolls, prints C1–C5 | "Alert fatigue is a billing problem. My agent decides what is worth a model call before it spends one." | claims are recomputed, never typed |
| 3 | 0:15–0:27 | `report.html` top: metric cards, gate-off vs gate-on arms | "Same incident batch, two arms. Gate off, then gate on." | 5 claims · 7 fixtures · generated from frozen evidence |
| 4 | 0:27–0:39 | Per-fixture table; cursor parks on the 4 `drop` rows | "Seven incidents. Four were monitoring noise — the gate killed them with zero model calls." | 0 noise calls · 4 rules-dropped |
| 5 | 0:39–0:49 | Token + latency cards | "Thirteen point two percent fewer tokens, and seven point three seconds faster, end to end." | 5533 → 4802 tokens · 36.8s → 29.5s |
| 6 | 0:49–0:58 | Escalation rows + incident-miss card | "All three real signals still escalated. Zero incident misses — that's the metric that would kill this product." | escalations 3/3 · misses 0/7 |
| 7 | 0:58–1:06 | Frozen-inputs table, sha256 prefixes visible; terminal `freeze_evidence --check` prints `freeze intact` | "Inputs are hash-locked. Change the evidence, and every number on this page changes with it." | sha256-pinned · `--check` exits 2 on drift |
| 8 | 1:06–1:15 | Terminal: one real Meta call — request body showing `muse-spark-1.3` and the tool/route decision | "The routing decision itself is made by Muse Spark on Meta's model API — the model decides what deserves the model." | api.meta.ai/v1 · muse-spark-1.3 |
| 9 | 1:15–1:24 | Terminal: `run_live` without `--confirm-live` → refusal, exit 4; then the cap printout | "Live mode is opt-in and hard-capped at ten calls. A demo can't quietly turn into a bill." | opt-in · 10 calls / 8000 tokens ceiling |
| 10 | 1:24–1:30 | Back to report top, then cut to README with the public repo URL | "SentinelPulse — noise out, signal triaged. Repo is public; clone it and run the same check." | github.com/&lt;owner&gt;/&lt;repo&gt; · replay needs no key |

VO word count ≈ 205 → comfortable inside 90s at normal pace. If a take runs long, cut shot 5 to
four words ("thirteen percent fewer tokens") — never trim shot 6 or 7.

---

## 3. Shot 8 detail — the Meta tooling beat (25% of score)

This is the beat most submissions lose points on, so it gets its own setup. One call, one take,
nothing else on screen:

```bash
export MODEL_API_KEY=...                  # never echo the key on camera
.venv/bin/python -m demos.l3.run_live --confirm-live --calls 1 --fixture 1
```

On screen: the endpoint `api.meta.ai/v1`, the model id `muse-spark-1.3`, the router tool call and
its arm decision (`drop` / `reason` / `escalate`). The key itself must never appear — it is read
from the environment, never echoed.

If credits are not confirmed, replace shot 8 with the dry path (0 credits, no network):

```bash
.venv/bin/python -m demos.l3.run_live --dry --calls 1
```

That prints endpoint, model, key source ("not printed"), fixture scope and the hard ceiling on
screen; say "the request shape is real, the spend is gated." Honest, and it still scores.

---

## 4. Recording commands

```bash
# report open at recording size (any Chromium-family browser)
google-chrome --new-window --window-size=1280,900 \
  file:///home/ubuntu/meta-hack/demo-bundle/report.html

# capture (X11 + ffmpeg; adjust :0.0 to the live display)
ffmpeg -f x11grab -video_size 1280x900 -framerate 30 -i :0.0 \
       -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p \
       ~/meta-hack/demo-bundle/l3-demo-raw-$(date +%Y%m%d).mp4
```

Post: trim to 90s, burn captions, export 1080p H.264 (`-vf scale=1920:-2`), then:

```bash
ffprobe -v error -show_entries format=duration,size -of default=nw=1 \
  ~/meta-hack/demo-bundle/l3-demo-20261027.mp4    # duration must be ≤ 90
```

Name the final file `l3-demo-<YYYYMMDD>.mp4` in `demo-bundle/`, and link it from the README.

---

## 5. Hard rules (a take that breaks one is discarded, not patched)

1. No number spoken that is not visible on screen at that moment.
2. No camera edit between the terminal run and the report render — the run must be continuous.
3. No API key, token, email or internal hostname on screen. Check `--check` output and shell
   prompt history before recording.
4. Pre-window artifacts (the 10-20 reading) never appear on screen; the comparison is the in-window
   gate-off vs gate-on pair.
5. If a live call fails mid-take, keep the take and narrate it. A visible fail-open path is stronger
   evidence than a perfect edit.
6. The video never edits out an incident miss. There are none — keep it that way.

---

## 6. Acceptance checklist before upload

- [ ] `bash demos/l3/demo_take.sh` exits 0 on a clean shell
- [ ] `freeze_evidence --check` → `freeze intact` visible on camera (shot 7)
- [ ] model id `muse-spark-1.3` readable at 1080p (shot 8)
- [ ] duration ≤ 90s, `ffprobe` confirms
- [ ] repo public, README links the video and states the work window
- [ ] captions burned (judges watch muted more often than not)
- [ ] no key / token / internal host in any frame
