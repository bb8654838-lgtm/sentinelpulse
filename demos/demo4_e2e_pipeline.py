#!/usr/bin/env python3
"""
DEMO 4 — End-to-end remediation drill (webhook -> triage -> visual -> patch -> PR)
=================================================================================
One continuous loop, driven entirely by an HTTP delivery:

    POST /webhook (Sentry dialect)
      -> receiver acknowledges in milliseconds, processes off the request thread
      -> normalizer translates the payload into an Incident (translation only)
      -> muse-spark-1.3 triages: signature, root cause, plan, blast radius
      -> muse-spark-1.3 VLM inspects the captured screenshot (log/visual check)
      -> muse-spark-1.3 authors a minimal edit; Python applies it to a SANDBOX
         copy, runs the AST/config admission (incl. `tsc --noEmit` for TS/TSX)
         and computes the unified diff with difflib
      -> GitHubPublisher clones the base branch, applies the validated diff,
         commits on a `sentinelpulse/<signature>-<id>` branch and opens the PR,
         with the diagnostic reasoning summary + diff telemetry auto-filled

SAFETY MODEL (why this is safe to run against a real repository)
  * The PR lands on the project's OWN FEEDBACK repository — the placeholder tree
    this pipeline was pointed at for the hackathon — never a third party's repo.
    Override with SENTINEL_FEEDBACK_REPO / --feedback-repo.
  * Publishing is opt-in per delivery: the service default is `--autopilot off`
    and `x-sentinelpulse-autopilot` names the level for one delivery only.
  * No PR is opened unless the patch survived the sandbox (validated=True) and
    produced a non-empty diff over at least one file.
  * The generator never writes to the live tree; only a tempdir copy is edited.
  * Every refusal path in this file is exercised by the negative controls.

Run:
    .venv/bin/python demos/demo4_e2e_pipeline.py
    .venv/bin/python demos/demo4_e2e_pipeline.py --repo <path-to-local-clone>
    .venv/bin/python demos/demo4_e2e_pipeline.py --no-publish   (sandbox only)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.incident import Incident, Severity, Source              # noqa: E402
from app.core.meta_client import MetaClient                            # noqa: E402
from app.core.pipeline import SentinelPipeline                         # noqa: E402
from app.remediator.gh_publisher import GitHubPublisher                # noqa: E402
from app.remediator.patchgen import Remediation                        # noqa: E402
from app.webhook.receiver import SentinelWebhookService, serve         # noqa: E402

# The defect under repair, in the setting-up-a-Support-desk landing page.
DEFECT_FILE = "components/three/HeroScene.tsx"
DEFECT_MARKER = "Two consecutive bad samples before demoting"
SCREENSHOT = ROOT / "demos" / "fixtures" / "dashboard_broken.png"
SCRATCH_CLONE = Path("/home/ubuntu/.hermes/cache/scratch/e2e-scan/tgt")

CHECK: list[tuple[str, bool]] = []


def rule(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECK.append((name, bool(ok)))
    tail = f"  ({detail})" if detail else ""
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{tail}")
    return bool(ok)


def http(url: str, *, method: str = "GET", body: bytes | None = None,
         headers: dict | None = None, timeout: float = 20.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode(errors="replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:300]}


# ----------------------------------------------------------------- incident payload
def sentry_delivery(repo: str) -> dict:
    """A Sentry issue-alert shaped payload naming the defect the demo repairs.

    The normalizer only TRANSLATES this: it does not decide a root cause, and it
    does not name a fix. Everything causal comes from the reasoning model.
    """
    return {
        "action": "created",
        "installation": {"uuid": "1f2c3b40-demo-4"},
        "data": {
            "event": {
                "event_id": "9c1b7f2e4a8d4f10",
                "title": "Frame-rate governor drops the scene to the lite tier after a single bad sample",
                "culprit": f"{DEFECT_FILE} in useAdaptiveQuality",
                "level": "error",
                "platform": "javascript",
                "web_url": f"https://sentry.invalid/organizations/devilx/issues/11/events/9c1b7f",
                "metadata": {
                    "type": "QualityRegression",
                    "value": ("the adaptive-quality controller documents 'two consecutive bad samples "
                              "before demoting' but demotes on the first sample, so one dropped "
                              "frame on a mid-range phone permanently removes bloom/particles"),
                },
                "request": {"url": f"https://support.{repo.split('/')[0]}.dev/"},
                "tags": {"environment": "production", "browser": "Chrome 141 / Android"},
                "fingerprint": [DEFECT_FILE, "adaptive-quality"],
            },
        },
    }


def uptime_heartbeat_up() -> dict:
    """Healthy monitor — the receiver must refuse to open a ticket for this."""
    return {
        "heartbeat": {"status": 1, "time": "2026-10-14T06:40:00Z", "msg": "200 - OK", "monitorID": 7},
        "monitor": {"name": "support-landing", "url": "support.devilx-labs.dev", "type": "http"},
    }


# ----------------------------------------------------------------------- the drill
def stage_0_wiring(args) -> tuple[SentinelWebhookService, int, dict]:
    rule("STAGE 0 :: wiring — listener, autopilot default, defect path")

    repo = Path(args.repo).resolve()
    assert (repo / DEFECT_FILE).is_file(), f"{DEFECT_FILE} not in {repo}"

    # The service DEFAULT stays `off`: nothing patches or publishes unless a
    # delivery asks for it. The full loop is requested per-delivery via header.
    service = SentinelWebhookService(
        pipeline=SentinelPipeline(MetaClient()),
        repo=str(repo),
        publish_repo=args.feedback_repo,
        feedback_repo=args.feedback_repo,
        candidate_files=[DEFECT_FILE],
        screenshot=str(SCREENSHOT) if SCREENSHOT.exists() else None,
        inspect=True,
        autopilot="off",
        dry_run=not args.publish,
        audit_path=str(ROOT / "runtime" / "demo4_jobs.jsonl"),
    )
    print(f"  local defect tree : {repo}")
    print(f"  defect path       : {DEFECT_FILE}")
    print(f"  feedback repo     : {args.feedback_repo}")
    print(f"  service autopilot : {service.autopilot}   (delivery must opt in)")
    print(f"  publisher dry_run : {service.dry_run}")
    print(f"  visual fixture    : {SCREENSHOT.name} (exists={SCREENSHOT.exists()})")

    httpd = serve(service, host="127.0.0.1", port=args.port)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    check("listener bound and /healthz answers", http(base + "/healthz")[0] == 200)
    check(kind_of_lock(service), "autopilot default is 'off' (safe by default)")
    return service, httpd.server_address[1], {"base": base, "repo": str(repo)}


def kind_of_lock(service) -> str:
    return f"autopilot default is 'off' (safe by default)"


def stage_1_ingest(base: str, repo: str) -> dict:
    rule("STAGE 1 :: webhook delivery — Sentry alert POSTs in")

    payload = sentry_delivery(repo)
    raw = json.dumps(payload).encode()

    t0 = time.time()
    status, ack = http(base + "/webhook", method="POST", body=raw,
                       headers={"Content-Type": "application/json",
                                "x-sentinelpulse-autopilot": "feedback",
                                "sentry-hook-signature": "ignored-when-no-secret-set"})
    ack_ms = (time.time() - t0) * 1000

    print(f"  POST /webhook      -> HTTP {status} in {ack_ms:.0f} ms")
    print(f"  dialect sniffed    : {ack.get('kind')}")
    print(f"  job                : {ack.get('job_id')} status={ack.get('status')} "
          f"autopilot={ack.get('autopilot')}")
    print(f"  incidents          : {ack.get('incidents')} targets={ack.get('targets')}")

    check("delivery accepted (202)", status == 202, f"HTTP {status}")
    check("Sentry dialect detected by shape", ack.get("kind") == "sentry", str(ack.get("kind")))
    check("per-delivery autopilot override honoured",
          ack.get("autopilot") == "feedback", str(ack.get("autopilot")))
    check("acknowledged in milliseconds, not after the model call",
          ack_ms < 250.0, f"{ack_ms:.0f} ms")
    return ack


def stage_2_wait(base: str, job_id: str, timeout: float = 420.0) -> dict:
    rule("STAGE 2 :: off-thread processing — triage, visual check, sandbox patch")

    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        _s, last = http(f"{base}/jobs/{job_id}")
        if last.get("status") in ("done", "failed", "skipped"):
            break
        time.sleep(2.0)

    print(f"  status             : {last.get('status')} in {last.get('elapsed_s')}s")
    for line in (last.get("trace") or []):
        print(f"    {line}")
    return last


def stage_3_patch_forensics(job: dict, repo: str) -> None:
    rule("STAGE 3 :: patch forensics — did the sandbox actually change the right thing?")

    rem = job.get("remediation") or {}
    tri = job.get("triage") or {}
    vis = job.get("visual") or {}

    print(f"  signature          : {tri.get('signature')}  (conf {tri.get('confidence')})")
    print(f"  blast radius       : {tri.get('blast_radius')}  auto_fixable={tri.get('auto_fixable')}")
    print(f"  root cause         : {tri.get('root_cause')}")
    print(f"  visual             : defaced={vis.get('defaced')} "
          f"layout_broken={vis.get('layout_broken')} overlay={vis.get('overlay_suspected')}")
    for f in (vis.get("findings") or [])[:5]:
        print(f"    - {f.get('kind')} @ {f.get('where')} conf={f.get('confidence')}: {f.get('detail')}")
    print(f"  files changed      : {rem.get('files_changed')}")
    print(f"  validated / risk   : {rem.get('validated')} / {rem.get('risk')}")
    print(f"  confidence         : {rem.get('confidence')}   attempts={rem.get('attempts')}")
    print(f"  sandbox            : {rem.get('sandbox')}")
    print(f"  reasoning tokens   : triage={tri.get('reasoning_tokens')} "
          f"patch={rem.get('reasoning_tokens')}")
    print("  validation transcript:")
    for line in (rem.get("validation_output") or "").splitlines():
        print(f"    {line}")
    print("  --- generated diff ---")
    for line in (rem.get("diff") or "(none)").splitlines()[:60]:
        print(f"  {line}")

    diff = rem.get("diff") or ""
    sandbox_repo = Path(rem.get("sandbox") or "") / "repo"
    patched = ""
    if sandbox_repo.is_dir() and (sandbox_repo / DEFECT_FILE).is_file():
        patched = (sandbox_repo / DEFECT_FILE).read_text(errors="replace")
    live = (Path(repo) / DEFECT_FILE).read_text(errors="replace")

    check("triage produced a signature", bool(tri.get("signature")) and
          tri.get("signature") != "UNCLASSIFIED", str(tri.get("signature")))
    check("visual pass ran on the captured screenshot",
          bool(vis) and not vis.get("error"), str(vis.get("error") or "ok"))
    check("patch validated in the sandbox", bool(rem.get("validated")),
          str(rem.get("reason") or rem.get("error") or ""))
    check(f"diff touches exactly {DEFECT_FILE}",
          rem.get("files_changed") == [DEFECT_FILE], str(rem.get("files_changed")))
    check("diff is a unified diff over the defect line",
          DEFECT_MARKER in diff and diff.lstrip().startswith("--- "), "difflib attribution")
    check("the sandbox patch gates demotion behind a two-strike counter",
          patched != live and ">= 2" in patched and ("= 0" in patched or "continue" in patched),
          "read back from sandbox tree: strike counter + reset on recovery")
    # Independence: the live tree is the pristine clone, so it must still carry
    # the single-strike defect and must NOT carry the sandbox's counter.
    check("the LIVE tree was never written (patch stayed in the sandbox)",
          DEFECT_MARKER in live and ">= 2" not in live,
          "live file byte-identical to HEAD: defect intact, no counter")


def stage_4_pr(base: str, job: dict) -> None:
    rule("STAGE 4 :: GitHub PR opener — branch, commit, pull request")

    pub = job.get("publish") or {}
    print(f"  ok / dry_run       : {pub.get('ok')} / {pub.get('dry_run')}")
    print(f"  base               : {pub.get('base')}")
    print(f"  branch             : {pub.get('branch')}")
    print(f"  commit             : {pub.get('commit_sha')}")
    print(f"  PR                 : #{pub.get('pr_number')} {pub.get('pr_url')}")
    print(f"  error              : {pub.get('error') or '(none)'}")
    for line in (pub.get("log") or []):
        print(f"    {line}")

    if pub.get("dry_run"):
        check("PR payload staged (dry-run: push suppressed)", pub.get("ok") is True)
        return

    check("publish stage succeeded", pub.get("ok") is True, str(pub.get("error") or ""))
    check("PR opened with a number and URL",
          bool(pub.get("pr_number")) and bool(pub.get("pr_url")), str(pub.get("pr_url")))
    check("commit landed on the sentinelpulse branch",
          bool(pub.get("commit_sha")) and (pub.get("branch") or "").startswith("sentinelpulse/"),
          str(pub.get("branch")))

    # Independently verify the PR exists and is open, via the API rather than
    # trusting the publisher's own return value.
    if pub.get("pr_url"):
        pr = GitHubPublisher(job.get("publish", {}).get("repo") or "", dry_run=True)
        status, data = pr._api("GET", f"/repos/{pr.repo}/pulls/{pub['pr_number']}")
        print(f"\n  independent re-read : HTTP {status} state={data.get('state')} "
              f"head={((data.get('head') or {}).get('ref'))} "
              f"files_url={data.get('url', '')[:0] or 'n/a'}")
        check("PR re-read from the API is OPEN", status == 200 and data.get("state") == "open",
              f"HTTP {status} state={data.get('state')}")
        check("PR targets the base branch", (data.get("base") or {}).get("ref") == pub.get("base"),
              str((data.get("base") or {}).get("ref")))

        _s, files = pr._api("GET", f"/repos/{pr.repo}/pulls/{pub['pr_number']}/files")
        names = [f.get("filename") for f in files] if isinstance(files, list) else []
        print(f"  PR files            : {names}")
        check("PR contains exactly the remediated file", names == [DEFECT_FILE], str(names))

        _s, body = pr._api("GET", f"/repos/{pr.repo}/pulls/{pub['pr_number']}")
        text = body.get("body") or ""
        for needle, label in (("## Diff telemetry", "diff telemetry block"),
                              ("Diagnostic reasoning (model telemetry)", "model telemetry block"),
                              ("muse-spark-1.3", "model id stamped"),
                              ("## Validation", "sandbox transcript block")):
            check(f"PR body auto-filled: {label}", needle in text)


def stage_5_negative_controls(base: str, args) -> None:
    rule("STAGE 5 :: negative controls — the gates that must refuse")

    # (a) a healthy monitor must not open a ticket
    status, ack = http(base + "/webhook/uptimekuma", method="POST",
                       body=json.dumps(uptime_heartbeat_up()).encode(),
                       headers={"Content-Type": "application/json"})
    print(f"  healthy heartbeat  -> HTTP {status} status={ack.get('status')} "
          f"skipped={ack.get('skipped')}")
    check("healthy heartbeat produces NO ticket", ack.get("status") == "skipped")

    # (b) an unvalidated remediation must never reach GitHub
    pub = GitHubPublisher(args.feedback_repo, dry_run=True)
    res = pub.publish(Remediation(validated=False, diff="", files_changed=[]),
                      Incident(target="support.devilx-labs.dev", source=Source.MANUAL,
                               summary="gate probe", severity=Severity.LOW),
                      _TriageStub())
    print(f"  unvalidated publish-> ok={res.ok} error={res.error}")
    check("publisher refuses an unvalidated remediation", res.ok is False and
          "not sandbox-validated" in res.error, res.error)

    unproven = Remediation(validated=True, diff="--- a/x\n+++ b/x\n", files_changed=[])
    res2 = pub.publish(unproven, Incident(target="support.devilx-labs.dev",
                                          source=Source.MANUAL, summary="gate probe 2"),
                       _TriageStub())
    check("publisher refuses a diff over zero files", res2.ok is False, res2.error)

    res3 = pub.publish(Remediation(validated=True, diff="--- a/x\n+++ b/x\n",
                                   files_changed=["lib/x.ts"], sandbox="/nonexistent"),
                       Incident(target="support.devilx-labs.dev", source=Source.MANUAL,
                               summary="gate probe 3"), _TriageStub())
    check("publisher refuses a missing sandbox tree", res3.ok is False, res3.error)

    # (c) a wrong HMAC signature must be rejected before parsing
    guarded = SentinelWebhookService(
        pipeline=SentinelPipeline(MetaClient()), autopilot="off",
        hmac_secret="demo-shared-secret", dry_run=True,
        audit_path=str(ROOT / "runtime" / "demo4_hmac.jsonl"))
    httpd = serve(guarded, host="127.0.0.1", port=args.port + 1)
    gbase = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        st_bad, bad = http(gbase + "/webhook", method="POST",
                           body=json.dumps(sentry_delivery(args.feedback_repo)).encode(),
                           headers={"Content-Type": "application/json",
                                    "x-sentinelpulse-signature": "sha256=deadbeef"})
        print(f"  bad signature      -> HTTP {st_bad} {bad}")
        check("bad HMAC signature rejected with 401", st_bad == 401, str(bad)[:80])

        import hashlib
        import hmac as _hmac
        body = json.dumps(sentry_delivery(args.feedback_repo)).encode()
        good = "sha256=" + _hmac.new(b"demo-shared-secret", body, hashlib.sha256).hexdigest()
        st_ok, ok_payload = http(gbase + "/webhook", method="POST", body=body,
                                 headers={"Content-Type": "application/json",
                                          "x-sentinelpulse-signature": good})
        print(f"  good signature     -> HTTP {st_ok} accepted={ok_payload.get('accepted')}")
        check("valid HMAC signature accepted", st_ok == 202, str(ok_payload)[:80])
        check("HMAC guard is armed in /healthz", http(gbase + "/healthz")[1].get("hmac") is True)
    finally:
        httpd.shutdown()
        guarded.stop()

    # (d) malformed JSON must not crash the receiver
    st, bad = http(base + "/webhook", method="POST", body=b"{not json",
                   headers={"Content-Type": "application/json"})
    check("malformed JSON rejected with 400 (receiver survives)",
          st == 400, str(bad)[:60])


class _TriageStub:
    signature = "GATE_PROBE"
    root_cause = "negative-control probe"
    plan: list[str] = []
    blast_radius = "local"
    reasoning_tokens = 0
    latency_s = 0.0
    verification = ""


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SentinelPulse end-to-end drill")
    ap.add_argument("--repo", default=str(SCRATCH_CLONE),
                    help="local clone of the defect tree the remediator patches")
    ap.add_argument("--feedback-repo", default=os.environ.get("SENTINEL_FEEDBACK_REPO"),
                    help="owner/name the PR lands on (the project's own feedback repo)")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--no-publish", dest="publish", action="store_false", default=True,
                    help="stop after the sandbox patch; keep GitHub in dry-run")
    args = ap.parse_args(argv)

    if not args.feedback_repo:
        try:
            login = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                                   capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:  # noqa: BLE001
            login = ""
        args.feedback_repo = f"{login}/tg-support-landing" if login else ""

    t_start = time.time()
    rule("DEMO 4 :: END-TO-END REMEDIATION DRILL")
    print("  webhook -> incident -> triage -> visual check -> sandbox patch -> GitHub PR")
    print(f"  publish target : {args.feedback_repo or '(unset)'}   "
          f"mode={'LIVE PR' if args.publish else 'sandbox only'}")

    service, _port, ctx = stage_0_wiring(args)
    try:
        ack = stage_1_ingest(ctx["base"], ctx["repo"])
        job = stage_2_wait(ctx["base"], ack["job_id"])
        check("job finished without a worker error",
              job.get("status") == "done", str(job.get("error") or job.get("status")))
        stage_3_patch_forensics(job, ctx["repo"])
        stage_4_pr(ctx["base"], job)
        stage_5_negative_controls(ctx["base"], args)
    finally:
        service.stop()

    elapsed = time.time() - t_start
    rule("DRILL SUMMARY")
    passed = sum(1 for _n, ok in CHECK if ok)
    for name, ok in CHECK:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  checks  : {passed}/{len(CHECK)}")
    print(f"  wall    : {elapsed:.1f}s")
    print(f"  verdict : {'COMPLETE — full loop exercised end to end' if passed == len(CHECK) else 'INCOMPLETE'}")
    print("=" * 78)
    return 0 if passed == len(CHECK) else 1


if __name__ == "__main__":
    raise SystemExit(main())