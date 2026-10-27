#!/usr/bin/env python3
"""Run the real demo commands and record their exact stdout for the video.

Nothing on screen is typed by hand: every terminal pane in the render is the
captured output of a command that actually ran on this machine seconds earlier
(captured_at is stamped per command).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BUILD = HERE / "build"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

PY = str(ROOT / ".venv" / "bin" / "python")
if not Path(PY).exists():
    PY = sys.executable

# key -> (display command, argv)
JOBS = [
    ("freeze", "$PY -m demos.l3.freeze_evidence --check",
     [PY, "-m", "demos.l3.freeze_evidence", "--check"]),
    ("bundle", "$PY -m demos.l3.build_bundle",
     [PY, "-m", "demos.l3.build_bundle"]),
    ("dry", "$PY -m demos.l3.run_live --dry --calls 1",
     [PY, "-m", "demos.l3.run_live", "--dry", "--calls", "1"]),
    ("refuse", "$PY -m demos.l3.run_live",
     [PY, "-m", "demos.l3.run_live"]),
]


def run(cmd, argv, timeout=180):
    env = dict(os.environ, PYTHONUNBUFFERED="1", NO_COLOR="1", TERM="dumb")
    t0 = datetime.now(timezone.utc)
    p = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                       timeout=timeout, env=env)
    text = ANSI.sub("", (p.stdout or "") + (p.stderr or "")).replace("\r", "")
    return {
        "cmd": cmd.replace("$PY", ".venv/bin/python"),
        "rc": p.returncode,
        "text": text.strip("\n"),
        "captured_at": t0.isoformat(timespec="seconds"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BUILD / "transcripts.json"))
    a = ap.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    data = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": PY, "repo": str(ROOT), "runs": {}}
    for key, disp, argv in JOBS:
        rec = run(disp, argv)
        data["runs"][key] = rec
        print(f"[{key}] rc={rec['rc']} lines={rec['text'].count(chr(10)) + 1}")
    Path(a.out).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {a.out}")
    # the demo's own guard: refusal must stay a refusal
    if data["runs"]["refuse"]["rc"] == 0:
        print("FATAL: run_live ran without --confirm-live (guard broken)", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
