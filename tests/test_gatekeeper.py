#!/usr/bin/env python3
"""
SentinelPulse :: stage-0/1 gate test suite  (tests/test_gatekeeper.py)

Offline — no API key, no network. Guards the tiered-triage contract:

  stage 0  rules clear routine monitor/static noise with ZERO model calls
  stage 1  a benign verdict at/above threshold drops, everything else escalates
  FAIL-OPEN an error / reasoning-only / unparseable tier-0 reply escalates

Run:  .venv/bin/python tests/test_gatekeeper.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.gatekeeper import Tier0Gate, rule_scan  # noqa: E402
from app.core.incident import Incident, IncidentFactory, Severity, Source  # noqa: E402
from app.core.meta_client import MetaClient  # noqa: E402
from app.core.pipeline import SentinelPipeline  # noqa: E402


class FakeClient(MetaClient):
    """Records every call; replays queued replies (an Exception instance raises)."""

    def __init__(self, served=("muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.1"),
                 replies=()):
        self.api_key = "test"
        self._served = list(served)
        self._replies = list(replies)
        self.calls: list[dict] = []

    def models(self):
        return list(self._served)

    def chat(self, prompt, *, model="muse-spark-1.3", reasoning_effort="low", max_tokens=0, **kw):
        self.calls.append({"model": model, "max_tokens": max_tokens, "prompt": prompt})
        if not self._replies:
            raise AssertionError("fake client has no queued reply")
        item = self._replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def reply(text, *, reasoning=0, completion=60, prompt=210):
    return SimpleNamespace(text=text, model="fake", reasoning_tokens=reasoning,
                           completion_tokens=completion if text.strip() else 0,
                           prompt_tokens=prompt,
                           truncated_by_reasoning=(not text.strip() and reasoning > 0))


def benign(conf, reason="routine monitor hit"):
    return reply('{"benign": true, "confidence": %s, "reason": "%s"}' % (conf, reason))


def signal(conf=0.9, reason="real 5xx on the user path"):
    return reply('{"benign": false, "confidence": %s, "reason": "%s"}' % (conf, reason))


def monitor_noise() -> Incident:
    inc = Incident(target="https://shop.example.com/health",
                   summary="uptime monitor probe returned 200 OK", severity=Severity.LOW,
                   source=Source.HTTP)
    inc.add("http", "200 OK from synthetic monitor", status=200)
    return inc


def real_incident() -> Incident:
    return IncidentFactory.http_5xx("https://api.example.com/checkout", 503)


# --------------------------------------------------------------------- stage 0
def test_rules_drop_monitor_noise_without_any_model_call() -> bool:
    c = FakeClient()
    g = Tier0Gate(c).classify(monitor_noise())
    assert g.route == "drop" and g.stage == "0" and g.total_tokens == 0, g
    assert c.calls == [], "stage 0 must never call a model"
    print(f"  code={g.code} model_calls={len(c.calls)} tokens={g.total_tokens}")
    return True


def test_rules_clear_the_static_and_scanner_tail() -> bool:
    cases = [("favicon.ico request returned 404", Severity.LOW),
             ("cdn cache miss revalidation for /assets/app.js", Severity.LOW),
             ("scanner noticed probing /.git/config", Severity.LOW)]
    for summary, sev in cases:
        inc = Incident(target="https://x.example.com", summary=summary, severity=sev,
                       source=Source.HTTP)
        hit = rule_scan(inc)
        assert hit is not None, f"no rule cleared: {summary}"
        print(f"  {hit[0]:22} <- {summary[:44]}")
    return True


def test_high_severity_is_never_rule_dropped() -> bool:
    inc = Incident(target="https://x.example.com",
                   summary="uptime monitor probe returned 200 OK", severity=Severity.HIGH,
                   source=Source.HTTP)
    assert rule_scan(inc) is None, "rules must never clear a HIGH/CRITICAL incident"
    inc2 = Incident(target="https://x.example.com",
                    summary="favicon.ico 404", severity=Severity.CRITICAL,
                    source=Source.HTTP)
    assert rule_scan(inc2) is None
    print("  HIGH/CRITICAL immune to stage-0 drops: ok")
    return True


# --------------------------------------------------------------------- stage 1
def test_benign_verdict_above_threshold_drops_on_the_tier0_model() -> bool:
    c = FakeClient(replies=[benign(0.93)])
    g = Tier0Gate(c).classify(real_incident())
    assert (g.route, g.stage) == ("drop", "1"), g
    assert g.model == "muse-spark-1.2", "glimmer unserved -> cheapest served fallback"
    assert len(c.calls) == 1 and c.calls[0]["model"] == "muse-spark-1.2"
    assert c.calls[0]["max_tokens"] < 2000, "tier-0 budget must stay small"
    print(f"  dropped by {g.model} conf={g.confidence} tok={g.total_tokens} {g.latency_s}s")
    return True


def test_low_confidence_benign_escalates() -> bool:
    c = FakeClient(replies=[benign(0.41)])
    g = Tier0Gate(c, confidence_min=0.7).classify(real_incident())
    assert g.route == "escalate", g
    print(f"  escalate reason: {g.reason}")
    return True


def test_signal_escalates() -> bool:
    c = FakeClient(replies=[signal()])
    g = Tier0Gate(c).classify(real_incident())
    assert g.route == "escalate" and not g.benign
    print(f"  {g.reason}")
    return True


def test_glimmer_is_preferred_when_the_api_serves_it() -> bool:
    c = FakeClient(served=("muse-glimmer", "muse-spark-1.3"), replies=[signal()])
    g = Tier0Gate(c).classify(real_incident())
    assert g.model == "muse-glimmer" and c.calls[0]["model"] == "muse-glimmer"
    print("  chain honours SENTINEL_TIER0_MODEL once served")
    return True


def test_fail_open_never_drops_on_tier0_failure() -> bool:
    for label, item in [("http error", RuntimeError("503 upstream")),
                        ("reasoning-only reply", reply("", reasoning=1500)),
                        ("unparseable body", reply("server exploded, sorry"))]:
        c = FakeClient(replies=[item])
        gate = Tier0Gate(c)
        g = gate.classify(real_incident())
        assert g.route == "escalate" and g.error, f"{label}: {g}"
        assert gate.stats["drops_model"] == 0
        print(f"  {label:20} -> escalate ({g.error[:52]})")
    return True


# ------------------------------------------------------------- pipeline wiring
def test_pipeline_drops_noise_before_the_expensive_model() -> bool:
    c = FakeClient(replies=[])
    p = SentinelPipeline(client=c, gate_enabled=True)
    v = p.run(monitor_noise())
    assert v.ok and v.triage["signature"] == "TIER0_DROPPED", v.triage
    assert c.calls == [], "no model call at all for stage-0 noise"
    assert v.stages and v.stages[0]["stage"] == "gate-0" and v.stages[0]["tokens"] == 0
    print(f"  dropped_by={v.triage['dropped_by']} stages={[s['stage'] for s in v.stages]}")
    return True


def test_pipeline_escalates_a_real_incident_to_tier1() -> bool:
    triage_json = ('{"signature":"HTTP_UPSTREAM_5XX","confidence":0.9,'
                   '"root_cause":"upstream pool exhausted","blast_radius":"service",'
                   '"auto_fixable":false,"plan":["raise pool","add health check"],'
                   '"verification":"503 rate back to zero"}')
    c = FakeClient(replies=[signal(), reply(triage_json, reasoning=1400, completion=180)])
    p = SentinelPipeline(client=c, gate_enabled=True)
    v = p.run(real_incident())
    assert v.ok and v.triage["signature"] == "HTTP_UPSTREAM_5XX", v.triage
    assert [x["model"] for x in c.calls] == ["muse-spark-1.2", "muse-spark-1.3"], c.calls
    assert [s["stage"] for s in v.stages] == ["gate-1", "triage"], v.stages
    assert v.triage["gate"]["route"] == "escalate"
    print(f"  chain={[s['stage'] for s in v.stages]} sig={v.triage['signature']}")
    return True


def test_gate_can_be_disabled_for_baseline_benchmarks() -> bool:
    c = FakeClient(replies=[reply('{"signature":"X","plan":[]}')])
    p = SentinelPipeline(client=c, gate_enabled=False)
    v = p.run(real_incident())
    assert c.calls[0]["model"] == "muse-spark-1.3", "baseline must hit tier 1 first"
    assert v.triage.get("gate") is None, "disabled gate must leave no gate payload"
    assert [s["stage"] for s in v.stages] == ["triage"], v.stages
    assert len(c.calls) == 1
    print("  gate_enabled=False -> straight to muse-spark-1.3 (A/B baseline)")
    return True


# ------------------------------------------------------------------------- run
def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"SentinelPulse stage-0/1 gate suite — {len(tests)} checks\n")
    results = []
    for t in tests:
        print(f"{t.__name__}")
        try:
            results.append(bool(t()))
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {type(exc).__name__}: {exc}")
            results.append(False)
        print()
    passed, total = sum(results), len(results)
    print(f"{'=' * 60}\n{passed}/{total} checks passed\n{'=' * 60}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())