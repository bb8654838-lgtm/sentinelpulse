#!/usr/bin/env python3
"""
SentinelPulse :: GitHub publisher  (app/remediator/gh_publisher.py)

Takes a SANDBOX-VALIDATED remediation and turns it into a real pull request.

Hard gate
---------
Nothing is published unless the patch came out of the sandbox with
`validated=True`, a non-empty diff, and at least one changed file. A publisher
that will happily open a PR for an unvalidated diff is a publisher that ships
outages. `publish()` refuses and says why.

Flow
----
  1. clone the target repo into a scratch workdir (never the live tree)
  2. `git apply --check` the difflib diff — proof the patch applies as text
  3. branch off the base, apply, commit (author: Sam, no tooling fingerprints)
  4. push the branch
  5. POST /repos/{repo}/pulls with a body built from the triage reasoning,
     the validation transcript and diff telemetry

dry_run=True (the default) executes steps 1-3 for real in the local clone and
prints the exact push command + PR request it WOULD have sent, so the whole
contract is exercised without touching a remote. Pass --live to actually push.

Credentials: GITHUB_PERSONAL_ACCESS_TOKEN from the environment, else the
sanctioned R6 helper `env-key.sh`, else `gh auth token`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API = "https://api.github.com"
ENV_KEY_HELPER = "/home/ubuntu/.hermes/scripts/env-key.sh"
ENV_VAR = "GITHUB_PERSONAL_ACCESS_TOKEN"

# Commit authorship is deliberately human and tool-free (no bot/agent traces).
AUTHOR_NAME = os.environ.get("SENTINEL_GIT_AUTHOR", "Sam")
AUTHOR_EMAIL = os.environ.get("SENTINEL_GIT_EMAIL", "sam@devilx-digital-labs.dev")


class PublishRefused(RuntimeError):
    """Raised when the remediation is not safe to publish."""


@dataclass
class PublishResult:
    ok: bool = False
    dry_run: bool = True
    repo: str = ""
    branch: str = ""
    base: str = ""
    commit_sha: str = ""
    pr_number: int | None = None
    pr_url: str = ""
    files: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    error: str = ""
    request: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "dry_run": self.dry_run, "repo": self.repo,
            "branch": self.branch, "base": self.base, "commit_sha": self.commit_sha,
            "pr_number": self.pr_number, "pr_url": self.pr_url, "files": self.files,
            "log": self.log, "error": self.error, "request": self.request,
            "elapsed_s": self.elapsed_s,
        }

    def summary_line(self) -> str:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        if not self.ok:
            return f"[{mode}] REFUSED/FAILED — {self.error}"
        pr = f"PR #{self.pr_number} {self.pr_url}" if self.pr_number else "PR payload staged (dry-run)"
        return f"[{mode}] branch={self.branch} commit={self.commit_sha[:10]} {pr}"


# ------------------------------------------------------------------ diff metrics
def diff_stat(diff: str) -> dict[str, Any]:
    """Telemetry extracted from a unified diff: files, hunks, +/- counts."""
    files: list[str] = []
    insertions = deletions = hunks = 0
    for line in (diff or "").splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            files.append(name[2:] if name.startswith("b/") else name)
        elif line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"files": files, "insertions": insertions, "deletions": deletions,
            "hunks": hunks, "lines": len((diff or "").splitlines())}


def _slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "incident")[:limit].strip("-")


# --------------------------------------------------------------------- publisher
class GitHubPublisher:
    """Publish a validated remediation as a GitHub pull request."""

    def __init__(self, repo: str, *, token: str | None = None,
                 base_branch: str | None = None, remote_url: str | None = None,
                 workdir: str | Path | None = None, dry_run: bool = True,
                 api_base: str = API, timeout: int = 60):
        self.repo = repo.strip().strip("/")
        self._token = token
        self.base_branch = base_branch
        self.remote_url = remote_url or f"https://github.com/{self.repo}.git"
        self.workdir = Path(workdir) if workdir else None
        self.dry_run = dry_run
        self.api_base = api_base
        self.timeout = timeout

    # ------------------------------------------------------------------ plumbing
    @property
    def token(self) -> str:
        if self._token:
            return self._token
        tok = os.environ.get(ENV_VAR, "").strip()
        if not tok:
            try:
                tok = subprocess.run([ENV_VAR and "bash", ENV_KEY_HELPER, ENV_VAR],
                                     capture_output=True, text=True, timeout=20).stdout.strip()
            except Exception:  # noqa: BLE001
                tok = ""
        if not tok:
            try:
                tok = subprocess.run(["gh", "auth", "token"], capture_output=True,
                                     text=True, timeout=20).stdout.strip()
            except Exception:  # noqa: BLE001
                tok = ""
        if not tok:
            raise PublishRefused(
                f"no GitHub credential: set {ENV_VAR}, or `bash {ENV_KEY_HELPER} {ENV_VAR}`, "
                f"or `gh auth login`")
        self._token = tok
        return tok

    def _api(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.api_base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "SentinelPulse/0.2")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode(errors="replace")
                return resp.status, (json.loads(body) if body.strip() else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"message": raw[:400]}

    def whoami(self) -> dict:
        _s, data = self._api("GET", "/user")
        return data

    def repo_info(self) -> dict:
        _s, data = self._api("GET", f"/repos/{self.repo}")
        return data

    def resolve_base(self) -> str:
        if self.base_branch:
            return self.base_branch
        info = self.repo_info()
        self.base_branch = str(info.get("default_branch") or "main")
        return self.base_branch

    @staticmethod
    def _git(args: list[str], cwd: Path, *, check: bool = True) -> tuple[int, str]:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        # Never let an in-URL credential reach a log, an exception or an audit record.
        out = re.sub(r"//[^/@\s]+:[^/@\s]+@", "//***@", out)
        if check and r.returncode != 0:
            raise PublishRefused(f"git {' '.join(args[:2])} failed: {out[:400]}")
        return r.returncode, out

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "***") if self._token else text

    def _auth_remote_url(self) -> str:
        """Bake x-access-token auth into the HTTPS remote for git clone/push only.

        Laptops/CI have no interactive credential prompt, so a bare
        https://github.com/... remote always dies with
        'could not read Username'. The token lives only in this in-memory URL;
        every log line goes through _redact() first. Overridden by
        SENTINEL_GIT_REMOTE (e.g. an ssh:// remote handled by an agent).
        """
        override = os.environ.get("SENTINEL_GIT_REMOTE", "").strip()
        if override:
            return override
        tok = self.token
        host_path = f"github.com/{self.repo}.git"
        return f"https://x-access-token:{tok}@{host_path}"

    # -------------------------------------------------------------------- publish
    def publish(self, remediation, incident, triage, *, verdict: dict | None = None,
                title: str | None = None, branch: str | None = None,
                body_extra: str = "") -> PublishResult:
        t0 = time.time()
        res = PublishResult(repo=self.repo, dry_run=self.dry_run)

        # ---- gate 1: the remediation must have survived the sandbox
        if not getattr(remediation, "validated", False):
            res.error = "refused: remediation is not sandbox-validated"
            res.log.append(f"validation_output: {getattr(remediation, 'validation_output', '')[:300]}")
            res.elapsed_s = round(time.time() - t0, 2)
            return res
        if not getattr(remediation, "diff", "").strip():
            res.error = "refused: remediation produced an empty diff"
            res.elapsed_s = round(time.time() - t0, 2)
            return res
        files = list(getattr(remediation, "files_changed", []) or [])
        if not files:
            res.error = "refused: remediation changed no files"
            res.elapsed_s = round(time.time() - t0, 2)
            return res
        res.files = files

        sandbox_repo = Path(getattr(remediation, "sandbox", "") or "") / "repo"
        if not sandbox_repo.is_dir():
            res.error = f"refused: sandbox tree missing at {sandbox_repo}"
            res.elapsed_s = round(time.time() - t0, 2)
            return res

        sig = getattr(triage, "signature", "INCIDENT")
        branch = branch or f"sentinelpulse/{_slug(sig)}-{incident.incident_id[:6]}"
        res.branch = branch

        staging = self.workdir or Path(tempfile.mkdtemp(prefix="sentinel-publish-"))
        try:
            base = self.resolve_base()
            res.base = base
            clone = staging / "repo"
            try:
                auth_url = self._auth_remote_url()
            except PublishRefused:
                # Dry-run with no credential at all: still exercise the local
                # clone/apply/commit contract against the public remote.
                auth_url = self.remote_url

            # ---- clone
            if (clone / ".git").is_dir():
                res.log.append(f"reusing workdir {clone}")
                self._git(["remote", "set-url", "origin", auth_url], clone, check=False)
                self._git(["fetch", "--all", "--prune"], clone, check=False)
            else:
                rc, out = self._git(["clone", "--quiet", auth_url, str(clone)],
                                    staging, check=False)
                if rc != 0:
                    res.error = f"clone failed: {self._redact(out)[:400]}"
                    res.elapsed_s = round(time.time() - t0, 2)
                    return res
                res.log.append(f"cloned {self._redact(self.remote_url)}")

            self._git(["-c", f"user.name={AUTHOR_NAME}", "-c", f"user.email={AUTHOR_EMAIL}",
                       "checkout", "-B", branch, f"origin/{base}"], clone)

            # ---- gate 2: the diff must apply as TEXT (independent of the sandbox copy)
            diff_file = staging / "remediation.patch"
            diff_file.write_text(remediation.diff)
            rc, out = self._git(["apply", "--check", "--verbose", str(diff_file)], clone, check=False)
            if rc == 0:
                self._git(["apply", str(diff_file)], clone)
                res.log.append("git apply --check: OK (diff applies cleanly as text)")
                applied_via = "git apply"
            else:
                # The diff is a difflib artifact; copy the sandbox truth instead,
                # but say so out loud rather than hiding the fallback.
                res.log.append(f"git apply --check rc={rc}: {out[:200]}")
                for rel in files:
                    src = sandbox_repo / rel
                    if not src.is_file():
                        res.error = f"neither git apply nor sandbox copy has {rel}"
                        res.elapsed_s = round(time.time() - t0, 2)
                        return res
                    dst = clone / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                applied_via = "sandbox file copy"
                res.log.append(f"fell back to {applied_via} (content provenance: sandbox)")

            # ---- commit
            self._git(["add", "-A"], clone)
            rc, staged = self._git(["diff", "--cached", "--stat"], clone, check=False)
            if not staged.strip():
                res.error = "refused: nothing staged after applying the patch (no-op diff)"
                res.elapsed_s = round(time.time() - t0, 2)
                return res
            res.log.append(f"staged:\n{staged}")

            subject = title or self._commit_subject(incident, triage)
            body = self._commit_body(incident, triage, remediation, applied_via)
            self._git(["-c", f"user.name={AUTHOR_NAME}", "-c", f"user.email={AUTHOR_EMAIL}",
                       "commit", "--quiet", "-m", subject, "-m", body], clone)
            rc, sha = self._git(["rev-parse", "HEAD"], clone)
            res.commit_sha = sha.strip()
            res.log.append(f"commit {res.commit_sha[:10]} on {branch}")

            # ---- push
            push_cmd = ["push", "--quiet", "-u", "origin", branch]
            if self.dry_run:
                res.log.append(f"DRY-RUN: would run `git {' '.join(push_cmd)}` "
                               f"(remote {self._redact(self.remote_url)})")
            else:
                rc, out = self._git(push_cmd, clone, check=False)
                if rc != 0:
                    res.error = f"push failed: {self._redact(out)[:400]}"
                    res.elapsed_s = round(time.time() - t0, 2)
                    return res
                res.log.append(f"pushed {branch} to origin")

            # ---- PR
            pr_payload = {
                "title": subject,
                "head": branch,
                "base": base,
                "body": self.build_pr_body(incident, triage, remediation, verdict,
                                           extra=body_extra),
                "draft": False,
            }
            res.request = {"method": "POST", "path": f"/repos/{self.repo}/pulls",
                           "payload": pr_payload}

            if self.dry_run:
                res.log.append("DRY-RUN: PR request staged (not sent)")
                res.ok = True
                res.elapsed_s = round(time.time() - t0, 2)
                return res

            status, data = self._api("POST", f"/repos/{self.repo}/pulls", pr_payload)
            if status in (200, 201):
                res.pr_number = data.get("number")
                res.pr_url = data.get("html_url", "")
                res.ok = True
                res.log.append(f"PR opened: {res.pr_url}")
            elif status == 422 and "already exists" in str(data.get("errors", data)).lower():
                status2, existing = self._api(
                    "GET", f"/repos/{self.repo}/pulls?head={self.repo.split('/')[0]}:{branch}&state=open")
                if status2 == 200 and existing:
                    res.pr_number = existing[0].get("number")
                    res.pr_url = existing[0].get("html_url", "")
                    res.ok = True
                    res.log.append(f"PR already open: {res.pr_url}")
            else:
                res.error = f"PR create failed HTTP {status}: {str(data)[:300]}"
        except PublishRefused as exc:
            res.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            res.error = f"{type(exc).__name__}: {exc}"
        res.elapsed_s = round(time.time() - t0, 2)
        return res

    # ------------------------------------------------------------------- messages
    @staticmethod
    def _commit_subject(incident, triage) -> str:
        sig = (getattr(triage, "signature", "") or "INCIDENT").lower().replace("_", " ")
        target = incident.target
        short = target if len(target) <= 48 else target[:45] + "..."
        return f"fix: {sig} affecting {short}"

    @staticmethod
    def _commit_body(incident, triage, remediation, applied_via: str) -> str:
        stat = diff_stat(remediation.diff or "")
        return (
            f"Incident: {incident.incident_id}\n"
            f"Signature: {getattr(triage, 'signature', '?')}\n"
            f"Root cause: {getattr(triage, 'root_cause', '')}\n"
            f"Files: {', '.join(remediation.files_changed)}\n"
            f"Diff: +{stat['insertions']} / -{stat['deletions']} across "
            f"{len(stat['files'])} file(s)\n"
            f"Validation: {getattr(remediation, 'validation_output', '').strip().splitlines()[-1:] or ['n/a']}\n"
            f"Applied via: {applied_via}\n"
        )

    def build_pr_body(self, incident, triage, remediation, verdict: dict | None = None,
                      *, extra: str = "") -> str:
        """Diagnostic reasoning summary + diff telemetry, auto-filled."""
        stat = diff_stat(remediation.diff or "")
        v = verdict or {}
        vis = (v.get("visual") or {}) if isinstance(v.get("visual"), dict) else {}

        def _bullets(items, empty="— none —"):
            return "\n".join(f"- {i}" for i in items) if items else empty

        plan = _bullets([f"{n}. {s}" for n, s in enumerate(getattr(triage, "plan", []) or [], 1)])
        files = _bullets([f"`{f}`" for f in remediation.files_changed])
        val = (remediation.validation_output or "").strip() or "no validation transcript"
        if len(val) > 3000:
            val = val[:3000] + "\n… (truncated)"

        visual_block = ""
        if vis:
            findings = vis.get("findings") or []
            visual_block = (
                "\n### Visual inspection\n\n"
                f"- defaced: `{vis.get('defaced')}` · layout_broken: `{vis.get('layout_broken')}` "
                f"· overlay_suspected: `{vis.get('overlay_suspected')}`\n"
                f"- summary: {vis.get('summary', '')}\n"
                + ("".join(
                    f"- `{f.get('kind')}` @ {f.get('where')} (conf {f.get('confidence')}): "
                    f"{f.get('detail')}\n" for f in findings) if findings else "")
            )

        return f"""## Summary

Automated remediation for incident `{incident.incident_id}` on **{incident.target}**.

**Signature:** `{getattr(triage, 'signature', '?')}` · **Blast radius:** `{getattr(triage, 'blast_radius', '?')}` · **Risk:** `{getattr(remediation, 'risk', '?')}` · **Confidence:** `{getattr(remediation, 'confidence', 0.0)}`

### Symptom

{incident.summary}

### Root cause

{getattr(triage, 'root_cause', '(not determined)')}

### Remediation plan

{plan}

## Changes

{files}

```diff
{self._trim_diff(remediation.diff or '')}
```

## Diff telemetry

- files touched: **{len(stat['files'])}** · hunks: **{stat['hunks']}**
- insertions **+{stat['insertions']}** / deletions **-{stat['deletions']}**
- diff lines: {stat['lines']}

## Diagnostic reasoning (model telemetry)

- model: `muse-spark-1.3`
- triage reasoning tokens: `{getattr(triage, 'reasoning_tokens', 0)}` · latency `{getattr(triage, 'latency_s', 0)}s`
- patch reasoning tokens: `{getattr(remediation, 'reasoning_tokens', 0)}` · attempts `{getattr(remediation, 'attempts', 0)}`

**Rationale:** {getattr(remediation, 'rationale', '') or '(none supplied)'}
{visual_block}
## Validation

<details><summary>sandbox transcript (AST / config admission)</summary>

```
{val}
```
</details>

## Verification

{getattr(triage, 'verification', '') or 'Apply the change in staging and re-run the health probes that produced this incident.'}

## Rollback

Revert this commit — the change is confined to the files listed above.

---
{extra}
"""
    @staticmethod
    def _trim_diff(diff: str, limit: int = 6000) -> str:
        return diff if len(diff) <= limit else diff[:limit] + "\n… (diff truncated)"


# --------------------------------------------------------------------------- CLI
if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="SentinelPulse GitHub publisher")
    ap.add_argument("repo", help="owner/name")
    ap.add_argument("--live", action="store_true", help="actually push + open the PR")
    ap.add_argument("--check", action="store_true", help="auth + repo contract check only")
    a = ap.parse_args()

    pub = GitHubPublisher(a.repo, dry_run=not a.live)
    if a.check:
        me = pub.whoami()
        info = pub.repo_info()
        print(json.dumps({
            "auth": {"login": me.get("login"), "id": me.get("id")},
            "repo": {"full_name": info.get("full_name"),
                     "default_branch": info.get("default_branch"),
                     "private": info.get("private"),
                     "permissions": info.get("permissions")},
        }, indent=2))
    else:
        print(f"GitHubPublisher ready. repo={pub.repo} dry_run={pub.dry_run}")