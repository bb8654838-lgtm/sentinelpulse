# Cerebral Valley — Meta AI Developer Hackathon: submission pack

Track: **Indie Developer** · Team: **solo** · Window: **2026-10-26 → 2026-11-04**
Repo: **https://github.com/bb8654838-lgtm/sentinelpulse**
Video: **https://github.com/bb8654838-lgtm/sentinelpulse/blob/master/demo-bundle/sentinelpulse-demo-20261027.mp4**

Everything below is copy-paste ready. Blocks are plain text; no markdown needed except the
description, where line breaks are intentional.

---

## FIELD 1 — Project title

```
SentinelPulse — noise out, signal triaged
```

## FIELD 2 — Elevator pitch (one or two sentences)

```
A tier-0 gate that keeps alert fatigue away from the reasoning model: deterministic rules drop
provable noise at zero tokens, and only real signal escalates to Meta Muse (muse-spark-1.3).
Measured on a 7-fixture replay: 0 noise calls, -13.2% tokens, -19.8% latency, 0/7 incidents missed.
```

## FIELD 3 — Detailed description

```
Every on-call queue fills with events that were never incidents: health checks that returned 200,
favicon 404s, CDN revalidations, blips that self-recovered before anyone looked. Most "AI SRE"
stacks hand all of it to a reasoning model and pay tokens, latency and attention for nothing.

SentinelPulse puts a deterministic gate in front of the model. gate-0 applies rules to the
normalized event and drops provable noise at zero tokens. Survivors are typed by a cheap
classifier, and only real signal reaches tier-1 triage on Meta's Muse API (muse-spark-1.3),
which drafts the triage note, hypotheses and next actions. A sandboxed remediator can propose a
minimal patch; the authenticated PR path exists but is opt-in and unused in the demo.

The claim is not "the model is smart" - it is that the model never sees the noise at all.
Replay of the same 7 fixtures, gate off vs gate on, single run:
  C1 incidents missed      0/7 -> 0/7
  C2 noise model calls     4   -> 0     (4 dropped by rules)
  C3 tokens                5533 -> 4802 (-13.2%)
  C4 end-to-end latency    36.8s -> 29.5s (-19.8%)
  C5 signal escalation     3/3 -> 3/3

Evidence is frozen by SHA-256 (bench/evidence/FREEZE-20261027.json), so every number is tied to
exact bytes. `python3 -m demos.l3.freeze_evidence --check` fails loudly on any drift, and
`bash demos/l3/demo_take.sh` runs a 4-step preflight (freeze -> bundle -> report -> dry live shape)
that aborts with exit 2 if the evidence moved. The report is a single self-contained HTML file with
no CDN, so it renders air-gapped. Live mode refuses with exit 4 unless --confirm-live is passed, and
BudgetGuard reserves calls and tokens against a hard ceiling before any spend.

Built for the Meta AI Developer Hackathon, Indie track, solo. Inside the window (Oct 26 - Nov 4):
the whole demos/l3 lane - freeze, bundle, report renderer, live runner, preflight harness, and the
90-second video renderer - plus the 2026-10-27 bench run those claims cite. The app/ gate prototype
from Oct 14-20 is disclosed as pre-window: it is the thing being measured, not a claimed deliverable.
```

## FIELD 4 — GitHub repository URL

```
https://github.com/bb8654838-lgtm/sentinelpulse
```

## FIELD 5 — Demo video URL

```
https://github.com/bb8654838-lgtm/sentinelpulse/blob/master/demo-bundle/sentinelpulse-demo-20261027.mp4
```

Direct file (if the form wants a raw playable URL):

```
https://raw.githubusercontent.com/bb8654838-lgtm/sentinelpulse/master/demo-bundle/sentinelpulse-demo-20261027.mp4
```

If the form only accepts YouTube / Loom: upload `demo-bundle/sentinelpulse-demo-20261027.mp4`
unlisted and paste the resulting watch URL. Nothing else changes.

## FIELD 6 — Technologies / built with

```
Python 3.12, Meta Muse API (muse-spark-1.3, api.meta.ai/v1), deterministic rule gate,
headless Chromium (Playwright) for the video renderer, ffmpeg/libx264, no frontend framework
```

## FIELD 7 — Track

```
Indie Developer (solo builder)
```

## FIELD 8 — What was built during the hackathon window

```
Inside window (Oct 26 - Nov 4, 2026):
  demos/l3/freeze_evidence.py     SHA-256 evidence freeze + --check drift gate
  demos/l3/build_bundle.py        machine-readable claims bundle
  demos/l3/render_report.py       self-contained HTML/MD report (no CDN)
  demos/l3/run_live.py            budget-guarded live runner, --dry, exit-4 refusal
  demos/l3/budget.py              call/token ceiling guard
  demos/l3/demo_take.sh           4-step preflight gate (exit 2 on drift)
  demos/l3/video/*                capture -> compose -> 2700 frames -> ffmpeg + ffprobe gate
  bench/evidence/tier0-bench-LIVE-20261027.json + FREEZE-20261027.json
  demo-bundle/*                   bundle.json, report.md, report.html, 90s mp4

Disclosed as pre-window: app/ gate prototype (Oct 14-20) and the 2026-10-20 bench JSON kept as a
regression baseline.
```

## Judging-weight map (why each score should land)

- **Impact (30)** — removes 100% of noise model calls on the bench (4/4), cuts tokens 13.2% and
  latency 19.8% with zero missed incidents. The saving is per-event and scales with queue volume.
- **Meta tooling (25)** — real Muse API integration (`api.meta.ai/v1`, `muse-spark-1.3`), key from
  `MODEL_API_KEY`, tier-1 triage is the model's actual job: hypotheses + next actions.
- **Demo (25)** — 90s, burned captions, every frame backed by real command output; the renderer
  replays recorded stdout instead of typing fiction; ffprobe gates duration/fps before acceptance.
- **Tech depth (20)** — deterministic gate semantics with stage trails, hash-frozen evidence,
  fail-loud drift checks, budget ceiling enforced before spend, refusal path by default, air-gap
  HTML report.

## Pre-submit checklist

1. `gh api repos/bb8654838-lgtm/sentinelpulse -q .visibility` -> `public`
2. `python3 -m demos.l3.freeze_evidence --check` -> exit 0
3. `bash demos/l3/demo_take.sh` -> 4/4 steps rc=0
4. Video plays from the repo URL, 90s, captions legible
5. No secret in repo: `git grep -nIE 'sk-|ghp_|Bearer '` -> empty
