#!/usr/bin/env python3
"""
SentinelPulse :: test suite  (tests/test_pipeline.py)

Offline tests — no API key, no network. These guard the deterministic layer:
probe classification, incident normalisation, responder parsing, the patch
engine's unique-match contract, and the reasoning-budget guard.

Run:  .venv/bin/python -m pytest tests/ -v        (or: python tests/test_pipeline.py)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.incident import Incident, IncidentFactory, Severity, Source  # noqa: E402
from app.core.meta_client import MetaClient, MetaReply  # noqa: E402
from app.probes import health  # noqa: E402
from app.remediator.patchgen import PatchGenerator, _extract_json as patch_json  # noqa: E402
from app.vision.inspector import VisualInspector, _extract_json as vis_json  # noqa: E402


# --------------------------------------------------------------------- helpers
def check(name: str, cond: bool) -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return cond


def test_dns_nxdomain_is_classified():
    """RFC 2606 reserves .invalid — NXDOMAIN is guaranteed by DNS itself."""
    inc = health.probe_dns("api.client.invalid")
    return check("probe_dns flags guaranteed NXDOMAIN", inc is not None
                 and inc.source is Source.DNS and "NXDOMAIN" in inc.summary)


def test_dns_healthy_host_returns_none():
    inc = health.probe_dns("localhost")
    return check("probe_dns returns None for a resolvable host", inc is None)


def test_incident_brief_contains_evidence():
    inc = IncidentFactory.http_5xx("https://example.com/api", 503, "<html>oops</html>")
    brief = inc.brief()
    ok = all(k in brief for k in ("incident_id", "503", "evidence", "http"))
    return check("incident brief carries id/target/evidence", ok)


def test_incident_serialises():
    inc = IncidentFactory.nxdomain("api.client.invalid")
    d = inc.to_dict()
    ok = d["source"] == "dns" and d["severity"] == "high" and isinstance(d["evidence"], list)
    return check("incident.to_dict is JSON-safe with enum values flattened", ok)


def test_severity_escalates_for_5xx():
    inc = IncidentFactory.http_5xx("https://x.test", 503)
    return check("503 escalates to critical severity", inc.severity is Severity.CRITICAL)


def test_responder_parses_fenced_json():
    payload = {"signature": "DNS_NXDOMAIN", "plan": ["a", "b"]}
    text = f"here you go:\n```json\n{json.dumps(payload)}\n```\ndone"
    got = patch_json(text)
    return check("json extractor handles fenced output", got == payload)


def test_responder_parses_bare_json():
    text = 'prose {"signature": "X", "confidence": 0.5} trailing'
    got = vis_json(text)
    return check("json extractor handles bare object in prose",
                 got["signature"] == "X" and got["confidence"] == 0.5)


def test_reasoning_budget_guard():
    """A reasoning model given too little budget returns empty content.
    The client must refuse the request instead of burning a call."""
    c = MetaClient(api_key="dummy")
    try:
        c.chat("hello", max_tokens=800)
    except ValueError as exc:
        return check("client refuses max_tokens below the reasoning floor",
                     "reasoning" in str(exc))
    return check("client refuses max_tokens below the reasoning floor", False)


def test_reasoning_effort_validated():
    c = MetaClient(api_key="dummy")
    try:
        c.chat("hello", reasoning_effort="turbo", max_tokens=4000)
    except ValueError as exc:
        return check("invalid reasoning_effort rejected locally", "reasoning_effort" in str(exc))
    return check("invalid reasoning_effort rejected locally", False)


def test_truncated_by_reasoning_flag():
    body = {"model": "muse-spark-1.3", "choices": [{"message": {"content": None},
            "finish_reason": "length"}],
            "usage": {"completion_tokens": 1200, "prompt_tokens": 30,
                      "completion_tokens_details": {"reasoning_tokens": 1197}}}
    r = MetaClient._parse(body)
    return check("empty content + reasoning tokens is flagged as truncated",
                 r.truncated_by_reasoning and r.reasoning_tokens == 1197)


# ------------------------------------------------------- patch engine contract
def _patch_fixture(tmp: Path) -> Path:
    f = tmp / "mod.py"
    f.write_text("def f():\n    try:\n        return True\n    except Exception:\n        return True\n")
    return f


def test_edit_applies_and_emits_valid_diff():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        (repo).mkdir()
        _patch_fixture(repo)
        gen = PatchGenerator(MetaClient(api_key="dummy"), repo)
        ok, log, touched, diff, sbx = gen._sandbox_apply([{
            "file": "mod.py",
            "find": "    except Exception:\n        return True",
            "replace": "    except (TimeoutError, OSError):\n        return False",
        }])
        return check("edit applies, validates, and yields a diff",
                     ok and touched == ["mod.py"] and diff.startswith("--- a/mod.py")
                     and "return False" in diff)


def test_nonunique_find_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text("x = 1\nx = 1\n")
        gen = PatchGenerator(MetaClient(api_key="dummy"), repo)
        ok, log, _, _, _ = gen._sandbox_apply([{"file": "mod.py", "find": "x = 1",
                                                "replace": "x = 2"}])
        return check("ambiguous `find` (2 matches) is refused", not ok
                     and "unique" in log)


def test_absent_find_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text("x = 1\n")
        gen = PatchGenerator(MetaClient(api_key="dummy"), repo)
        ok, log, _, _, _ = gen._sandbox_apply([{"file": "mod.py", "find": "nope",
                                                "replace": "y"}])
        return check("absent `find` is refused", not ok and "NOT present" in log)


def test_syntax_error_is_caught():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text("x = 1\n")
        gen = PatchGenerator(MetaClient(api_key="dummy"), repo)
        ok, log, _, _, _ = gen._sandbox_apply([{"file": "mod.py", "find": "x = 1",
                                                "replace": "def broken(:"}])
        return check("patch introducing a SyntaxError fails validation", not ok
                     and "SyntaxError" in log)


def test_live_repo_never_modified():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        repo.mkdir()
        f = _patch_fixture(repo)
        before = f.read_text()
        gen = PatchGenerator(MetaClient(api_key="dummy"), repo)
        gen._sandbox_apply([{"file": "mod.py",
                             "find": "    except Exception:\n        return True",
                             "replace": "    except OSError:\n        return False"}])
        return check("sandbox edits never touch the source repo", f.read_text() == before)


def test_candidate_extraction_from_plan():
    plan = ["Edit app/probes/health.py check_timeout()",
            "Add tests in tests/test_health.py",
            "Reload nginx.conf"]
    got = PatchGenerator._candidates_from_plan(plan)
    return check("file paths extracted from free-text plan",
                 got == ["app/probes/health.py", "tests/test_health.py", "nginx.conf"])


def test_visual_report_needs_remediation():
    rep = VisualInspector(MetaClient(api_key="dummy"))._to_report(
        MetaReply(text=json.dumps({"defaced": True, "layout_broken": False,
                                   "overlay_suspected": False, "findings": [],
                                   "summary": "banner injected"}), model="muse-spark-1.3"))
    return check("visual report flags remediation need", rep.needs_remediation
                 and rep.defaced)


def test_visual_report_parses_findings():
    body = {"defaced": False, "layout_broken": True, "overlay_suspected": False,
            "findings": [{"kind": "layout_break", "where": "hero", "detail": "overlap",
                          "confidence": 0.91}], "summary": "grid collapsed"}
    rep = VisualInspector(MetaClient(api_key="dummy"))._to_report(
        MetaReply(text=json.dumps(body), model="muse-spark-1.3"))
    f = rep.findings[0]
    return check("visual findings parsed with confidence",
                 len(rep.findings) == 1 and f.kind == "layout_break" and f.confidence == 0.91)


def test_visual_truncation_reported_not_crashed():
    rep = VisualInspector(MetaClient(api_key="dummy"))._to_report(
        MetaReply(text="", model="muse-spark-1.3", completion_tokens=1200,
                  reasoning_tokens=1197))
    return check("reasoning-only visual response degrades gracefully",
                 "max_tokens" in rep.summary.lower() and not rep.needs_remediation)


# ------------------------------------------------------------------------- run
def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"SentinelPulse test suite — {len(tests)} checks\n")
    results = []
    for t in tests:
        print(f"{t.__name__}")
        try:
            results.append(bool(t()))
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] raised {type(exc).__name__}: {exc}")
            results.append(False)
        print()
    passed, total = sum(results), len(results)
    print(f"{'=' * 60}\n{passed}/{total} checks passed\n{'=' * 60}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())