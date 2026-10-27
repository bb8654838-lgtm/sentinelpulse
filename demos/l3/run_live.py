#!/usr/bin/env python3
"""Optional live capture for the L3 demo — opt-in, budget-capped, abortable.

This is the only L3 entry point that can spend credits. It refuses to run unless
the operator passes --confirm-live, and it hard-caps itself below the CLI values,
so a typo cannot turn a demo into a bill.

    python -m demos.l3.run_live --confirm-live            # 7 fixtures, <= 8 calls
    python -m demos.l3.run_live --confirm-live --fixture checkout-503

Compare the live JSON against the frozen replay bundle: routing must match, only
token counts drift. A routing difference is a demo-blocking bug, not a number tweak.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "bench")):
    if p not in sys.path:
        sys.path.insert(0, p)

from demos.l3.budget import BudgetExceeded, BudgetGuard  # noqa: E402

HARD_CAP_CALLS = 10
HARD_CAP_TOKENS = 8000
OUT_DIR = ROOT / "demo-bundle"


def load_fixtures():
    """Load bench fixtures by path -- bench/ is not an installed package."""
    import importlib.util
    path = ROOT / "bench" / "tier0_bench.py"
    spec = importlib.util.spec_from_file_location("l3_tier0_bench", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load fixtures from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.FIXTURES)


def run(limit: int | None, calls: int, tokens: int) -> dict:
    from app.core.pipeline import SentinelPipeline

    guard = BudgetGuard(max_calls=min(calls, HARD_CAP_CALLS),
                        max_tokens=min(tokens, HARD_CAP_TOKENS))
    pipe = SentinelPipeline(gate_enabled=True)
    fixtures = load_fixtures()
    if limit:
        fixtures = fixtures[:limit]

    results, aborted = [], None
    for fx in fixtures:
        started = time.time()
        try:
            # one reserve per fixture: gate-0 may drop before any model call, and
            # the settle() below records the real usage either way.
            guard.reserve(stage=f"fixture:{fx.name}", est_tokens=0)
            verdict = pipe.run(fx.build())
            stages = list(pipe.stages)
            route = stages[0].get("route") if stages else None
            triage = getattr(verdict, "triage", {}) or {}
            node = {
                "fixture": fx.name,
                "label": fx.label,
                "note": fx.note,
                # route lives on the gate stage; a dropped incident never reaches triage
                "routed": route or "escalate",
                "stages": [s.get("stage") for s in stages],
                "stage_detail": stages,
                "signature": triage.get("signature"),
                "tokens": sum(int(s.get("tokens", 0) or 0) for s in stages),
                "latency_s": round(time.time() - started, 2),
                "ok": bool(getattr(verdict, "ok", True)) and not triage.get("error"),
            }
            guard.settle(node["tokens"], ok=node["ok"])
            results.append(node)
        except BudgetExceeded as exc:
            aborted = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 - a demo must survive one bad fixture
            guard.settle(0, ok=False)
            results.append({"fixture": fx.name, "label": fx.label,
                            "error": f"{type(exc).__name__}: {exc}", "ok": False})
        finally:
            pipe.stages = []

    return {
        "kind": "sentinelpulse-live-capture",
        "mode": "live",
        "track": "indie",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "budget": guard.to_dict(),
        "aborted": aborted,
        "totals": {
            "fixtures": len(results),
            "calls": guard.calls,
            "tokens": guard.tokens,
            "errors": sum(1 for r in results if not r.get("ok", True)),
        },
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm-live", action="store_true",
                    help="required: acknowledges that this run spends credits")
    ap.add_argument("--fixture", type=int, default=None, help="only the first N fixtures")
    ap.add_argument("--calls", type=int, default=8)
    ap.add_argument("--tokens", type=int, default=6000)
    ap.add_argument("--dry", action="store_true",
                    help="print the exact request shape without sending it (0 credits)")
    args = ap.parse_args(argv)

    if args.dry:
        from app.core.meta_client import BASE_URL  # noqa: PLC0415
        model = os.environ.get("MODEL_API_MODEL", "muse-spark-1.3")
        cap = min(args.calls, HARD_CAP_CALLS)
        tok = min(args.tokens, HARD_CAP_TOKENS)
        print(f"endpoint : {BASE_URL}/chat/completions")
        print(f"model    : {model}")
        print(f"ceiling  : <= {cap} calls / <= {tok} tokens  (hard caps "
              f"{HARD_CAP_CALLS}/{HARD_CAP_TOKENS})")
        print("key      : resolved from MODEL_API_KEY (not printed)")
        print(f"fixtures : {args.fixture if args.fixture is not None else 'all'}")
        print("sent     : nothing — --dry performs no network call")
        return 0

    if not args.confirm_live:
        print("refusing: live capture spends credits. Re-run with --confirm-live "
              "(offline bundle: python -m demos.l3.build_bundle).", file=sys.stderr)
        return 4

    cap = min(args.calls, HARD_CAP_CALLS)
    print(f"live capture: <= {cap} calls / <= {min(args.tokens, HARD_CAP_TOKENS)} tokens")
    data = run(args.fixture, args.calls, args.tokens)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"live-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(data, indent=2, default=str) + "\n")
    t = data["totals"]
    print(f"-> {out}\n   fixtures={t['fixtures']} calls={t['calls']} "
          f"tokens={t['tokens']} errors={t['errors']}"
          + (f" aborted={data['aborted']}" if data["aborted"] else ""))
    return 0 if not data["aborted"] and not t["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
