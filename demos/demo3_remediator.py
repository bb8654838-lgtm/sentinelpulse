#!/usr/bin/env python3
"""
DEMO 3 — Agentic code remediator               (muse-spark-1.3 + sandbox)
=========================================================================
Injects a REAL defect into a scratch copy of the repo, then lets the remediator
find, patch, sandbox-apply and validate it. Nothing touches the live tree.

Run:  .venv/bin/python demos/demo3_remediator.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.incident import Incident, Severity, Source  # noqa: E402
from app.core.meta_client import MetaClient  # noqa: E402
from app.core.pipeline import SentinelPipeline  # noqa: E402
from app.remediator.patchgen import PatchGenerator  # noqa: E402

DEFECT_FILE = "app/probes/health.py"

# A genuine logic defect: the resolver-timeout path is silently swallowed, so a
# SERVFAIL looks like success and the incident never escalates.
BROKEN_FN = '''def check_timeout(host: str, timeout: float = 5.0) -> bool:
    """Return True when the host answers within `timeout` seconds."""
    try:
        import socket
        socket.create_connection((host, 80), timeout=timeout)
        return True
    except Exception:
        return True
'''

FIXED_FN = '''def check_timeout(host: str, timeout: float = 5.0) -> bool:
    """Return True when the host answers within `timeout` seconds."""
    import socket
    try:
        socket.create_connection((host, 80), timeout=timeout)
        return True
    except (socket.timeout, OSError):
        return False
'''


def rule(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def make_scratch_repo() -> Path:
    """Copy the repo to a scratch dir and inject the real defect."""
    sandbox = Path(tempfile.mkdtemp(prefix="sentinel-demo3-"))
    work = sandbox / "repo"
    shutil.copytree(
        ROOT, work, symlinks=True,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".git",
                                      "fixtures", "*.pyc", ".pytest_cache"),
    )
    target = work / DEFECT_FILE
    src = target.read_text()
    marker = 'if __name__ == "__main__":'
    assert marker in src, "anchor comment missing in health.py"
    target.write_text(src.replace(marker, BROKEN_FN + "\n\n" + marker, 1))
    return work


def main() -> int:
    rule("PILLAR 3 :: AGENTIC CODE REMEDIATOR  (muse-spark-1.3 authors, sandbox validates)")

    work = make_scratch_repo()
    print(f"scratch repo : {work}")
    print(f"injected defect in : {DEFECT_FILE}")
    print("  -> except Exception: return True   (swallows SERVFAIL as success)")

    client = MetaClient()
    pipe = SentinelPipeline(client)

    inc = Incident(
        target=DEFECT_FILE,
        source=Source.MANUAL,
        summary=("check_timeout() returns True on every exception, so DNS/connection "
                 "timeouts are reported as healthy and incidents never escalate"),
        severity=Severity.HIGH,
    )
    inc.add("code", "bare `except Exception: return True` masks real failures",
            file=DEFECT_FILE, function="check_timeout")
    print(f"\n{inc.brief()}")

    rule("STAGE 1 :: triage")
    tr = pipe.triage(inc)
    if tr.error:
        print(f"  ERROR: {tr.error}")
        return 1
    print(f"  signature    : {tr.signature}")
    print(f"  root_cause   : {tr.root_cause}")
    print(f"  auto_fixable : {tr.auto_fixable}")
    print("  plan:")
    for i, s in enumerate(tr.plan, 1):
        print(f"    {i}. {s}")

    rule("STAGE 2 :: patch generation")
    gen = PatchGenerator(client, work)
    rem = gen.propose(inc, tr, candidate_files=[DEFECT_FILE])
    if rem.error:
        print(f"  ERROR: {rem.error}")
        print(f"  reason: {rem.reason}")
        return 1
    print(f"  files_changed    : {rem.files_changed}")
    print(f"  confidence/risk  : {rem.confidence} / {rem.risk}")
    print(f"  rationale        : {rem.rationale}")
    print(f"  reasoning_tokens : {rem.reasoning_tokens}")
    print("\n  --- generated diff ---")
    print("\n".join("  " + l for l in (rem.diff or "(none)").splitlines()[:40]))

    rule("STAGE 3 :: sandbox validation")
    print(rem.validation_output or "(no output)")
    print(f"\n  validated : {rem.validated}")
    print(f"  sandbox   : {rem.sandbox}")

    rule("STAGE 4 :: does the patch actually fix the defect?")
    fixed_text = (Path(rem.sandbox) / "repo" / DEFECT_FILE).read_text() if rem.validated else ""

    # The defect is behavioural, not textual: the failure path must stop claiming
    # success. Assert the behaviour, not the exact spelling of the except clause.
    def _except_bodies(src: str) -> list[str]:
        out, lines, i = [], src.splitlines(), 0
        while i < len(lines):
            if lines[i].lstrip().startswith("except"):
                body = []
                j = i + 1
                while j < len(lines) and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                    if lines[j].strip():
                        body.append(lines[j].strip())
                    j += 1
                out.append(" ".join(body))
                i = j
            else:
                i += 1
        return out

    bodies = _except_bodies(fixed_text)
    behaviours = [
        ("no except-block still returns True (defect gone)",
         bool(bodies) and not any("return True" in b for b in bodies)),
        ("at least one except-block returns False",
         any("return False" in b for b in bodies)),
        ("patched file still parses", _parses(fixed_text)),
        ("live repo untouched (patch stayed in sandbox)",
         "return False" not in (ROOT / DEFECT_FILE).read_text()),
    ]
    for name, ok in behaviours:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    all_ok = rem.validated and all(ok for _, ok in behaviours)
    print(f"\n{'=' * 78}\nDEMO 3 {'COMPLETE — patch validated in sandbox' if all_ok else 'INCOMPLETE'}")
    print(f"{'=' * 78}")
    return 0 if all_ok else 1


def _parses(src: str) -> bool:
    import ast
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())