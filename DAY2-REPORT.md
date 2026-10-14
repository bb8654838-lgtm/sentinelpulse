# SentinelPulse — Day-2 Implementation Report

**Scope:** webhook intake pipeline · GitHub PR opener · end-to-end 2-minute drill
**Date:** 2026-10-14
**Status:** COMPLETE — loop exercised live end to end, 32/32 checks

---

## 1. Webhook intake pipeline (`app/webhook/`)

| Component | File | Contract |
|---|---|---|
| Listener | `app/webhook/receiver.py` | `SentinelWebhookService` — binds `:8791`, `POST /webhook`, `GET /healthz` |
| Dialect translation | `app/webhook/normalizers.py` | Sentry / Prometheus / Uptime payload shapes → `Incident` |
| Dispatch | `receiver.py` worker thread | normalizes → enqueues job → returns `202` immediately |

Design points actually verified at runtime:

- **Acknowledge-then-process.** The HTTP handler normalizes and enqueues, then returns `202`
  with a `job_id`. The model call never runs on the request thread. Measured: ack in
  **milliseconds** while the job itself took ~22 s of model time (`receiver.py` worker).
- **Normalizers translate only.** No root-cause conclusions are drawn from the payload; the
  dialect adapter maps fields to `Incident` and stops. Triage does the reasoning.
- **Autopilot is `off` by default.** Every delivery may override per-request; the default is
  the safe one, asserted as a check.
- **HMAC guard armed when configured.** `X-Sentinel-Signature` verified against
  `SENTINEL_WEBHOOK_SECRET`; bad signature → `401`, armed state surfaced on `/healthz`.
- **Malformed JSON → `400`, receiver survives** (no worker death, process still served the
  next request).

## 2. GitHub PR opener (`app/remediator/gh_publisher.py`)

Pipeline: sandbox-validated patch → new branch → commit → PR with auto-filled body.

- **Refuses unvalidated work.** Three independent refusal checks pass: unvalidated
  remediation, a diff over zero files, and a missing sandbox tree are each rejected with
  `PublishRefused` before any network write.
- **Branch/commit naming** follows the repo convention: `sentinelpulse/<signature>-<shortid>`.
- **PR body auto-fills** diagnostic reasoning summary + diff telemetry:
  - `## Diff telemetry` — files touched, hunks, `+insertions/-deletions`, diff lines
  - `## Diagnostic reasoning (model telemetry)` — `model: muse-spark-1.3`, triage/patch
    reasoning tokens, latency, rationale
  - `## Validation` — the sandbox AST/config transcript
  - symptom, root cause, remediation plan, verification steps, rollback

### Defect fixed this day — git push authentication

**Symptom:** `push failed: fatal: could not read Username for 'https://github.com': No such
device or address`. PR creation would have worked; **the push never did.**

**Root cause:** API calls used `self.token`, but `git clone`/`git push` used the bare
`https://github.com/<repo>.git` remote. A non-interactive process has no credential prompt,
so git aborted.

**Fix (surgical):**

1. New `_auth_remote_url()` bakes `https://x-access-token:<token>@github.com/<repo>.git`
   **in memory only**; `SENTINEL_GIT_REMOTE` overrides it for an ssh/agent remote.
2. Clone path uses the auth URL; the reuse path re-points `origin` via
   `git remote set-url` so a stale unauthenticated remote can't resurface.
3. `_git()` now scrubs `//user:pass@` from all captured output **before** it can reach a log,
   an exception message, or an audit record.
4. Dry-run with no credential falls back to the public remote, so the local
   clone/apply/commit contract is still exercised.

**Safety:** no credential is written to disk or printed. Every git line passes through
`_redact()` / the `_git()` scrubber.

## 3. Second defect fixed — patchgen excerpt truncation

**Symptom:** the E2E drill failed to find the defect. `patchgen.py` sliced the target file
with `[:8000]`, hiding line ~302 in a 12.4 k-char file.

**Fix:** replaced the blind head-slice with line-windowed excerpts.

- Files under `MAX_EXCERPT_CHARS=24000` are sent whole.
- Larger files are windowed (`WINDOW_LINES=70`, `MAX_WINDOWS=5`) around lines matching the
  incident vocabulary, with explicit `_OMITTED` markers showing elision.
- Hint matching uses prefix-stem logic (`h[:5]`) so `demotes` matches `demoting` — an exact
  substring match silently dropped the hit.

Verified against a real 12.4 k-char file (whole, true line numbers) and a synthetic 79 k-char
file (defect at line 901 captured in one 8.7 k-char window).

## 4. End-to-end drill (`demos/demo4_e2e_pipeline.py`)

Two-minute loop: webhook trigger → incident triage → visual/log check → sandbox patch →
GitHub PR.

```
checks  : 32/32
wall    : 33.5s
verdict : COMPLETE — full loop exercised end to end
```

Live artefact produced by the drill (independently re-read from the GitHub API, not from the
drill's own output):

- **PR #3** — https://github.com/bb8654838-lgtm/tg-support-landing/pull/3
  - branch `sentinelpulse/quality-tier-premature-demote-fb1bc2`, commit `9219e8fd12`, base `main`
  - `state=open`, exactly one file: `components/three/HeroScene.tsx`
  - body carries the diff-telemetry block, the model-telemetry block, the `muse-spark-1.3`
    stamp, and the sandbox transcript

### Defect remediated (real, in `tg-support-landing`)

`components/three/HeroScene.tsx` — the adaptive-quality controller demoted the scene to the
`lite` tier after a *single* bad frame sample, while the code comment and the product intent
call for **two consecutive** bad samples. Effect: one transient stall permanently disabled
bloom/particles for the session.

Generated patch adds `const badSamples = useRef(0)`, increments the counter on a sub-28 fps
sample, gates demotion behind `badSamples.current >= 2`, and resets the counter on any good
sample to enforce consecutiveness.

### Honest limitation

The sandbox `tsc` transcript reports `errors=58`, of which **syntax=0 and type=58** — every
one is `TS2307 Cannot find module 'react'`, because the sandbox tree is a bare clone with no
`node_modules`. So the gate proves **syntax/AST validity** in the sandbox; it does not prove
the type-check of the patched package. The substantive fix is confirmed by reading the patch
back out of the sandbox tree, not by the type-checker.

## 5. Regression state

- `tests/test_pipeline.py` — **19/19 checks passed** after the `patchgen` changes.
- Dry-run / refusal paths re-exercised; no live write on any refusal path.

## 6. Open items

- The live sandbox has no `node_modules`; a per-job `npm ci` (or a cached dependency volume)
  would upgrade the admission gate from syntax-only to full type-check.
- The drill ran against a single defect signature. Multi-defect batching is untested.