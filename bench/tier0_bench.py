#!/usr/bin/env python3
"""
SentinelPulse :: tier-0 gate A/B benchmark  (bench/tier0_bench.py)

Measures the stage-0/1 gate against the baseline on a 7-fixture labelled set,
using the LIVE model API. Two arms, same fixtures, same process:

  baseline : gate off  -> every incident goes straight to tier 1 (muse-spark-1.3)
  tiered   : gate on   -> rules clear static/monitor noise (0 tokens),
                          tier-0 model fast-drops the rest, real incidents reach tier 1

The number that matters is LAST: any signal fixture that the gate dropped is an
INCIDENT MISS (must be 0). Escalating noise is only wasted spend, not a failure.

Writes runtime/tier0-bench.json after every fixture so a timeout loses nothing.
Run:  .venv/bin/python bench/tier0_bench.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.incident import Incident, IncidentFactory, Severity, Source  # noqa: E402
from app.core.pipeline import SentinelPipeline  # noqa: E402

OUT = ROOT / "runtime" / "tier0-bench.json"


@dataclass
class Fixture:
    key: str
    label: str          # "noise" | "signal"
    note: str
    build: Callable[[], Incident]
    expect_stage: str   # "rules" | "model" | "tier1"  (routing expectation, informational)


def _monitor_200() -> Incident:
    inc = Incident(target="https://shop.example.com/health",
                   summary="uptime monitor probe returned 200 OK", severity=Severity.LOW,
                   source=Source.HTTP)
    inc.add("http", "200 OK from synthetic monitor", status=200)
    return inc


def _favicon() -> Incident:
    inc = Incident(target="https://shop.example.com/favicon.ico",
                   summary="favicon.ico request returned 404", severity=Severity.LOW,
                   source=Source.HTTP)
    inc.add("http", "404 for a static asset", status=404)
    return inc


def _cdn_revalidate() -> Incident:
    inc = Incident(target="https://cdn.example.com/assets/app.js",
                   summary="cdn cache miss revalidation for /assets/app.js", severity=Severity.LOW,
                   source=Source.HTTP)
    inc.add("http", "200 after revalidation", status=200)
    return inc


def _recovered_blip() -> Incident:
    inc = Incident(target="https://api.example.com/v2/orders",
                   summary="synthetic monitor saw a transient 502 that recovered on the next retry",
                   severity=Severity.MEDIUM, source=Source.HTTP)
    inc.add("http", "502 then 200 on retry", status=502, recovered_on_retry=True)
    return inc


def _checkout_503() -> Incident:
    return IncidentFactory.http_5xx("https://api.example.com/checkout", 503,
                                    body_head="upstream pool exhausted")


def _nxdomain() -> Incident:
    return IncidentFactory.nxdomain("api.client.example.com", resolver_note="SERVFAIL upstream")


def _defacement() -> Incident:
    inc = Incident(target="https://portal.example.com/login",
                   summary="injected overlay on the login form captures credentials",
                   severity=Severity.HIGH, source=Source.VISUAL)
    inc.add("visual", "unexpected fixed-position iframe over the password field", overlay=True)
    return inc


FIXTURES = [
    Fixture("monitor-health-200", "noise", "synthetic monitor, 200 OK", _monitor_200, "rules"),
    Fixture("favicon-404", "noise", "static asset 404", _favicon, "rules"),
    Fixture("cdn-revalidate", "noise", "CDN cache revalidation", _cdn_revalidate, "rules"),
    Fixture("recovered-blip", "noise", "transient 502, recovered", _recovered_blip, "model"),
    Fixture("checkout-503", "signal", "checkout upstream 503", _checkout_503, "tier1"),
    Fixture("nxdomain-api", "signal", "api hostname NXDOMAIN", _nxdomain, "tier1"),
    Fixture("login-overlay", "signal", "credential-capture overlay", _defacement, "tier1"),
]


ARMS: list["Arm"] = []


@dataclass
class Arm:
    name: str
    rows: list = field(default_factory=list)

    @property
    def calls(self) -> int:
        return sum(r["model_calls"] for r in self.rows)

    @property
    def tokens(self) -> int:
        return sum(r["tokens"] for r in self.rows)

    @property
    def latency(self) -> float:
        return round(sum(r["latency_s"] for r in self.rows), 1)


def run_arm(name: str, gate_enabled: bool) -> Arm:
    arm = Arm(name)
    ARMS.append(arm)
    print(f"\n{'=' * 78}\nARM: {name}  (gate_enabled={gate_enabled})\n{'=' * 78}")
    for fx in FIXTURES:
        p = SentinelPipeline(gate_enabled=gate_enabled)
        inc = fx.build()
        t0 = time.time()
        try:
            v = p.run(inc)
            err = ""
        except Exception as exc:  # noqa: BLE001
            v, err = None, f"{type(exc).__name__}: {exc}"
        dt = round(time.time() - t0, 1)

        raw_stages = list(v.stages) if v else []
        stages = [s["stage"] for s in raw_stages]
        tokens = sum(s.get("tokens", 0) for s in raw_stages)
        triage = (v.triage if v else {}) or {}
        routed = ("drop" if "TIER0_DROPPED" in str(triage.get("signature", ""))
                  else ("tier1" if "triage" in stages else "?"))
        row = {"fixture": fx.key, "label": fx.label, "note": fx.note,
               "stages": stages, "stage_detail": raw_stages, "routed": routed,
               "model_calls": sum(1 for s in raw_stages if s.get("model")),
               "tokens": tokens, "latency_s": dt, "ok": bool(v and v.ok), "error": err,
               "signature": triage.get("signature", "")}
        arm.rows.append(row)

        verdict = "OK"
        if fx.label == "signal" and routed == "drop":
            verdict = "INCIDENT MISS"
        elif fx.label == "noise" and routed == "tier1":
            verdict = "spend"
        print(f"  {fx.key:18} {fx.label:6} -> {routed:6} {'+'.join(stages) or '-':14} "
              f"tok={tokens:5} {dt:6}s {verdict}"
              + (f"  [{err[:40]}]" if err else ""))
        OUT.write_text(json.dumps({"arms": {a.name: a.rows for a in ARMS}}, indent=2))
    return arm


def summarize(arm: Arm) -> dict:
    noise = [r for r in arm.rows if r["label"] == "noise"]
    signal = [r for r in arm.rows if r["label"] == "signal"]
    by_stage = {}
    for r in arm.rows:
        for s in r.get("stage_detail", []):
            if s.get("model"):
                acc = by_stage.setdefault(s["stage"], {"calls": 0, "tokens": 0})
                acc["calls"] += 1
                acc["tokens"] += s.get("tokens", 0)
    return {
        "model_calls": arm.calls,
        "tokens": arm.tokens,
        "latency_s": arm.latency,
        "tokens_by_stage": by_stage,
        "noise_escalated": sum(1 for r in noise if r["routed"] == "tier1"),
        "calls_on_noise": sum(r["model_calls"] for r in noise),
        "rules_dropped": sum(1 for r in arm.rows if r["stages"] == ["gate-0"]),
        "INCIDENT_MISSES": [r["fixture"] for r in signal if r["routed"] == "drop"],
        "errors": [r["fixture"] for r in arm.rows if r["error"]],
    }


if __name__ == "__main__":
    base = run_arm("baseline_gate_off", gate_enabled=False)
    tiered = run_arm("tiered_gate_on", gate_enabled=True)

    sb, st = summarize(base), summarize(tiered)
    print(f"\n{'=' * 78}\nSUMMARY  ({len(FIXTURES)} fixtures: 4 noise, 3 signal)\n{'=' * 78}")
    print(f"  {'metric':26} {'baseline':>12} {'tiered':>12} {'delta':>12}")
    for k in ("model_calls", "tokens", "latency_s", "calls_on_noise", "rules_dropped",
              "noise_escalated"):
        b, t = sb[k], st[k]
        print(f"  {k:26} {b:>12} {t:>12} {t - b:>+12}")
    print(f"  {'INCIDENT MISSES':26} {len(sb['INCIDENT_MISSES']):>12} "
          f"{len(st['INCIDENT_MISSES']):>12}   <- must stay 0")
    if st["INCIDENT_MISSES"]:
        print(f"\n  !! FAIL: real incidents dropped by the gate: {st['INCIDENT_MISSES']}")
    for arm_name, s in (("baseline", sb), ("tiered", st)):
        for stage, acc in sorted(s["tokens_by_stage"].items()):
            print(f"  {arm_name:8} {stage:8} calls={acc['calls']:>2} tokens={acc['tokens']:>6}")
    if sb["tokens"]:
        print(f"\n  token reduction: {100 * (1 - st['tokens'] / sb['tokens']):.1f}%")
    OUT.write_text(json.dumps({"summary": {"baseline": sb, "tiered": st},
                               "arms": {a.name: a.rows for a in ARMS}}, indent=2))
    print(f"\n  artifact: {OUT}")
    raise SystemExit(1 if st["INCIDENT_MISSES"] else 0)