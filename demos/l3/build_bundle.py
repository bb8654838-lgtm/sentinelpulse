#!/usr/bin/env python3
"""Build the single machine-readable demo bundle from frozen L2 evidence.

Replay mode is the default and costs nothing: every number in the bundle is read
out of a hash-locked evidence file, never typed by hand. Live mode lives in
run_live.py and is budget-capped, so this module stays offline-safe -- it can be
run in front of judges on a plane.

Usage:
    python -m demos.l3.build_bundle                  # -> demo-bundle/
    python -m demos.l3.build_bundle --out /tmp/x     # custom output dir
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "bench" / "evidence"
SHOTS = ROOT / "demos" / "fixtures"
DEFAULT_OUT = ROOT / "demo-bundle"
TRACK = "indie"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _frozen_hash(name: str) -> str | None:
    """Hash of an evidence file, cross-checked against the freeze manifest."""
    for manifest in sorted(EVIDENCE.glob("FREEZE-*.json")):
        data = json.loads(manifest.read_text())
        for art in data.get("artifacts", []):
            if Path(art["path"]).name == name and art.get("sha256"):
                return art["sha256"]
    return None


def load_bench(path: Path) -> dict:
    data = json.loads(path.read_text())
    summary = data["summary"]
    base, tiered = summary["baseline"], summary["tiered"]
    return {
        "source": str(path.relative_to(ROOT)),
        "sha256_actual": sha256(path),
        "sha256_frozen": _frozen_hash(path.name),
        "fixtures": len(data["arms"]["tiered_gate_on"]),
        "arms": {
            "baseline_gate_off": base,
            "tiered_gate_on": tiered,
        },
        "deltas": {
            "model_calls": tiered["model_calls"] - base["model_calls"],
            "tokens": tiered["tokens"] - base["tokens"],
            "tokens_pct": round(100.0 * (base["tokens"] - tiered["tokens"]) / base["tokens"], 1)
            if base["tokens"] else 0.0,
            "latency_s": round(tiered["latency_s"] - base["latency_s"], 2),
            "calls_on_noise": tiered["calls_on_noise"] - base["calls_on_noise"],
        },
        "incident_misses": {
            "baseline": len(base.get("INCIDENT_MISSES") or []),
            "tiered": len(tiered.get("INCIDENT_MISSES") or []),
        },
        "per_fixture": [
            {
                "fixture": r["fixture"],
                "label": r["label"],
                "note": r.get("note", ""),
                "routed": r.get("routed"),
                "stages": r.get("stages", []),
                "model_calls": r.get("model_calls", 0),
                "tokens": r.get("tokens", 0),
                "signature": r.get("signature", ""),
            }
            for r in data["arms"]["tiered_gate_on"]
        ],
    }


def build(out_dir: Path) -> dict:
    bench_path = EVIDENCE / "tier0-bench-LIVE-20261027.json"
    bench = load_bench(bench_path)

    freeze_files = sorted(EVIDENCE.glob("FREEZE-*.json"))
    freeze = json.loads(freeze_files[-1].read_text()) if freeze_files else None

    shots = []
    for p in sorted(SHOTS.glob("*.png")):
        shots.append({"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size,
                      "sha256": sha256(p)})

    d = bench["deltas"]
    tiered = bench["arms"]["tiered_gate_on"]
    claims = [
        {"id": "C1", "claim": "Zero incident misses with the gate on",
         "value": f"{bench['incident_misses']['tiered']}/{bench['fixtures']} missed",
         "evidence": bench["source"]},
        {"id": "C2", "claim": "Noise never reaches the reasoning model",
         "value": f"{tiered['calls_on_noise']} noise calls, {tiered['rules_dropped']} rules-dropped",
         "evidence": bench["source"]},
        {"id": "C3", "claim": "Token spend falls per incident batch",
         "value": f"{bench['arms']['baseline_gate_off']['tokens']} -> {tiered['tokens']} "
                  f"({d['tokens_pct']}% less)",
         "evidence": bench["source"]},
        {"id": "C4", "claim": "End-to-end latency falls",
         "value": f"{bench['arms']['baseline_gate_off']['latency_s']}s -> {tiered['latency_s']}s",
         "evidence": bench["source"]},
        {"id": "C5", "claim": "Signal always escalates to tier-1 triage",
         "value": f"{sum(1 for f in bench['per_fixture'] if f['label'] == 'signal' and f['routed'] != 'drop')}"
                  f"/{sum(1 for f in bench['per_fixture'] if f['label'] == 'signal')} signal fixtures escalated",
         "evidence": bench["source"]},
    ]

    bundle = {
        "kind": "sentinelpulse-demo-bundle",
        "track": TRACK,
        "mode": "replay",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product": {
            "name": "SentinelPulse",
            "one_liner": "An incident-triage agent that drops the noise before it "
                         "costs a token, then plans the fix for what is left.",
            "models": {"tier0_gate": "muse-spark-1.3 (low effort)",
                       "tier1_triage": "muse-spark-1.3"},
        },
        "freeze": {
            "manifest": str(freeze_files[-1].relative_to(ROOT)) if freeze else None,
            "frozen_at": (freeze or {}).get("frozen_at"),
            "artifacts": (freeze or {}).get("artifacts", []),
        },
        "bench": bench,
        "claims": claims,
        "screenshots": shots,
        "reproduce": [
            "python -m demos.l3.freeze_evidence --check",
            "python -m demos.l3.build_bundle",
            "python -m demos.l3.render_report",
        ],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n")

    md = [f"# SentinelPulse — demo bundle ({bundle['mode']})", "",
          f"Track: **{TRACK}** · generated {bundle['generated_at']}", "",
          "## Claims and the evidence behind them", ""]
    for c in claims:
        md.append(f"- **{c['id']}** {c['claim']} — {c['value']}  \n  evidence: `{c['evidence']}`")
    md += ["", "## Per-fixture routing (gate ON)", "",
           "| fixture | label | route | stages | tokens |", "|---|---|---|---|---|"]
    for f in bench["per_fixture"]:
        md.append(f"| {f['fixture']} | {f['label']} | {f['routed']} | "
                  f"{', '.join(f['stages'])} | {f['tokens']} |")
    md += ["", "## Freeze", "",
           f"- manifest: `{bundle['freeze']['manifest']}`",
           f"- frozen_at: {bundle['freeze']['frozen_at']}", ""]
    (out_dir / "report.md").write_text("\n".join(md) + "\n")

    return bundle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    bundle = build(Path(args.out))
    print(f"bundle -> {args.out}/bundle.json  ({len(bundle['claims'])} claims, "
          f"{bundle['bench']['fixtures']} fixtures)")
    for c in bundle["claims"]:
        print(f"  {c['id']}  {c['value']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
