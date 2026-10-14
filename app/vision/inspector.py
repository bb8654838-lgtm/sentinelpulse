#!/usr/bin/env python3
"""
SentinelPulse :: visual inspector  (app/vision/inspector.py)

Split of responsibility, per the live docs (dev.meta.ai/docs/models):

  muse-spark-1.3  -> the VLM. Interprets a rendered page: layout breakage,
                     broken asset containers, defacement text, clickjacking
                     overlays. Returns structured JSON we can act on.
  sam-3.1         -> geometry only. Given a noun phrase it returns a box +
                     pixel mask over the Responses API (streaming). Used to
                     localise a component that Spark has already flagged.

This module owns the Spark path, which is what the demos exercise. The SAM
locator is exposed as `locate()` and intentionally talks to /responses with
stream=True because that is the only endpoint that serves segmentation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from app.core.meta_client import MetaClient, MetaReply, to_data_url
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.meta_client import MetaClient, MetaReply, to_data_url

BASE_URL = "https://api.meta.ai/v1"

INSPECTOR_SYSTEM = """You are SentinelPulse's visual inspector for rendered web pages.
You receive a screenshot of a live page and report visual failures.

Report ONLY what is visible. Do not speculate about backend causes.
Answer with a single JSON object, no prose, in exactly this shape:

{
  "defaced": false,
  "layout_broken": false,
  "overlay_suspected": false,
  "findings": [
    {"kind": "broken_asset|layout_break|defacement|clickjacking_overlay|other",
     "where": "top-left|header|hero|footer|...",
     "detail": "what is visibly wrong",
     "confidence": 0.0}
  ],
  "summary": "one sentence a human operator can act on"
}"""


@dataclass
class VisualFinding:
    kind: str
    where: str
    detail: str
    confidence: float = 0.0


@dataclass
class VisualReport:
    summary: str
    defaced: bool = False
    layout_broken: bool = False
    overlay_suspected: bool = False
    findings: list[VisualFinding] = None
    raw_text: str = ""
    reasoning_tokens: int = 0

    def __post_init__(self):
        if self.findings is None:
            self.findings = []

    @property
    def needs_remediation(self) -> bool:
        return self.defaced or self.layout_broken or self.overlay_suspected or bool(self.findings)


def _extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in prose or fences. Dig it out."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError(f"no JSON object in model output: {text[:200]!r}")


class VisualInspector:
    """Screenshot -> structured visual defect report, via muse-spark-1.3."""

    def __init__(self, client: MetaClient | None = None,
                 model: str = "muse-spark-1.3"):
        self.client = client or MetaClient()
        self.model = model

    def inspect(self, screenshot: str | Path, context: str = "",
                reasoning_effort: str = "low") -> VisualReport:
        prompt = "Inspect this rendered page and report visual failures."
        if context:
            prompt += f"\nPage context: {context}"

        # Compose a single user turn: system text + the image.
        image_url = to_data_url(screenshot) if not str(screenshot).startswith(("http", "data:")) \
            else str(screenshot)
        reply: MetaReply = self.client.vision(
            image_url, f"{INSPECTOR_SYSTEM}\n\n{prompt}",
            model=self.model, reasoning_effort=reasoning_effort, max_tokens=4000,
        )
        return self._to_report(reply)

    def _to_report(self, reply: MetaReply) -> VisualReport:
        rep = VisualReport(summary="", raw_text=reply.text,
                           reasoning_tokens=reply.reasoning_tokens)
        if reply.truncated_by_reasoning:
            rep.summary = (
                f"[no visible output] model spent all {reply.completion_tokens} tokens "
                f"reasoning ({reply.reasoning_tokens} reasoning tokens). Raise max_tokens."
            )
            return rep
        try:
            data = _extract_json(reply.text)
        except Exception as exc:  # noqa: BLE001
            rep.summary = f"[unparsed model output] {exc}"
            return rep
        rep.defaced = bool(data.get("defaced"))
        rep.layout_broken = bool(data.get("layout_broken"))
        rep.overlay_suspected = bool(data.get("overlay_suspected"))
        rep.summary = data.get("summary", "")
        for f in data.get("findings", []) or []:
            rep.findings.append(VisualFinding(
                kind=f.get("kind", "other"),
                where=f.get("where", "?"),
                detail=f.get("detail", ""),
                confidence=float(f.get("confidence", 0.0) or 0.0),
            ))
        return rep

    # ------------------------------------------------------------- SAM geometry
    def locate(self, screenshot: str | Path, concept: str, timeout: int = 120) -> dict:
        """Ask SAM 3.1 for the pixel region of `concept` (noun phrase).

        Responses API + stream is the documented segmentation path; we collect
        the stream and return the parsed geometry. Kept separate from score
        because callers usually run Spark first, then SAM on the flagged noun.
        """
        import urllib.request

        payload = {
            "model": "sam-3.1",
            "input": [{
                "type": "message", "role": "user",
                "content": [
                    {"type": "input_text", "text": concept},
                    {"type": "input_image",
                     "image_url": to_data_url(screenshot)
                     if not str(screenshot).startswith(("http", "data:")) else str(screenshot)},
                ],
            }],
            "stream": True,
        }
        req = urllib.request.Request(
            f"{BASE_URL}/responses", data=json.dumps(payload).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {self.client.api_key}")
        req.add_header("Content-Type", "application/json")
        events: list[dict] = []
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if chunk and chunk != "[DONE]":
                        try:
                            events.append(json.loads(chunk))
                        except json.JSONDecodeError:
                            pass
        return {"concept": concept, "events": len(events), "raw": events[-3:]}


if __name__ == "__main__":
    import sys

    shot = sys.argv[1]
    insp = VisualInspector()
    r = insp.inspect(shot, context="customer-facing dashboard")
    print(json.dumps({
        "summary": r.summary, "defaced": r.defaced, "layout_broken": r.layout_broken,
        "overlay_suspected": r.overlay_suspected, "reasoning_tokens": r.reasoning_tokens,
        "findings": [f.__dict__ for f in r.findings],
    }, indent=2))