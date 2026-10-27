#!/usr/bin/env python3
"""Freeze the L2 evidence inputs so L3 numbers can never drift from a live run.

Writes bench/evidence/FREEZE-<YYYYMMDD>.json containing the sha256 of every
frozen artifact, then marks those files read-only. Re-running is idempotent:
an existing freeze is reported, and a changed hash is a hard failure, not a
silent overwrite -- that is the whole point of a freeze.

Usage:
    python -m demos.l3.freeze_evidence            # freeze (or verify) today
    python -m demos.l3.freeze_evidence --check    # verify only, never write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "bench" / "evidence"
TARGETS = [
    ROOT / "bench" / "evidence" / "tier0-bench-LIVE-20261027.json",
    ROOT / "bench" / "evidence" / "tier0-bench-20261020.json",
    ROOT / "bench" / "tier0_bench.py",
    ROOT / "app" / "core" / "gatekeeper.py",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot() -> dict:
    artifacts = []
    for p in TARGETS:
        if not p.exists():
            artifacts.append({"path": str(p.relative_to(ROOT)), "state": "MISSING"})
            continue
        st = p.stat()
        artifacts.append({
            "path": str(p.relative_to(ROOT)),
            "bytes": st.st_size,
            "sha256": sha256(p),
            "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            "state": "frozen",
        })
    return {
        "kind": "l2-evidence-freeze",
        "product": "SentinelPulse",
        "lane": "L2 -> L3 handoff",
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(ROOT),
        "artifacts": artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify against existing freeze only")
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    args = ap.parse_args(argv)

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / f"FREEZE-{args.date}.json"
    current = snapshot()

    if out.exists():
        prior = json.loads(out.read_text())
        prior_map = {a["path"]: a.get("sha256") for a in prior.get("artifacts", [])}
        drift = [a for a in current["artifacts"]
                 if a.get("sha256") and prior_map.get(a["path"]) not in (None, a["sha256"])]
        if drift:
            print("FREEZE VIOLATION -- frozen artifacts changed:", file=sys.stderr)
            for a in drift:
                print(f"  {a['path']}  {prior_map[a['path']][:12]} -> {a['sha256'][:12]}",
                      file=sys.stderr)
            return 2
        print(f"freeze intact: {out.relative_to(ROOT)}  ({len(prior_map)} artifacts)")
        return 0

    if args.check:
        print("no freeze file yet; run without --check to create", file=sys.stderr)
        return 3

    out.write_text(json.dumps(current, indent=2) + "\n")
    for a in current["artifacts"]:
        if a.get("state") != "frozen":
            continue
        p = ROOT / a["path"]
        # only evidence payloads are write-protected: source stays editable so the
        # sprint can still iterate, while the numbers it produced stay pinned.
        if p.parent.name != "evidence":
            continue
        mode = p.stat().st_mode
        p.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    print(f"frozen {len(current['artifacts'])} artifacts -> {out.relative_to(ROOT)}")
    for a in current["artifacts"]:
        print(f"  {a.get('sha256', 'MISSING')[:16]}  {a['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
