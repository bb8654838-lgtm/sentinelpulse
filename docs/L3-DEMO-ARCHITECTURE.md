# L3 — DEMO-EVIDENCE LANE (SentinelPulse · Track: Indie)

Status: **scaffolded, offline path green** (2026-10-27). Track: **Indie Developer** (locked).
Portal rules: **CONFIRMED 2026-10-27** — see §8; no open assumption remains. Verified against the
Kickoff slide text, the live event page (`cerebralvalley.ai/e/meta-ai-developer-hackathon/live`)
and the official workshop repo README (raw fetch, HTTP 200).

---

## 1. Why this lane exists

L2 proved the gate works (7 fixtures, 0 incident misses, tokens 5533 → 4802, noise calls 4 → 0).
L2 proof is machine-readable but a judge cannot *watch* a JSON file. L3 exists to convert frozen
proof into something a human can audit in 90 seconds, while keeping the numbers welded to the
evidence file so the demo cannot drift from reality.

Operating principle: **the demo never types a number.** Every figure on screen is read from
`bench/evidence/tier0-bench-LIVE-20261027.json` through `demo-bundle/bundle.json`.

---

## 2. Three layers

```
  ┌──────────────── L1 evidence (frozen) ────────────────┐
  │ bench/evidence/tier0-bench-LIVE-20261027.json         │
  │ bench/evidence/FREEZE-20261027.json  (sha256 manifest)│
  └───────────────────────┬──────────────────────────────┘
                          │ read-only, hash-locked
  ┌──────────────── L2 runner ───────────────────────────┐
  │ replay:  demos/l3/build_bundle.py   (0 credits)       │
  │ live:    demos/l3/run_live.py       (<=8 calls, opt-in)│
  │ guard:   demos/l3/budget.py         (pre-call ceiling) │
  └───────────────────────┬──────────────────────────────┘
                          │ bundle.json (single source of truth)
  ┌──────────────── L3 presentation ─────────────────────┐
  │ demos/l3/render_report.py -> demo-bundle/report.html  │
  │ 90s capture script -> docs/L3-VIDEO-SCRIPT.md         │
  └───────────────────────────────────────────────────────┘
```

Why three layers: judges get a video, reviewers get an HTML audit trail, and an engineer gets a
JSON they can diff. One artifact per audience, one evidence root.

---

## 3. Spend model

| Mode | Command | Credits | Ceiling |
|---|---|---|---|
| replay | `python -m demos.l3.build_bundle` | 0 | n/a |
| render | `python -m demos.l3.render_report` | 0 | n/a |
| live | `python -m demos.l3.run_live --confirm-live` | yes | 10 calls / 8000 tokens hard-capped in code |

`BudgetGuard.reserve()` fires **before** the request leaves the process, so an over-budget run
aborts without spending. Verified: third call on a 2-call guard raises `BudgetExceeded`.
`run_live.py` without `--confirm-live` exits 4 and says why.

---

## 4. Evidence discipline

- `freeze_evidence.py` hashes the live bench JSON, the prior bench JSON, the bench harness and
  `gatekeeper.py`, writes `FREEZE-<YYYYMMDD>.json`, and marks **only evidence payloads** read-only.
- Re-running is idempotent. A changed hash on a frozen artifact exits 2 — a freeze violation, never
  a silent overwrite.
- `--check` verifies without writing (used in CI-style demo preflight).
- Source files stay writable: the sprint must still iterate; only the *numbers* are pinned.

---

## 5. Bundle contract (`demo-bundle/bundle.json`)

| key | meaning |
|---|---|
| `track` | `indie` |
| `mode` | `replay` \| `live` |
| `freeze.artifacts[]` | path, bytes, sha256, mtime of every frozen input |
| `bench.arms.*` | gate-off vs gate-on totals: calls, tokens, latency, noise calls, rules-dropped |
| `bench.per_fixture[]` | fixture, label, note, route, stages, tokens, signature |
| `claims[]` | C1..C5 — human sentence + measured value + evidence path |
| `screenshots[]` | rendered artifacts with sha256 |
| `reproduce[]` | the three commands that regenerate the bundle |

Claims are generated, not written: change the evidence and the claims change.

---

## 6. Gates

- **G3 (demo ready):** `FREEZE-*.json` intact **and** `bundle.json` regenerated from it **and**
  `report.html` renders with all sections **and** 90s video exported.
- **G4 (submission ready):** G3 + write-up + repo URL + portal rule checklist signed off.
- Blocking rule: if live capture routing disagrees with replay routing on any fixture, the demo is
  **not** recorded until the divergence is explained. A routing mismatch is a bug, not a number.

---

## 7. File map

```
meta-hack/
  bench/evidence/FREEZE-20261027.json          hash manifest (frozen inputs)
  bench/evidence/tier0-bench-LIVE-20261027.json L2 live proof
  demos/l3/__init__.py                          package docstring / lane contract
  demos/l3/freeze_evidence.py                   freeze + verify
  demos/l3/budget.py                            pre-call spend ceiling
  demos/l3/build_bundle.py                      replay -> bundle.json + report.md
  demos/l3/render_report.py                     bundle.json -> self-contained report.html
  demos/l3/run_live.py                          opt-in, budget-capped live capture
  demos/fixtures/l3-report-20261027.png         rendered proof (2x DPR, full page)
  demo-bundle/{bundle.json,report.md,report.html}
  docs/L3-DEMO-ARCHITECTURE.md                  this file
  docs/L3-VIDEO-SCRIPT.md                       90s beat sheet + capture commands
```

---

## 8. Rules — CONFIRMED (2026-10-27)

Every former assumption is resolved. The evidence column names the verification, not a
recollection.

| # | was | now | evidence |
|---|-----|-----|----------|
| 1 | video vs live judge | **CONFIRMED** — demo video is a scored criterion ("Demo 25%", wording covers the quality of the demo video); the live path stays runnable in case of a finals call | live event page (HTTP 200) |
| 2 | model cost on entrant | **CONFIRMED (operational)** — entrant uses their own `MODEL_API_KEY`; our default path spends 0 credits, so the question cannot bite at judging | `agent/.env.example` |
| 3 | deliverables | **CONFIRMED** — **public GitHub repository is mandatory**, plus write-up; one track per project | Kickoff slide (SAM-verified) |
| 4 | vendor allow-list | **CONFIRMED** — Meta Model API is the sanctioned surface (`api.meta.ai/v1`, `muse-spark-1.3`) and "Use of Meta AI Tooling" is 25% of the score, so Meta calls must be visible on screen | event page + `.env.example` |
| 5 | deadline | **CONFIRMED** — submissions close **2026-11-04, 9:00 PM ET**; judging Nov 4–8; finals/closing Nov 9. Work window Oct 26 → Nov 4, **new work only** | event page + Kickoff slide |
| 6 | track | **CONFIRMED** — **Indie Developer track**: individual builders and students, teams up to 4 (solo welcome), judged on creativity, technical ambition, and whether it works; $400k pool in-track; one track per participant/project | event page |

Still `[OPEN]` — none of these block the recording:

- `[OPEN]` Maximum video length. Not stated in the text we hold. Cut to 90s; a shorter file is
  never penalised. Resolve at upload if the form enforces a cap.
- `[OPEN]` Repo public at submission vs at judging. Assume **at submission** (conservative).
- `[OPEN]` Muse Code runtime beyond `MODEL_API_KEY` / `MODEL_API_BASE_URL` / `MODEL_API_MODEL`.
  `app/core/meta_client.py` now reads `MODEL_API_KEY` first (legacy name still honoured), so the
  env file the workshop hands out works unchanged.

Judging weights (grounded) and what each demands of the cut:

| weight | criterion | what the video must contain |
|---|---|---|
| 30% | Impact | the cost/noise problem in one sentence, with 13.2% and 0/7 on screen |
| 25% | Use of Meta AI Tooling | model id + a real call signature visible; gate decisions made by `muse-spark-1.3` |
| 25% | Demo | terminal run → report in browser, no edits in between, artifact hashes shown |
| 20% | Technical Depth & Execution | architecture beat: freeze → replay → render, tests green |

---

## 9. Window compliance — new rule, was not in §8

The Kickoff slide adds a constraint the earlier assumptions missed: **"Demo only what was built
during the hackathon window."** Hard constraint; it changes what the video may show.

1. **Window** = 2026-10-26 00:00 → 2026-11-04 21:00 ET. Everything shown must be created inside it.
2. **The 10-20 bench reading is pre-window.** It stays in the repo as provenance and stays frozen,
   but it is never presented as demo output and no claim derives from it. The on-screen comparison
   is **gate-off vs gate-on inside the 10-27 run** — both arms in-window (§5).
3. **Third-party code is disclosed, not demoed.** The workshop repo is cited as the runtime
   contract (`MODEL_API_KEY`, `api.meta.ai/v1`), never presented as our work.
4. **Repo honesty:** the README states which parts were authored inside the window. This project's
   first commit is 10-27, so the answer is "all of it" — say it plainly.

---

## 10. Failure modes, pre-empted

| failure | pre-emption |
|---|---|
| key missing at judging | replay default needs no key; live path prints the refusal, rc=4 |
| no browser on judge box | report.html is static — any browser; committed PNG fallback |
| fresh clone | `--check` + `build_bundle` need only Python stdlib + repo files |
| numbers drift on re-run | claims are recomputed; drift is a visible bug, not a hidden one |
| "is this a mock?" | every metric row carries fixture id + sha256 of the evidence it came from |
