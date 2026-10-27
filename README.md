# SentinelPulse

**Noise out. Signal triaged.** A tier-0 gate that keeps alert fatigue from reaching your reasoning model.

Every on-call queue fills with events that were never incidents: health checks that returned 200,
favicon 404s, CDN revalidations, blips that self-recovered before anyone looked. Sending those to a
reasoning model costs tokens, latency, and attention — and it is exactly what most "AI SRE" stacks do.

SentinelPulse puts a cheap, deterministic gate in front of the model. Rules drop what is provably
noise at **zero tokens**; only real signal is escalated to tier-1 triage (Meta Muse `muse-spark-1.3`).
The claim is not "the model is smart" — it is that **the model never sees the noise at all**.

---

## What it does

```
webhook -> normalize -> gate-0 (rules, 0 tokens) --> drop   (noise, zero model calls)
                                    |
                                    +--> classify (fast) --> tier-1 triage (muse-spark-1.3)
                                                                  |
                                                                  +--> plan / remediation patch
                                                                  +--> authenticated PR (guarded, opt-in)
```

- **gate-0** — deterministic rules over normalized event fields. Emits `drop` or `tier1` with a stage trail.
- **classify** — fast pass that turns surviving events into a typed incident (severity, service, symptom).
- **tier-1 triage** — Meta Muse model drafts the triage note, hypotheses, and next actions.
- **remediator** — proposes a minimal patch inside a sandbox directory; nothing touches a live repo.
- **PR opener** — authenticated path exists but is **off unless explicitly configured**; the demo never uses it.

## Measured results

Replay of the same 7 fixtures with the gate **off** vs **on**, one run, no cherry-picking:

| # | Claim | Gate off | Gate on | Result |
|---|---|---|---|---|
| C1 | Incidents missed | 0/7 | 0/7 | **no miss introduced** |
| C2 | Noise reaching the reasoning model | 4 calls | **0 calls** | 4 noise items dropped by rules |
| C3 | Tokens per batch | 5533 | **4802** | **−13.2%** |
| C4 | End-to-end latency | 36.8 s | **29.5 s** | **−19.8%** |
| C5 | Signal escalation to tier-1 | 3/3 | 3/3 | unchanged |

Per-fixture routing (gate on) — the four drops are the four that never mattered:

| fixture | label | route | stages | tokens |
|---|---|---|---|---|
| monitor-health-200 | noise | drop | gate-0 | 0 |
| favicon-404 | noise | drop | gate-0 | 0 |
| cdn-revalidate | noise | drop | gate-0 | 0 |
| recovered-blip | noise | drop | gate-0 | 0 |
| checkout-503 | signal | tier1 | gate-1, triage | 1886 |
| nxdomain-api | signal | tier1 | gate-1, triage | 1468 |
| login-overlay | signal | tier1 | gate-1, triage | 1448 |

## Evidence is frozen and checkable

`bench/evidence/FREEZE-20261027.json` pins the artifacts by SHA-256, so the numbers above are tied to
exact bytes rather than to a screenshot:

| artifact | bytes | sha256 |
|---|---|---|
| `bench/evidence/tier0-bench-LIVE-20261027.json` | 10721 | `0071ca3a688c165ec2e1939a8ffc7e50e2f34ef514a23e156749e7011caa1105` |
| `bench/evidence/tier0-bench-20261020.json` | 10777 | `7dd3262e1d6107384b90d6e7e2484ed11bc18d741d32aca9ed7c009acd8dc3e2` |
| `bench/tier0_bench.py` | 8556 | `ef4da6ac4e608c529c4153e11a08e678415fd9fac2dcf3515b21aba9febc78c5` |
| `app/core/gatekeeper.py` | 10679 | `22a42e07472beee5203d4444b3042b45390577c9289527fe1c992a1be3edb948` |

Verify locally — no network, no key:

```bash
python3 -m demos.l3.freeze_evidence --check      # compares sha256, exits non-zero on drift
sha256sum bench/evidence/tier0-bench-LIVE-20261027.json
```

## Quickstart (offline, zero model calls, no key)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # stdlib-first; playwright only for the video renderer

bash demos/l3/demo_take.sh             # freeze check -> build bundle -> render report -> dry live shape
```

`demo_take.sh` is a gate, not a script that "usually works": it aborts with exit 2 if any frozen
artifact drifted, so a passing run proves the numbers still match the bytes.

Outputs land in `demo-bundle/`:

- `bundle.json` — machine-readable claims + evidence pointers
- `report.md` — the claims table and per-fixture routing
- `report.html` — single self-contained file (no CDN, no external JS, air-gap ready)

## Live mode is opt-in and budget-guarded

```bash
export MODEL_API_KEY=...                       # META_MODEL_API_KEY accepted as legacy alias
python3 -m demos.l3.run_live --dry             # prints endpoint/model/ceiling/key-source; 0 credits
python3 -m demos.l3.run_live --confirm-live --calls 8 --tokens 6000
```

- Without `--confirm-live` the runner **refuses** with `exit 4`. Nothing spends by accident.
- `BudgetGuard` reserves per stage against a hard call/token ceiling and raises before overspend.
- `--dry` prints the request shape only: endpoint, model, ceiling, and `resolved from MODEL_API_KEY (not printed)`.

## The 90-second demo

`demo-bundle/sentinelpulse-demo-20261027.mp4` — 1920×1080, 30 fps, burned captions, rendered
end-to-end from real command output:

```bash
bash demos/l3/video/make_video.sh              # capture -> compose -> frames -> ffmpeg + ffprobe gate
```

The renderer never types anything on screen. `capture_terminal.py` runs the real commands, records
stdout, and `compose.py` replays it on a timeline; `render_frames.py` screenshots the timeline in
headless Chromium; ffmpeg encodes and `ffprobe` verifies duration/fps before the file is accepted.

## Repo layout

```
app/core/          meta_client (Muse API), gatekeeper (gate-0 rules), classifier
app/webhook/       intake, normalizers
app/remediator/    sandboxed patch generation, guarded PR publisher
bench/             tier0_bench.py + evidence/*.json + FREEZE manifest
demos/l3/          freeze_evidence, build_bundle, render_report, run_live, demo_take
demos/l3/video/    capture_terminal, compose, render_frames, make_video
demo-bundle/       generated: bundle.json, report.md, report.html, 90s .mp4
docs/              architecture, video script, submission pack
tests/             pipeline + gatekeeper tests
```

## Hackathon scope, stated plainly

Built for the **Meta AI Developer Hackathon** (Oct 26 → Nov 4, 2026), **Indie track**, solo.

- Written **inside** the window: everything under `demos/l3/` (freeze, bundle, report, live runner,
  preflight harness, video renderer) and the `2026-10-27` bench run those claims cite.
- **Pre-window** (disclosed, not hidden): the `app/` gate prototype from Oct 14–20, and the
  `2026-10-20` bench JSON kept only as a regression baseline. The demo measures that prototype with
  in-window tooling; it does not claim the prototype was written during the hackathon.

## Safety

- The API key is read from the environment and never printed, logged, or captured on screen.
- Replay, bundle, report, and video paths run with **no key and no network**.
- The remediator writes only inside a sandbox directory; the PR publisher is opt-in and unconfigured by default.

## License

MIT — see `LICENSE`.
