#!/usr/bin/env python3
"""
SentinelPulse :: orchestration core  (app/core/pipeline.py)

The reasoning loop. Deterministic scaffolding around muse-spark-1.3: the model
classifies the signature and proposes a plan; Python owns the control flow,
the guardrails and the record-keeping.

Pipeline stages
  1. COLLECT   probes measure the target (DNS / HTTP / headers / screenshot)
  2. TRIAGE    incident.brief() -> muse-spark-1.3 -> signature + severity + plan
  3. LOCATE    optional: visual inspector on the captured screenshot
  4. REMEDIATE optional: patch generator + AST/sandbox validation
  5. REPORT    a single verdict object the operator (and CI) can act on
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    from app.core.incident import Incident, Severity
    from app.core.meta_client import MetaClient, MetaReply
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.incident import Incident, Severity
    from app.core.meta_client import MetaClient, MetaReply

TRIAGE_SYSTEM = """You are SentinelPulse's incident triage core.

Given a normalised incident record, you produce a deterministic remediation plan.

Rules:
- Reply with ONE JSON object and nothing else.
- `signature` is a stable machine key: UPPER_SNAKE_CASE, ending in a short
  qualifier, e.g. DNS_NXDOMAIN, HTTP_UPSTREAM_5XX, HEADER_CSP_MISSING,
  VISUAL_LAYOUT_BREAK.
- `root_cause` is the most probable cause given ONLY the supplied evidence.
  If evidence is insufficient, say what is missing instead of guessing.
- `plan` is an ordered list of 2-6 concrete steps. Each step names the exact
  artifact to change (file, DNS record, nginx directive, deploy setting).
- `blast_radius` is one of: local | service | platform.
- `auto_fixable` is true only when the fix is a code/config diff a machine can
  author and validate without human judgement.

Shape:
{
  "signature": "DNS_NXDOMAIN",
  "confidence": 0.0,
  "root_cause": "...",
  "blast_radius": "local|service|platform",
  "auto_fixable": false,
  "plan": ["step 1", "step 2"],
  "verification": "how to prove the fix worked"
}"""


def _extract_json(text: str) -> dict:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError(f"no JSON in model output: {text[:200]!r}")


@dataclass
class Triage:
    signature: str = "UNCLASSIFIED"
    confidence: float = 0.0
    root_cause: str = ""
    blast_radius: str = "service"
    auto_fixable: bool = False
    plan: list[str] = field(default_factory=list)
    verification: str = ""
    reasoning_tokens: int = 0
    latency_s: float = 0.0
    error: str = ""


@dataclass
class Verdict:
    incident: dict
    triage: dict
    visual: dict | None = None
    remediation: dict | None = None
    ok: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


class SentinelPipeline:
    """Collect -> triage -> (inspect) -> (remediate) -> report."""

    def __init__(self, client: MetaClient | None = None,
                 triage_model: str = "muse-spark-1.3",
                 reasoning_effort: str = "low"):
        self.client = client or MetaClient()
        self.triage_model = triage_model
        self.reasoning_effort = reasoning_effort

    # ------------------------------------------------------------------ triage
    def triage(self, inc: Incident) -> Triage:
        prompt = f"{TRIAGE_SYSTEM}\n\nINCIDENT RECORD\n{inc.brief()}"
        t0 = time.time()
        try:
            reply: MetaReply = self.client.chat(
                prompt, model=self.triage_model,
                reasoning_effort=self.reasoning_effort, max_tokens=4000,
            )
        except Exception as exc:  # noqa: BLE001
            return Triage(error=f"{type(exc).__name__}: {exc}")

        tr = Triage(reasoning_tokens=reply.reasoning_tokens,
                    latency_s=round(time.time() - t0, 2))
        if reply.truncated_by_reasoning:
            tr.error = (f"reasoning-only response: {reply.reasoning_tokens} reasoning "
                        f"tokens consumed the {reply.completion_tokens} budget")
            return tr
        try:
            data = _extract_json(reply.text)
        except Exception as exc:  # noqa: BLE001
            tr.error = f"unparsed triage output: {exc}"
            return tr

        tr.signature = str(data.get("signature", "UNCLASSIFIED"))
        tr.confidence = float(data.get("confidence", 0.0) or 0.0)
        tr.root_cause = str(data.get("root_cause", ""))
        tr.blast_radius = str(data.get("blast_radius", "service"))
        tr.auto_fixable = bool(data.get("auto_fixable", False))
        tr.plan = [str(s) for s in (data.get("plan") or [])]
        tr.verification = str(data.get("verification", ""))

        # persist onto the incident so downstream stages inherit the verdict
        inc.signature = tr.signature
        inc.root_cause = tr.root_cause
        inc.plan = list(tr.plan)
        return tr

    # ----------------------------------------------------------------- collect
    def collect(self, target: str, *, kind: str = "auto") -> list[Incident]:
        """Run the deterministic probes and return every incident found."""
        from app.probes import health

        found: list[Incident] = []
        if kind in ("auto", "dns") and "://" not in target:
            inc = health.probe_dns(target)
            if inc:
                found.append(inc)
        if kind in ("auto", "http", "dns") and (target.startswith("http") or kind == "http"):
            url = target if target.startswith("http") else f"https://{target}"
            inc, _ = health.probe_http(url)
            if inc:
                found.append(inc)
            hinc, _ = health.probe_headers(url)
            if hinc:
                found.append(hinc)
        return found

    # ------------------------------------------------------------------ report
    def run(self, inc: Incident, *, inspect: bool = False, screenshot: str | None = None,
            remediate: bool = False, repo: str | None = None,
            candidate_files: list[str] | None = None) -> Verdict:
        tr = self.triage(inc)
        v = Verdict(incident=inc.to_dict(), triage=asdict(tr), ok=not tr.error)

        if inspect and screenshot:
            try:
                from app.vision.inspector import VisualInspector
                rep = VisualInspector(self.client).inspect(screenshot, context=inc.target)
                v.visual = {
                    "summary": rep.summary, "defaced": rep.defaced,
                    "layout_broken": rep.layout_broken,
                    "overlay_suspected": rep.overlay_suspected,
                    "findings": [f.__dict__ for f in rep.findings],
                }
            except Exception as exc:  # noqa: BLE001
                v.visual = {"error": f"{type(exc).__name__}: {exc}"}

        if remediate and repo:
            try:
                from app.remediator.patchgen import PatchGenerator
                gen = PatchGenerator(self.client, repo)
                result = gen.propose(inc, tr, candidate_files=candidate_files)
                v.remediation = result.to_dict()
            except Exception as exc:  # noqa: BLE001
                v.remediation = {"error": f"{type(exc).__name__}: {exc}"}

        return v


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "api.client.example.com"
    pipe = SentinelPipeline()
    incidents = pipe.collect(target)
    if not incidents:
        incidents = [IncidentFactory_stub(target)] if False else []
    for inc in incidents:
        print(pipe.run(inc).to_json())