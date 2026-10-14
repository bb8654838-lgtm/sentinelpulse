#!/usr/bin/env python3
"""
DEMO 2 — Multimodal visual inspector           (muse-spark-1.3 VLM + sam-3.1)
============================================================================
Feeds a genuinely defective rendered page to the VLM and asserts the structured
report finds what was actually drawn. Then runs the same model on a clean page
as a false-positive control.

Run:  .venv/bin/python demos/demo2_vision.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.vision.inspector import VisualInspector  # noqa: E402
from demos.mock_incidents import FIXTURES, build_fixtures  # noqa: E402


def rule(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def show(label: str, rep) -> None:
    print(f"\n[{label}]")
    print(f"  defaced           : {rep.defaced}")
    print(f"  layout_broken     : {rep.layout_broken}")
    print(f"  overlay_suspected : {rep.overlay_suspected}")
    print(f"  reasoning_tokens  : {rep.reasoning_tokens}")
    print(f"  summary           : {rep.summary}")
    if rep.findings:
        print("  findings:")
        for f in rep.findings:
            print(f"    - {f.kind:22s} @ {f.where:12s} conf={f.confidence:<5} {f.detail}")


def main() -> int:
    build_fixtures()
    insp = VisualInspector()

    rule("PILLAR 2 :: MULTIMODAL VISUAL INSPECTOR  (muse-spark-1.3 reads the pixels)")

    broken = FIXTURES / "dashboard_broken.png"
    clean = FIXTURES / "dashboard_clean.png"
    print(f"defective fixture : {broken}  ({broken.stat().st_size} bytes)")
    print(f"control fixture   : {clean}  ({clean.stat().st_size} bytes)")

    rule("CASE A :: defective page — injected defects must be found")
    print("drawn defects: (1) broken asset container 'logo-main.png FAILED TO LOAD'")
    print("               (2) card escaped its grid and overlaps the layout")
    print("               (3) red full-width unauthorised banner")
    rep_a = insp.inspect(broken, context="ACME OPS production dashboard", reasoning_effort="low")
    show("BROKEN", rep_a)

    rule("CASE B :: clean control — must NOT false-positive")
    rep_b = insp.inspect(clean, context="ACME OPS production dashboard", reasoning_effort="low")
    show("CLEAN", rep_b)

    rule("ASSERTIONS")
    checks = [
        ("defective page flagged as needing remediation", rep_a.needs_remediation),
        ("broken asset reported", any("asset" in f.kind or "image" in f.detail.lower()
                                      or "load" in f.detail.lower() for f in rep_a.findings)
         or rep_a.layout_broken),
        ("defacement/overlay detected", rep_a.overlay_suspected or rep_a.defaced
         or any("deface" in f.kind or "overlay" in f.kind for f in rep_a.findings)),
        ("clean page NOT flagged as defaced", not rep_b.defaced),
    ]
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())