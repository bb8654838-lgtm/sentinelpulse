# SentinelPulse — demo bundle (replay)

Track: **indie** · generated 2026-10-27T17:30:21+00:00

## Claims and the evidence behind them

- **C1** Zero incident misses with the gate on — 0/7 missed  
  evidence: `bench/evidence/tier0-bench-LIVE-20261027.json`
- **C2** Noise never reaches the reasoning model — 0 noise calls, 4 rules-dropped  
  evidence: `bench/evidence/tier0-bench-LIVE-20261027.json`
- **C3** Token spend falls per incident batch — 5533 -> 4802 (13.2% less)  
  evidence: `bench/evidence/tier0-bench-LIVE-20261027.json`
- **C4** End-to-end latency falls — 36.8s -> 29.5s  
  evidence: `bench/evidence/tier0-bench-LIVE-20261027.json`
- **C5** Signal always escalates to tier-1 triage — 3/3 signal fixtures escalated  
  evidence: `bench/evidence/tier0-bench-LIVE-20261027.json`

## Per-fixture routing (gate ON)

| fixture | label | route | stages | tokens |
|---|---|---|---|---|
| monitor-health-200 | noise | drop | gate-0 | 0 |
| favicon-404 | noise | drop | gate-0 | 0 |
| cdn-revalidate | noise | drop | gate-0 | 0 |
| recovered-blip | noise | drop | gate-0 | 0 |
| checkout-503 | signal | tier1 | gate-1, triage | 1886 |
| nxdomain-api | signal | tier1 | gate-1, triage | 1468 |
| login-overlay | signal | tier1 | gate-1, triage | 1448 |

## Freeze

- manifest: `bench/evidence/FREEZE-20261027.json`
- frozen_at: 2026-10-27T15:27:16+00:00

