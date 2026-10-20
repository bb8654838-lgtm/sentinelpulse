#!/usr/bin/env python3
"""
SentinelPulse :: Stage 0/1 gate  (app/core/gatekeeper.py)

Tiered triage front-door, per SAM's 2026-10-20 decision:

  Stage 0  DETERMINISTIC PRE-FILTER   zero-cost rules, no model call at all
  Stage 1  RAPID TRIAGE               the tier-0 model (muse-glimmer by design)
  Stage 2+ DEEP TRIAGE                muse-spark-1.3 (unchanged, in pipeline.py)

Design contract
- The gate answers ONE question: is this incident worth the expensive model?
  It never authors a remediation plan and never mutates the incident.
- Fail-OPEN, always: a tier-0 error, timeout, truncated-by-reasoning response or
  unparseable JSON escalates to tier 1. Nothing is ever dropped because a model
  misbehaved — a silent false-drop of a real incident is the one unacceptable bug.
- Only an explicit benign verdict at/above `confidence_min` drops an incident.
- The tier-0 model id is env-switchable (`SENTINEL_TIER0_MODEL`), because
  muse-glimmer is published as open weights and may not be served by the API
  yet: the chain walks glimmer -> cheapest served fallback and reports which id
  it actually used, so the benchmark is honest about the substitution.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

try:
    from app.core.incident import Incident, Severity
    from app.core.meta_client import MetaClient, MetaReply
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.incident import Incident, Severity
    from app.core.meta_client import MetaClient, MetaReply

# Tier-0 is a cheap classifier, NOT a planner: tiny prompt, tiny budget.
TIER0_CHAIN = (
    os.environ.get("SENTINEL_TIER0_MODEL", "muse-glimmer"),
    "muse-spark-1.2",
    "muse-spark-1.1",
)
# meta_client enforces MIN_SAFE_MAX_TOKENS = 2500 (a reasoning model shares the
# budget with its reasoning tokens: 1500 -> all reasoning, zero text). Tier 0
# therefore sits at the floor with reasoning_effort="low", tier 1 at 4000.
TIER0_MAX_TOKENS = int(os.environ.get("SENTINEL_TIER0_MAX_TOKENS", "2500"))
CONFIDENCE_MIN = float(os.environ.get("SENTINEL_TIER0_CONFIDENCE_MIN", "0.7"))

TIER0_SYSTEM = """You are SentinelPulse's rapid triage gate (stage 1).

Decide only whether the incident record below is TRUE SIGNAL (a real service
failure worth an expensive deep-triage pass) or BENIGN NOISE (routine monitor /
scanner / static-asset noise that a human would close without investigation).

Rules:
- Reply with ONE JSON object and nothing else.
- `benign` true ONLY for provably routine noise: healthy monitor hits, static
  404s, CDN cache churn, uptime-probe resets, scanner probing.
- Any doubt, any user-visible symptom, any unknown signature -> `benign` false.
- `confidence` is your own probability (0.0-1.0) that the `benign` call is right.

Shape: {"benign": true, "confidence": 0.0, "reason": "short machine reason"}"""


# --------------------------------------------------------------------- stage 0
@dataclass
class Rule:
    code: str
    pattern: re.Pattern
    note: str


# Conservative: an incident whose severity is HIGH/CRITICAL is never dropped by a
# rule. Rules only clear the routine low-severity monitor/static tail.
RULES: tuple[Rule, ...] = (
    Rule("BENIGN_MONITOR_HIT",
         re.compile(r"\b(uptime|synthetic|monitor(?:ing)?[ _-]?probe|health[ _-]?check)\b", re.I),
         "monitoring/uptime probe traffic"),
    Rule("BENIGN_STATIC_404",
         re.compile(r"\b(favicon\.ico|robots\.txt|apple-touch-icon|\.well-known/)\b.*\b404\b"
                    r"|\b404\b.*\b(favicon\.ico|robots\.txt|apple-touch-icon)\b", re.I),
         "404 on a static asset a browser requests unconditionally"),
    Rule("BENIGN_CDN_CHURN",
         re.compile(r"\b(cache[ _-]?miss|stale|expired|revalidat)\w*\b", re.I),
         "CDN cache churn with no user-visible failure"),
    Rule("BENIGN_SCANNER_NOISE",
         re.compile(r"\b(scanner|nikto|nuclei|masscan|dirbuster|gobuster|scrapy|crawler)\b", re.I),
         "third-party scanner probing, no exploit evidence"),
)
STATUS_OK = re.compile(r"\b(200|201|204|301|302)\b")


def rule_scan(inc: Incident) -> tuple[str, str] | None:
    """Stage 0. Returns (code, reason) when a rule clears the incident, else None."""
    if inc.severity in (Severity.HIGH, Severity.CRITICAL):
        return None
    brief = inc.brief()
    for r in RULES:
        if not r.pattern.search(brief):
            continue
        if r.code == "BENIGN_MONITOR_HIT" or r.code == "BENIGN_SCANNER_NOISE":
            return r.code, r.note
        if STATUS_OK.search(brief) or inc.severity == Severity.LOW:
            return r.code, r.note
    return None


# --------------------------------------------------------------------- stage 1
@dataclass
class Gate:
    route: str = "escalate"            # "drop" | "escalate"
    stage: str = "0"                   # "0" (rules) | "1" (tier-0 model)
    reason: str = ""
    code: str = ""                     # machine reason code (rules) / model reason
    model: str = ""                    # tier-0 model id actually used ("" for rules)
    benign: bool = False
    confidence: float = 0.0
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _extract_json(text: str) -> dict:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError(f"no JSON in tier-0 output: {text[:120]!r}")


class Tier0Gate:
    """Stage 0 rules + stage 1 rapid model. Fail-open by construction."""

    def __init__(self, client: MetaClient | None = None,
                 chain: tuple[str, ...] = TIER0_CHAIN,
                 confidence_min: float = CONFIDENCE_MIN,
                 max_tokens: int = TIER0_MAX_TOKENS,
                 served: list[str] | None = None):
        self.client = client or MetaClient()
        self.chain = chain
        self.confidence_min = confidence_min
        self.max_tokens = max_tokens
        self.served = served
        self.model = ""
        self.stats: dict[str, Any] = {
            "calls": 0, "drops_rules": 0, "drops_model": 0, "escalated": 0,
            "errors": 0, "prompt_tokens": 0, "completion_tokens": 0, "latency_s": 0.0,
        }
        self.trace: list[dict] = []

    # ------------------------------------------------------------------ resolve
    def resolve(self) -> str:
        """First chain entry the API actually serves (cached); chain[0] if unknown."""
        if self.model:
            return self.model
        if self.served is None:
            try:
                self.served = list(self.client.models())
            except Exception:  # noqa: BLE001  — unknown model list: try chain[0]
                self.served = []
        self.model = next((m for m in self.chain if m in self.served), self.chain[0])
        return self.model

    # ------------------------------------------------------------------ classify
    def classify(self, inc: Incident) -> Gate:
        hit = rule_scan(inc)
        if hit:
            g = Gate(route="drop", stage="0", code=hit[0], reason=hit[1],
                     benign=True, confidence=1.0)
            self.stats["drops_rules"] += 1
            self.trace.append({"incident": inc.incident_id, **asdict(g)})
            return g

        model = self.resolve()
        prompt = (f"{TIER0_SYSTEM}\n\nINCIDENT RECORD\n{inc.brief()}")
        t0 = time.time()
        self.stats["calls"] += 1
        try:
            reply: MetaReply = self.client.chat(
                prompt, model=model, reasoning_effort="low", max_tokens=self.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail_open(inc, model, t0, f"{type(exc).__name__}: {exc}")

        g = Gate(stage="1", model=model, latency_s=round(time.time() - t0, 2),
                 prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens)
        self.stats["prompt_tokens"] += reply.prompt_tokens
        self.stats["completion_tokens"] += reply.completion_tokens
        self.stats["latency_s"] = round(self.stats["latency_s"] + g.latency_s, 2)

        if reply.truncated_by_reasoning:
            return self._fail_open(inc, model, t0,
                                   f"reasoning-only reply ({reply.reasoning_tokens} reasoning tokens)")
        try:
            data = _extract_json(reply.text)
        except Exception as exc:  # noqa: BLE001
            return self._fail_open(inc, model, t0, f"unparsed tier-0 output: {exc}")

        g.benign = bool(data.get("benign", False))
        g.confidence = float(data.get("confidence", 0.0) or 0.0)
        g.code = str(data.get("reason", ""))[:160]
        if g.benign and g.confidence >= self.confidence_min:
            g.route, g.reason = "drop", g.code or "tier-0 benign verdict"
            self.stats["drops_model"] += 1
        else:
            g.route = "escalate"
            g.reason = ("tier-0 says signal" if not g.benign
                        else f"benign but confidence {g.confidence:.2f} < {self.confidence_min}")
            self.stats["escalated"] += 1
        self.trace.append({"incident": inc.incident_id, **asdict(g)})
        return g

    def _fail_open(self, inc: Incident, model: str, t0: float, why: str) -> Gate:
        g = Gate(route="escalate", stage="1", model=model, error=why,
                 latency_s=round(time.time() - t0, 2),
                 reason=f"tier-0 unavailable -> escalate ({why})")
        self.stats["errors"] += 1
        self.stats["escalated"] += 1
        self.trace.append({"incident": inc.incident_id, **asdict(g)})
        return g


if __name__ == "__main__":  # tiny live smoke: python -m app.core.gatekeeper
    from app.core.incident import IncidentFactory
    gate = Tier0Gate()
    print("tier-0 model resolved:", gate.resolve(), "| served:", gate.served)
    for inc in (IncidentFactory.nxdomain("api.client.example.com"),
                IncidentFactory.http_5xx("https://api.client.example.com/health", 503)):
        g = gate.classify(inc)
        print(f"{inc.incident_id} {inc.summary[:42]:42} -> {g.route:8} stage={g.stage} "
              f"model={g.model or 'rules'} {g.latency_s}s tok={g.total_tokens} {g.reason[:60]}")
    print(json.dumps(gate.stats, indent=2))