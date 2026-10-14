# SentinelPulse

**Autonomous, multimodal diagnostics and self-healing agent for web systems.**

SentinelPulse watches a service, decides what actually broke, and produces a
*validated* fix — not a suggestion. It reads server logs and DNS/HTTP failures,
looks at the rendered page with a vision model, and authors a minimal code or
config patch that is applied to a sandbox and syntax-checked before a human ever
sees it.

Track: **Indie Developer Track** · Theme: **Push the Frontier (Software Systems & Resilience)**

---

## The three pillars

| Pillar | Module | Model | What it does |
|---|---|---|---|
| 1. Orchestration & logic core | `app/core/` | `muse-spark-1.3` | Incident triage, error-signature classification, multi-step remediation planning |
| 2. Multimodal visual inspector | `app/vision/` | `muse-spark-1.3` (VLM) + `sam-3.1` (geometry) | Finds layout breakage, broken asset containers, defacement overlays that text scrapers miss |
| 3. Agentic code remediator | `app/remediator/` | `muse-spark-1.3` + sandbox | Authors a minimal patch, applies it to a sandbox copy, AST-validates, emits a reviewable diff |

## Pipeline

```
        webhook / schedule
                |
                v
   +-----------------------------+
   | 1  COLLECT   app/probes     |   DNS, HTTP, hardening headers, screenshot
   +-----------------------------+
                |
                v
   +-----------------------------+
   | 2  TRIAGE    app/core       |   muse-spark-1.3 -> signature, root cause, plan
   +-----------------------------+
                |
                v
   +-----------------------------+
   | 3  INSPECT   app/vision     |   VLM reads the rendered pixels (optional)
   +-----------------------------+
                |
                v
   +-----------------------------+
   | 4  REMEDIATE app/remediator |   edits -> sandbox -> ast/json/bash check -> diff
   +-----------------------------+
                |
                v
   +-----------------------------+
   | 5  REPORT    Verdict        |   single JSON object an operator or CI can act on
   +-----------------------------+
```

## Layout

```
meta-hack/
├── app/
│   ├── core/         meta_client.py (API), incident.py (model), pipeline.py (loop)
│   ├── probes/       health.py — DNS / HTTP / header / screenshot collectors
│   ├── vision/       inspector.py — VLM inspection + SAM 3.1 localisation
│   └── remediator/   patchgen.py — edit -> sandbox -> validate -> diff
├── demos/            demo1_reasoning.py · demo2_vision.py · demo3_remediator.py
├── sub/              README, architecture, submission docs
└── tests/            test_pipeline.py (offline) · rules_watch.sh
```

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install requests pillow

.venv/bin/python demos/demo1_reasoning.py     # triage 4 real incidents
.venv/bin/python demos/demo2_vision.py        # find injected visual defects (4/4 asserts)
.venv/bin/python demos/demo3_remediator.py    # inject bug -> patch -> sandbox-validate
.venv/bin/python tests/test_pipeline.py       # offline suite, no API key needed
```

`META_MODEL_API_KEY` must be present in the environment (or resolvable through
`~/.hermes/scripts/env-key.sh`). Nothing in the offline suite makes a network call.

## Engineering notes (measured, not assumed)

Two facts about the Meta Model API that cost real probe time, both handled in code:

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

A third finding drove an architecture change: **models cannot hand-write unified
diffs reliably.** `muse-spark-1.3` repeatedly produced hunks with invented offsets
and context lines missing their leading space, which `patch` rejects. The remediator
therefore has the model return exact find/replace edits and lets `difflib` build the
diff — correct by construction, with one repair turn on failure.

## Status

Pre-kickoff scaffold. Day-1 deliverables complete and demonstrated live:

- [x] repo scaffold + isolated venv
- [x] `app/core/meta_client.py` against the verified endpoint
- [x] mock incident harness with genuinely broken fixtures
- [x] three demos, all executing against the live API
- [x] offline test suite
- [x] rules watcher

Next: webhook intake + GitHub PR opener (the 2-minute detect→PR path), pending
official rules.