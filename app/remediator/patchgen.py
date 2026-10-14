#!/usr/bin/env python3
"""
SentinelPulse :: remediator  (app/remediator/patchgen.py)

Turns a triage verdict into a MINIMAL, VALIDATED patch.

DESIGN DECISION (learned the hard way, verified against muse-spark-1.3)
-----------------------------------------------------------------------
Asking a model to hand-write a unified diff does not work reliably: it invents
hunk offsets and emits context lines without the leading space marker, so
`patch` refuses the result ("Hunk #1 FAILED at N").

So the model never authors a diff here. It returns exact find/replace EDITS
copied verbatim from the file content it was shown; Python applies them to a
sandbox copy and generates the unified diff with difflib. The diff is therefore
correct by construction, and a failed apply is fed back verbatim for one repair.

  propose(incident, triage) -> Remediation
      1. show the model the real, line-numbered file content
      2. model returns edits [{file, find, replace}] under an exactness contract
      3. apply to a sandbox copy of the repo (unique-match enforced)
      4. validate: python -> ast.parse; json -> json.loads; shell -> bash -n
      5. emit the diff difflib computed from the sandbox result

The generator NEVER writes to the live repo.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_OMITTED = "      ... [lines omitted: excerpt windowed around the relevant region] ..."

try:
    from app.core.incident import Incident
    from app.core.meta_client import MetaClient, MetaReply
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.incident import Incident
    from app.core.meta_client import MetaClient, MetaReply

EDIT_SYSTEM = """You are SentinelPulse's remediation engineer.

You are given an incident, its triage plan, and the CURRENT content of the file
most likely responsible. Produce the smallest possible fix.

Hard rules:
- Reply with ONE JSON object and nothing else.
- `find` MUST be an exact substring copied verbatim from the file content shown,
  whitespace and indentation included. It MUST appear exactly once in that file.
  Do NOT paraphrase it, and do NOT include the line numbers from the listing.
- `replace` is the exact text that takes its place.
- Change ONLY what fixes the incident. No reformatting, no renames, no drive-by edits.
- If you cannot produce a safe minimal fix, return "edits": [] and explain in
  `reason`. Never invent file content that was not shown to you.

Shape:
{
  "edits": [
    {"file": "app/foo.py", "find": "exact old text", "replace": "new text"}
  ],
  "rationale": "one short paragraph",
  "confidence": 0.0,
  "risk": "low|medium|high",
  "reason": ""
}"""


@dataclass
class Remediation:
    files_changed: list[str] = field(default_factory=list)
    diff: str = ""
    rationale: str = ""
    confidence: float = 0.0
    risk: str = "medium"
    reason: str = ""
    validated: bool = False
    validation_output: str = ""
    applied: bool = False
    sandbox: str = ""
    attempts: int = 0
    reasoning_tokens: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "files_changed": self.files_changed, "diff": self.diff,
            "rationale": self.rationale, "confidence": self.confidence,
            "risk": self.risk, "reason": self.reason, "validated": self.validated,
            "validation_output": self.validation_output[:600],
            "applied": self.applied, "sandbox": self.sandbox,
            "attempts": self.attempts,
            "reasoning_tokens": self.reasoning_tokens, "error": self.error,
        }


def _extract_json(text: str) -> dict:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError(f"no JSON in model output: {text[:200]!r}")


class PatchGenerator:
    def __init__(self, client: MetaClient | None = None, repo: str | Path | None = None,
                 model: str = "muse-spark-1.3"):
        self.client = client or MetaClient()
        self.repo = Path(repo).resolve() if repo else None
        self.model = model

    # ------------------------------------------------------------------ propose
    def propose(self, inc: Incident, triage, *, candidate_files: list[str] | None = None,
                reasoning_effort: str = "low") -> Remediation:
        if not self.repo or not self.repo.exists():
            return Remediation(error=f"repo not found: {self.repo}")

        files = candidate_files or self._candidates_from_plan(triage.plan)
        if not files:
            return Remediation(
                reason="no candidate file identified from the triage plan; "
                       "pass candidate_files explicitly")

        hints = self._hints(inc, triage)
        excerpts: dict[str, str] = {}
        for rel in files:
            p = self.repo / rel
            if p.is_file() and self._is_text(p):
                excerpts[rel] = self._excerpt(p, hints)
        if not excerpts:
            return Remediation(reason=f"candidate files not readable: {files}")

        shown = "\n\n".join(
            f"===== FILE: {rel} =====\n{body}" for rel, body in excerpts.items())
        prompt = (
            f"{EDIT_SYSTEM}\n\nINCIDENT\n{inc.brief()}\n\n"
            f"TRIAGE\nsignature: {triage.signature}\nroot_cause: {triage.root_cause}\n"
            f"plan: {json.dumps(triage.plan)}\n\n"
            f"CURRENT FILE CONTENT (each line is shown as `NNNN<TAB>code`; the leading "
            f"numbers are NOT part of the file and must NOT appear in `find`):\n{shown}"
        )

        rem = Remediation()
        for attempt in (1, 2):
            rem.attempts = attempt
            try:
                reply: MetaReply = self.client.chat(
                    prompt, model=self.model, reasoning_effort=reasoning_effort,
                    max_tokens=4000)
            except Exception as exc:  # noqa: BLE001
                rem.error = f"{type(exc).__name__}: {exc}"
                return rem

            rem.reasoning_tokens += reply.reasoning_tokens
            if reply.truncated_by_reasoning:
                rem.error = "reasoning-only response; raise max_tokens"
                return rem
            try:
                data = _extract_json(reply.text)
            except Exception as exc:  # noqa: BLE001
                rem.error = f"unparsed patch output: {exc}"
                return rem

            rem.rationale = data.get("rationale", "")
            rem.confidence = float(data.get("confidence", 0.0) or 0.0)
            rem.risk = data.get("risk", "medium")
            rem.reason = data.get("reason", "")
            edits = data.get("edits") or []
            if not edits:
                return rem  # honest refusal beats an invented patch

            ok, log, targets, diff, sandbox = self._sandbox_apply(edits)
            rem.validated, rem.validation_output = ok, log
            rem.diff, rem.files_changed = diff, targets
            rem.sandbox = sandbox
            if ok or attempt == 2:
                break

            prompt += (
                f"\n\n--- YOUR PREVIOUS ATTEMPT FAILED ---\n{log[:1000]}\n\n"
                f"Your edits were:\n{json.dumps(edits)[:1200]}\n\n"
                f"Recall: `find` must be copied verbatim from the file content above and "
                f"must appear EXACTLY ONCE. Recount the indentation and return a corrected "
                f"JSON object with the same shape."
            )
        return rem

    # ------------------------------------------------------- excerpt windowing
    # A blind head-slice is a correctness bug: the defect is usually NOT near the
    # top of the file, so the model is asked to fix code it was never shown and
    # then (correctly) refuses. Instead: small files go in whole; for large files
    # we window around the lines that actually match the incident's vocabulary.
    MAX_EXCERPT_CHARS = 24000
    WINDOW_LINES = 70
    MAX_WINDOWS = 5
    _STOPWORDS = frozenset({
        "about", "above", "after", "again", "against", "because", "before",
        "being", "below", "between", "consecutive", "could", "documented",
        "during", "every", "first", "instead", "might", "other", "permanently",
        "should", "samples", "sample", "their", "there", "these", "those",
        "through", "under", "until", "where", "which", "while", "without",
    })

    @classmethod
    def _hints(cls, inc: Incident, triage: Any) -> list[str]:
        """Vocabulary that locates the relevant region of a large file."""
        blobs = [getattr(triage, "signature", "") or "",
                 getattr(triage, "root_cause", "") or "",
                 getattr(inc, "summary", "") or ""]
        out: list[str] = []
        for blob in blobs:
            for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", blob):
                low = tok.lower()
                if low not in cls._STOPWORDS and low not in out:
                    out.append(low)
        return out

    @staticmethod
    def _merge_windows(idxs: list[int], n_lines: int) -> list[tuple[int, int]]:
        w = PatchGenerator.WINDOW_LINES
        spans: list[tuple[int, int]] = []
        for i in sorted(idxs):
            a, b = max(0, i - w), min(n_lines, i + w + 1)
            if spans and a <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], b))
            else:
                spans.append((a, b))
        return spans[:PatchGenerator.MAX_WINDOWS]

    def _excerpt(self, p: Path, hints: list[str]) -> str:
        text = p.read_text(errors="replace")
        lines = text.splitlines()
        if len(text) <= self.MAX_EXCERPT_CHARS:
            return self._numbered(text)

        idxs = [i for i, line in enumerate(lines)
                if any(h[:5] in line.lower() for h in hints)]
        windows = self._merge_windows(idxs, len(lines))
        if not windows:
            head = self._numbered("\n".join(lines[:self.WINDOW_LINES]))
            start = max(self.WINDOW_LINES, len(lines) - self.WINDOW_LINES)
            tail = self._numbered("\n".join(lines[start:]), start=start + 1)
            return f"{head}\n{_OMITTED}\n{tail}"

        body = f"\n{_OMITTED}\n".join(
            self._numbered("\n".join(lines[a:b]), start=a + 1) for a, b in windows)
        return body[:self.MAX_EXCERPT_CHARS]

    # ------------------------------------------------------------- plan parsing
    @staticmethod
    def _candidates_from_plan(plan: list[str]) -> list[str]:
        """Pull plausible file paths out of free-text remediation steps."""
        pat = re.compile(r"([\w./-]+\.(?:py|js|ts|tsx|json|ya?ml|toml|conf|cfg|ini|md|sh))")
        out: list[str] = []
        for step in plan or []:
            for m in pat.findall(step):
                if m not in out:
                    out.append(m)
        return out

    @staticmethod
    def _is_text(p: Path) -> bool:
        try:
            p.read_text(errors="strict")
            return True
        except (UnicodeDecodeError, OSError):
            return False

    @staticmethod
    def _numbered(text: str, start: int = 1) -> str:
        return "\n".join(f"{i:>5}\t{line}"
                         for i, line in enumerate(text.splitlines(), start))

    # ------------------------------------------------------- sandbox + validate
    def _sandbox_apply(self, edits: list[dict]) -> tuple[bool, str, list[str], str, str]:
        """Apply edits to a sandbox copy, validate, and emit a difflib diff."""
        sandbox = Path(tempfile.mkdtemp(prefix="sentinel-sbx-"))
        try:
            work = sandbox / "repo"
            shutil.copytree(self.repo, work, symlinks=True,
                            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".git"))
            log: list[str] = ["--- applying edits ---"]
            current: dict[str, str] = {}
            touched: list[str] = []

            for i, e in enumerate(edits, 1):
                rel = str(e.get("file", "")).lstrip("/")
                find = e.get("find", "")
                repl = e.get("replace", "")
                target = work / rel

                if not target.is_file():
                    return False, "\n".join(log + [f"edit {i}: {rel}: file not found"]), \
                        touched, "", str(sandbox)
                if not find:
                    return False, "\n".join(log + [f"edit {i}: {rel}: empty `find`"]), \
                        touched, "", str(sandbox)

                src = current.get(rel, target.read_text(errors="replace"))
                n = src.count(find)
                if n == 0:
                    return False, "\n".join(log + [
                        f"edit {i}: {rel}: `find` text is NOT present verbatim in the file"]), \
                        touched, "", str(sandbox)
                if n > 1:
                    return False, "\n".join(log + [
                        f"edit {i}: {rel}: `find` text matches {n} times; must be unique"]), \
                        touched, "", str(sandbox)

                current[rel] = src.replace(find, repl, 1)
                target.write_text(current[rel])
                if rel not in touched:
                    touched.append(rel)
                log.append(f"edit {i}: {rel}: applied ({len(find)} -> {len(repl)} chars)")

            log += ["", "--- post-apply checks ---"]
            ok = True
            for rel in touched:
                f = work / rel
                if f.suffix == ".py":
                    try:
                        ast.parse(f.read_text(errors="replace"))
                        log.append(f"{rel}: ast.parse OK")
                    except SyntaxError as exc:
                        ok = False
                        log.append(f"{rel}: SyntaxError {exc}")
                elif f.suffix == ".json":
                    try:
                        json.loads(f.read_text(errors="replace"))
                        log.append(f"{rel}: json.loads OK")
                    except json.JSONDecodeError as exc:
                        ok = False
                        log.append(f"{rel}: JSONDecodeError {exc}")
                elif f.suffix in (".yml", ".yaml"):
                    try:
                        import yaml
                        yaml.safe_load(f.read_text(errors="replace"))
                        log.append(f"{rel}: yaml.safe_load OK")
                    except ImportError:
                        log.append(f"{rel}: yaml module absent, skipped")
                    except Exception as exc:  # noqa: BLE001
                        ok = False
                        log.append(f"{rel}: YAML error {exc}")
                elif f.suffix == ".sh":
                    r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
                    ok = ok and r.returncode == 0
                    log.append(f"{rel}: bash -n rc={r.returncode} {r.stderr.strip()[:120]}")
                elif f.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                    passed, note = _typescript_check(f)
                    ok = ok and passed
                    log.append(f"{rel}: {note}")

            # difflib diff: correct by construction, applies cleanly
            parts: list[str] = []
            for rel in touched:
                before = (self.repo / rel).read_text(errors="replace").splitlines(keepends=True)
                after = (work / rel).read_text(errors="replace").splitlines(keepends=True)
                parts.extend(difflib.unified_diff(
                    before, after, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3))
            return ok, "\n".join(log), touched, "".join(parts), str(sandbox)
        except Exception as exc:  # noqa: BLE001
            return False, f"validation crashed: {type(exc).__name__}: {exc}", [], "", str(sandbox)


# --------------------------------------------------------------- js/ts admission
_TS_SYNTAX_CODES = ("1005", "1003", "1015", "1109", "1128", "1135", "1136", "1160",
                    "1434", "17008", "17004")   # unterminated/expected-brace family
_TS_BIN = Path("/home/ubuntu/hermes-agent/node_modules/.bin/tsc")
_TS_ERR_RE = re.compile(r"error TS(\d+):\s*(.*)")


def _typescript_check(path: Path, *, timeout: int = 90) -> tuple[bool, str]:
    """Syntax admission for a .ts/.tsx file via `tsc --noEmit`.

    Fails CLOSED on the syntax family (unbalanced braces, unterminated strings,
    TS1xxx parse errors). Type errors (TS2xxx+) are reported but tolerated: an
    isolated file has no ambient types, and a logic fix is not a typing exercise.
    """
    if not _TS_BIN.exists():
        return True, "tsc unavailable — js/ts admission skipped (closed on syntax? no)"
    cmd = [str(_TS_BIN), "--noEmit", "--allowJs", "--target", "es2020",
           "--moduleResolution", "bundler", "--module", "esnext",
           "--jsx", "preserve", "--skipLibCheck", "--pretty", "false", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"tsc timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return False, f"tsc invocation failed: {type(exc).__name__}: {exc}"

    out = f"{proc.stdout}\n{proc.stderr}"
    codes = [(m.group(1), m.group(2)[:140]) for m in _TS_ERR_RE.finditer(out)]
    syntax = [c for c in codes if c[0] in _TS_SYNTAX_CODES]
    types = [c for c in codes if c[0] not in _TS_SYNTAX_CODES]
    summary = (f"tsc rc={proc.returncode} errors={len(codes)} "
               f"(syntax={len(syntax)} type={len(types)})")
    if syntax:
        return False, f"{summary} SYNTAX FAIL: " + "; ".join(
            f"TS{c}: {t}" for c, t in syntax[:4])
    return True, summary + (f" first-type: TS{types[0][0]} {types[0][1]}" if types else " clean")


if __name__ == "__main__":
    import sys

    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    print(f"PatchGenerator ready. repo={Path(repo).resolve()}")