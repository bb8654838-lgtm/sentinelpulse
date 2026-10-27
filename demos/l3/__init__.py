"""SentinelPulse :: L3 demo lane package.

Lane L3 (DEMO-EVIDENCE) turns frozen evidence into a submission-ready bundle:
  freeze   -> bench/evidence/FREEZE-<date>.json  (hash-locked L2 inputs)
  build    -> demo-bundle/bundle.json            (single machine-readable truth)
  render   -> demo-bundle/report.html            (self-contained, no network)
  live     -> demo-bundle/live-<stamp>.json      (budget-capped, opt-in only)

Nothing in this package may spend credits without BudgetGuard approval.
"""

__all__ = ["budget", "build_bundle", "render_report", "freeze_evidence"]
