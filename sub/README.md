# SentinelPulse

**Autonomous, multimodal diagnostics and self-healing agent for web systems.**

SentinelPulse watches a service, decides what actually broke, and produces a
*validated* fix — not a suggestion. It takes incidents from a webhook, reads
server signals and the rendered page with a vision model, and authors a minimal
patch that is applied to a sandbox and gate-checked before a human ever sees it —
then opens a reviewable pull request with the full reasoning attached.

Track: **Indie Developer Track** · Theme: **Push the Frontier (Software Systems & Resilience)**

Full component map, sequence diagram and invariants: **[`ARCHITECTURE.md`](./ARCHITECTURE.md)**

---

## The three pillars

| Pillar | Module | Model | What it does |
|---|---|---|---|
| 1. Orchestration & logic core | `app/core/` | `muse-spark-1.3` | Incident triage, error-signature classification, multi-step remediation planning |
| 2. Multimodal visual inspector | `app/vision/` | `muse-spark-1.3` (VLM) + `sam-3.1` (geometry) | Finds layout breakage, broken asset containers, defacement overlays that text scrapers miss |
| 3. Agentic code remediator | `app/remediator/` | `muse-spark-1.3` + sandbox | Authors a minimal patch, applies it to a sandbox copy, gate-validates, emits a reviewable diff and PR |

## Pipeline

```
   Sentry / Prometheus / Uptime            schedule
                |                            |
                v                            v
   +-----------------------------+   +-----------------------------+
   | 0  INGEST   app/webhook     |   | 1  COLLECT   app/probes     |
   |    :8791 · HMAC-SHA256      |   |    DNS, HTTP, headers, shot |
   |    ack-then-process         |   +-----------------------------+
   |    -> normalizers           |               |
   +-----------------------------+               |
                |                               |
                +---------------+---------------+
                                v
                 +-----------------------------+
                 | 2  TRIAGE    app/core       |  muse-spark-1.3 -> signature, root cause, plan
                 +-----------------------------+
                                |
                                v
                 +-----------------------------+
                 | 3  INSPECT   app/vision     |  VLM reads the rendered pixels (optional)
                 +-----------------------------+
                                |
                                v
                 +-----------------------------+
                 | 4  REMEDIATE app/remediator |  edits -> sandbox -> ast/json/bash/tsc gate -> diff
                 +-----------------------------+
                                |
                                v
                 +-----------------------------+
                 | 5  PUBLISH   gh_publisher   |  branch -> commit -> PR (token scrubbed)
                 +-----------------------------+
                                |
                                v
                 +-----------------------------+
                 | 6  HUMAN REVIEW             |  PR carries diff telemetry, model reasoning,
                 |    merge = the only          |  sandbox transcript. Nothing merges itself.
                 |    irreversible step         |
                 +-----------------------------+
```

**Admission gate** (`.py` AST · `.json` parse · `.sh` `bash -n` · `.ts/.tsx` `tsc`):
a patch that does not pass never leaves the sandbox — the publisher refuses
unvalidated causes, zero-file diffs and missing sandbox trees independently.

## Layout

```
meta-hack/
├── app/
│   ├── core/         meta_client.py (API), incident.py (model), pipeline.py (loop)
│   ├── probes/       health.py — DNS / HTTP / header / screenshot collectors
│   ├── vision/       inspector.py — VLM inspection + SAM 3.1 localisation
│   ├── remediator/   patchgen.py (edits -> sandbox -> gate -> diff)
│   │                 gh_publisher.py (authenticated branch -> commit -> PR)
│   └── webhook/      receiver.py (:8791, HMAC, ack-then-process) · normalizers.py
├── demos/            demo1_reasoning.py · demo2_vision.py · demo3_remediator.py
│                     demo4_e2e_pipeline.py (webhook -> PR, end to end)
├── sub/              README.md · ARCHITECTURE.md (Mermaid + sequence + invariants)
├── tests/            test_pipeline.py · test_ts_gate.py (both offline) · rules_watch.sh
└── runtime/          job ledger, drill logs (generated)
```

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install requests pillow

.venv/bin/python demos/demo1_reasoning.py     # triage 4 real incidents
.venv/bin/python demos/demo2_vision.py        # find injected visual defects (4/4 asserts)
.venv/bin/python demos/demo3_remediator.py    # inject bug -> patch -> sandbox-validate
.venv/bin/python demos/demo4_e2e_pipeline.py  # webhook -> normalizers -> PR (32/32 checks)
.venv/bin/python tests/test_pipeline.py       # offline suite, no API key needed
.venv/bin/python tests/test_ts_gate.py        # offline TS admission gate, 13/13
```

`META_MODEL_API_KEY` must be present in the environment (or resolvable through
`~/.hermes/scripts/env-key.sh`); `GITHUB_PERSONAL_ACCESS_TOKEN` likewise for the
publisher. Both offline suites make **no network calls**.

### Optional strict type-checking

Full `tsc` type-checking is **opt-in** so it can never add latency to the default
detect→PR path:

```bash
# default: syntax (TS1xxx) fails closed, type errors are reported and tolerated
.venv/bin/python demos/demo4_e2e_pipeline.py

# strict, reusing a dependency layer built once per target repo:
SENTINEL_TS_TYPECHECK=1 SENTINEL_TS_LAYER=/path/to/cached/node_modules \
  .venv/bin/python demos/demo4_e2e_pipeline.py
```

With a resolvable layer, `paths."*"` maps bare imports (`react`, `three`, …) at the
cached tree, so type errors become blocking *without* an `npm ci` per job. Ask for
strict mode with no layer and the gate degrades to the default two-tier behaviour
and says so in the log — it does not fail every patch on `TS2307`.

## Engineering notes (measured, not assumed)

Four findings that cost real probe time, all handled in code:

1. **`muse-spark-1.3` is a reasoning model.** Reasoning tokens are private, billed,
   and counted *inside* `max_tokens`. Measured: `max_tokens=1200` →
   `completion_tokens=1200`, `reasoning_tokens=1197`, `message.content == null`.
   The client therefore enforces a floor (`MIN_SAFE_MAX_TOKENS`) and defaults to
   4000, and it reports `truncated_by_reasoning` instead of returning silent empties.
   Depth is tuned with `reasoning_effort` (`minimal`→`max`), not by guessing budgets.

2. **`sam-3.1` is not a chat model.** On `/chat/completions` it returns HTTP 200
   with `content: null` and zero tokens. It is a segmentation model: a noun phrase
   in, boxes and masks out, over the Responses API. Visual *interpretation* belongs
   to `muse-spark-1.3`; SAM is used only to localise a region the VLM flagged.

3. **Models cannot hand-write unified diffs reliably.** `muse-spark-1.3` repeatedly
   produced hunks with invented offsets and context lines missing their leading
   space, which `patch` rejects. The remediator therefore has the model return exact
   find/replace edits and lets `difflib` build the diff — correct by construction,
   with one repair turn on failure. The same lesson applied to *reading*: a blind
   `[:8000]` excerpt silently hid a defect at line ~302 of a 12.4k-char file, so
   excerpts are now windowed (`WINDOW_LINES=70`, `MAX_WINDOWS=5`) around matches.

4. **The sandbox clone has no `node_modules`.** A naive `tsc` run therefore reports
   `TS2307 Cannot find module 'react'` for every import — 58 errors on a file whose
   only real problem was one logic branch. Hence the two-tier gate above: syntax
   blocks, unresolved-import noise does not, unless a cached layer makes the check
   meaningful.

## Status

Day-2 complete and demonstrated live:

**Day 1 — diagnose**
- [x] repo scaffold + isolated venv
- [x] `app/core/meta_client.py` against the verified endpoint
- [x] mock incident harness with genuinely broken fixtures
- [x] three demos, all executing against the live API
- [x] offline test suite
- [x] rules watcher

**Day 2 — act**
- [x] webhook receiver on `:8791` (HMAC-SHA256 guard, ack-then-process, autopilot off by default)
- [x] normalizers: Sentry / Prometheus / Uptime → one `Incident` shape
- [x] authenticated GitHub publisher (branch → commit → PR, token scrubbed from all logs)
- [x] windowed excerpt selection + prefix-stem matching in patchgen
- [x] optional strict type-check gate (isolated flag + cached layer, default path untouched)

**Verification**

| Check | Result |
|---|---|
| End-to-end drill (webhook → PR) | **32/32**, ~33.7s |
| Pipeline regression | **19/19** |
| TypeScript admission gate | **13/13** |
| Live artifact | PR open on `tg-support-landing` — `components/three/HeroScene.tsx`, 1 file, body carries diff telemetry + model reasoning + sandbox transcript |

Next: replay real production incidents through the receiver, and measure
detect→PR latency against a live target.