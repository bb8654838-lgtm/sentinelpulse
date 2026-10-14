#!/usr/bin/env python3
"""
DEMO 1 — Orchestration & logic core            (muse-spark-1.3)
=============================================================
Takes every mock incident through probe -> triage and prints the machine-readable
verdict: signature, root cause, blast radius, ordered remediation plan.

Run:  .venv/bin/python demos/demo1_reasoning.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.incident import Incident, IncidentFactory, Severity, Source  # noqa: E402
from app.core.meta_client import MetaClient  # noqa: E402
from app.core.pipeline import SentinelPipeline  # noqa: E402
from app.probes import health  # noqa: E402
from demos.mock_incidents import BAD_URL, FIXTURES, NX_HOST, OK_URL, build_fixtures  # noqa: E402


def rule(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def main() -> int:
    build_fixtures()
    client = MetaClient()
    pipe = SentinelPipeline(client)

    rule("PILLAR 1 :: ORCHESTRATION & LOGIC CORE  (muse-spark-1.3)")
    print(f"model check : {[m for m in client.models() if 'spark' in m or 'wonder' in m]}")
    print("pipeline    : collect -> triage -> (inspect) -> (remediate) -> verdict")

    incidents: list[Incident] = []

    # --- incident A: guaranteed NXDOMAIN -----------------------------------
    inc_a = health.probe_dns(NX_HOST) or IncidentFactory.nxdomain(NX_HOST, "unexpected: resolved")
    incidents.append(inc_a)

    # --- incident B: real upstream 5xx -------------------------------------
    inc_b, metrics_b = health.probe_http(BAD_URL)
    if inc_b is None:
        print(f"[warn] {BAD_URL} returned {metrics_b.get('status')} — treating as healthy")
    else:
        incidents.append(inc_b)

    # --- incident C: real hardening-header gap -----------------------------
    inc_c, hdr_metrics = health.probe_headers(OK_URL)
    if inc_c:
        incidents.append(inc_c)
    else:
        print(f"[info] {OK_URL} has all hardening headers — no incident")

    # --- incident D: visual anomaly (fed from the rendered fixture) --------
    inc_d = IncidentFactory.visual("mock://acme-ops/dashboard",
                                   str(FIXTURES / "dashboard_broken.png"),
                                   "operator reported 'page looks wrong'")
    incidents.append(inc_d)

    for inc in incidents:
        rule(f"INCIDENT {inc.incident_id} :: {inc.source.value.upper()}")
        print(inc.brief())
        t0 = time.time()
        tr = pipe.triage(inc)
        print(f"\n--- triage ({time.time() - t0:.1f}s, reasoning_tokens={tr.reasoning_tokens}) ---")
        if tr.error:
            print(f"  ERROR: {tr.error}")
            continue
        print(f"  signature     : {tr.signature}  (confidence {tr.confidence})")
        print(f"  root_cause    : {tr.root_cause}")
        print(f"  blast_radius  : {tr.blast_radius}")
        print(f"  auto_fixable  : {tr.auto_fixable}")
        print(f"  verification  : {tr.verification}")
        print("  plan:")
        for i, step in enumerate(tr.plan, 1):
            print(f"    {i}. {step}")

    print(f"\n{'=' * 78}")
    print(f"DEMO 1 complete — {len(incidents)} incidents triaged through muse-spark-1.3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())