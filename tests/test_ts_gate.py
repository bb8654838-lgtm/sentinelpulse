"""Offline tests for the two-tier / opt-in strict TypeScript admission gate.

No network, no API keys, no `npm ci`. Runs against `_typescript_check` with
synthetic fixtures, proving three things:

  1. the DEFAULT path is unchanged (syntax fails closed, type errors tolerated);
  2. strict type-checking is OPT-IN and never on the default path;
  3. strict mode DEGRADES instead of blanket-failing when no dependency layer
     is resolvable (the TS2307 trap that made the first draft useless).

Run:  python tests/test_ts_gate.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.remediator.patchgen import _typescript_check  # noqa: E402

SYNTAX_BAD = "const x = {\n"                                   # TS1005
TYPE_BAD = 'const n: number = "not a number";\nexport default n;\n'  # TS2322
CLEAN = "const n: number = 42;\nexport default n;\n"
BARE_IMPORT = 'import { useEffect } from "react";\nexport const a = useEffect;\n'


def _fixtures(tmp: Path) -> dict[str, Path]:
    (tmp / "syntax.ts").write_text(SYNTAX_BAD)
    (tmp / "typeerr.ts").write_text(TYPE_BAD)
    (tmp / "clean.ts").write_text(CLEAN)
    (tmp / "importsreact.ts").write_text(BARE_IMPORT)
    layer = tmp / "layer"
    layer.mkdir(exist_ok=True)
    (layer / "react.ts").write_text("export const useEffect = () => {};\n")
    return {
        "syntax": tmp / "syntax.ts",
        "typeerr": tmp / "typeerr.ts",
        "clean": tmp / "clean.ts",
        "imports": tmp / "importsreact.ts",
        "layer": layer,
    }


def _call(path: Path, **kw) -> tuple[bool, str]:
    """Call the gate with a guaranteed-clean env, then restore it."""
    saved = {k: os.environ.pop(k, None)
             for k in ("SENTINEL_TS_TYPECHECK", "SENTINEL_TS_LAYER")}
    try:
        return _typescript_check(path, **kw)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        f = _fixtures(Path(td))
        results: list[tuple[str, bool]] = []

        def check(name: str, cond: bool) -> None:
            results.append((name, bool(cond)))

        # ---- Tier 1: DEFAULT path (no env, no kwargs) -------------------
        ok, note = _call(f["syntax"])
        check("default: syntax error fails CLOSED", ok is False and "SYNTAX FAIL" in note)
        ok, note = _call(f["typeerr"])
        check("default: type error TOLERATED", ok is True and "first-type: TS2322" in note)
        ok, note = _call(f["clean"])
        check("default: clean file passes", ok is True and "clean" in note)
        ok, note = _call(f["imports"])
        check("default: unresolved import tolerated", ok is True and "TS2307" in note)
        check("default: mode labelled syntax-only", "syntax-only" in note)

        # ---- Tier 2: strict requested, no layer -> degrade --------------
        ok, note = _call(f["typeerr"], typecheck=True)
        check("no layer: degrades instead of blanket-fail", ok is True)
        check("no layer: degradation is reported", "degraded" in note)

        # ---- Tier 3: strict + resolvable layer -> type errors block -----
        layer = str(f["layer"])
        ok, note = _call(f["typeerr"], typecheck=True, layer=layer)
        check("strict: type error BLOCKS", ok is False and "TYPE FAIL: TS2322" in note)
        ok, note = _call(f["clean"], typecheck=True, layer=layer)
        check("strict: clean file passes", ok is True)
        ok, note = _call(f["syntax"], typecheck=True, layer=layer)
        check("strict: syntax error still blocks", ok is False and "SYNTAX FAIL" in note)
        ok, note = _call(f["imports"], typecheck=True, layer=layer)
        check("strict: bare import RESOLVES via layer", ok is True and "clean" in note)

        # ---- Tier 4: env-driven strict (production path, no kwargs) -----
        env = dict(os.environ, SENTINEL_TS_TYPECHECK="1", SENTINEL_TS_LAYER=layer)
        probe = (
            "import sys;sys.path.insert(0,'.');"
            "from pathlib import Path;"
            "from app.remediator.patchgen import _typescript_check as c;"
            f"print(c(Path({str(f['typeerr'])!r})))"
        )
        got = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                             text=True, env=env, cwd=str(ROOT))
        check("env strict=1 + layer blocks type errors",
              got.stdout.strip().startswith("(False"))

        env_off = {k: v for k, v in os.environ.items()
                   if k not in ("SENTINEL_TS_TYPECHECK", "SENTINEL_TS_LAYER")}
        got = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                             text=True, env=env_off, cwd=str(ROOT))
        check("env unset (default) tolerates type errors",
              got.stdout.strip().startswith("(True"))

    for name, ok in results:
        print(("  [PASS] " if ok else "  [FAIL] ") + name)
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} TypeScript gate checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())