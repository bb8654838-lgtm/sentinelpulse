#!/usr/bin/env python3
"""
SentinelPulse :: webhook intake receiver  (app/webhook/receiver.py)

The front door. Sentry / Prometheus Alertmanager / Uptime Kuma / Grafana /
PagerDuty POST here; a delivery is acknowledged in milliseconds and processed
off the request thread, because a receiver that blocks on a model call is a
receiver that starts dropping alerts under load.

    POST /webhook            -> sniff the dialect
    POST /webhook/<source>   -> dialect forced (sentry|alertmanager|uptimekuma|...)
    GET  /healthz            -> readiness + queue depth
    GET  /jobs               -> recent job records
    GET  /jobs/<job_id>      -> one job record (incident, triage, patch, PR)

Autopilot levels (`--autopilot`)
    off     triage only — incident + reasoning, no code changes
    patch   triage + sandbox patch, GitHub publish stays a DRY-RUN
    feedback  triage + sandbox patch + a real PR against the project's FEEDBACK
            repo (`--file` names the defect path, `--feedback-repo` the landing
            repo) — the inspectable artifact, without touching a stranger's repo
    live    triage + sandbox patch + real branch, commit and pull request

Live publishing is opt-in on purpose. Nothing opens a PR unless the operator
asked for `live`, and even then only for a sandbox-VALIDATED diff.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.incident import Incident                      # noqa: E402
from app.core.pipeline import SentinelPipeline              # noqa: E402
from app.webhook.normalizers import normalize, verify_hmac  # noqa: E402

DEFAULT_PORT = 8787
MAX_BODY = 2 * 1024 * 1024          # 2 MiB — a webhook larger than this is not a webhook
AUTOPILOT_LEVELS = ("off", "patch", "feedback", "live")


# ------------------------------------------------------------------------ models
@dataclass
class Job:
    """One webhook delivery, end to end."""

    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: str = "generic"
    status: str = "accepted"       # accepted|running|done|skipped|failed
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    autopilot: str = "off"
    incidents: list[dict] = field(default_factory=list)
    triage: dict = field(default_factory=dict)
    visual: dict | None = None
    remediation: dict | None = None
    publish: dict | None = None
    skipped: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.created_at, 2)

    def note(self, msg: str) -> None:
        self.trace.append(f"{round(time.time() - self.created_at, 2):>6.2f}s  {msg}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["elapsed_s"] = self.elapsed
        return d

    def brief(self) -> str:
        pr = ""
        if self.publish and self.publish.get("ok") and self.publish.get("pr_url"):
            pr = f" PR={self.publish['pr_url']}"
        elif self.publish and self.publish.get("dry_run"):
            pr = " PR=dry-run"
        return f"[{self.status:>7}] {self.job_id} kind={self.kind} inc={len(self.incidents)}{pr}"


class JobStore:
    """Bounded in-memory store + append-only JSONL audit trail."""

    def __init__(self, limit: int = 200, audit_path: str | Path | None = None):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._limit = limit
        self._lock = threading.Lock()
        self.audit_path = Path(audit_path) if audit_path else None
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def put(self, job: Job) -> Job:
        with self._lock:
            if job.job_id not in self._jobs:
                self._order.append(job.job_id)
            self._jobs[job.job_id] = job
            while len(self._order) > self._limit:
                self._jobs.pop(self._order.pop(0), None)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, n: int = 20) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in self._order[-n:] if i in self._jobs]

    def persist(self, job: Job) -> None:
        if not self.audit_path:
            return
        try:
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(job.to_dict(), default=str) + "\n")
        except OSError:
            pass  # an audit write must never break ingestion


# ---------------------------------------------------------------------- service
class SentinelWebhookService:
    """Normalize -> triage -> (inspect) -> (patch) -> (publish), off-thread."""

    def __init__(self, *, pipeline: SentinelPipeline | None = None,
                 repo: str | None = None, publish_repo: str | None = None,
                 autopilot: str = "off", screenshot: str | None = None,
                 inspect: bool = True, workers: int = 2,
                 hmac_secret: str | None = None, dry_run: bool = True,
                 audit_path: str | Path | None = None, publisher_factory=None,
                 candidate_files: list[str] | None = None,
                 feedback_repo: str | None = None):
        if autopilot not in AUTOPILOT_LEVELS:
            raise ValueError(f"autopilot must be one of {AUTOPILOT_LEVELS}, got {autopilot!r}")
        self.pipeline = pipeline or SentinelPipeline()
        self.repo = repo                       # local tree the remediator patches
        self.publish_repo = publish_repo       # owner/name the PR lands on
        self.candidate_files = list(candidate_files or [])  # defect path named up-front
        self.feedback_repo = (feedback_repo or publish_repo)  # where `feedback` lands
        self.autopilot = autopilot
        self.screenshot = screenshot
        self.inspect = inspect
        self.hmac_secret = hmac_secret or ""
        self.dry_run = dry_run
        self.store = JobStore(audit_path=audit_path)
        self._q: queue.Queue[tuple[str, dict, str]] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._stop = threading.Event()
        self._n_workers = max(1, workers)
        self._publisher_factory = publisher_factory or self._default_publisher_factory

    # ------------------------------------------------------------------ plumbing
    def _default_publisher_factory(self, repo: str | None = None):
        from app.remediator.gh_publisher import GitHubPublisher
        target = repo or self.publish_repo
        if not target:
            raise ValueError("no publish repo configured (--publish-repo / --feedback-repo)")
        return GitHubPublisher(target, dry_run=self.dry_run)

    def start(self) -> None:
        for i in range(self._n_workers):
            t = threading.Thread(target=self._loop, name=f"sentinel-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self) -> None:
        self._stop.set()
        for _ in self._workers:
            self._q.put(("", {}, ""))          # unblock a parked consumer
        for t in self._workers:
            t.join(timeout=5)

    def submit(self, payload: dict, *, hint: str | None = None,
               headers: dict | None = None, autopilot: str | None = None) -> Job:
        """Accept a delivery, hand it to a worker, return the pending job at once."""
        batch = normalize(payload, hint=hint, headers=headers)
        job = Job(kind=batch.kind, skipped=list(batch.skipped),
                  autopilot=autopilot or self.autopilot)
        job.incidents = [i.to_dict() for i in batch.incidents]
        job.note(f"normalized kind={batch.kind} incidents={batch.count} skipped={len(batch.skipped)}")
        for s in batch.skipped:
            job.note(f"skip: {s}")
        self.store.put(job)
        if not batch.incidents:
            job.status = "skipped"
            job.finished_at = time.time()
            job.note("nothing actionable in this delivery")
            self.store.persist(job)
            return job
        self._q.put((job.job_id, payload, batch.kind))
        # stash the parsed incidents so the worker does not re-normalize
        self._pending[batch.kind, job.job_id] = batch.incidents
        return job

    _pending: dict[tuple[str, str], list[Incident]] = {}

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id, payload, kind = self._q.get()
            if not job_id:
                continue
            try:
                self._process(job_id, payload, kind)
            except Exception as exc:  # noqa: BLE001 — a worker must never die
                job = self.store.get(job_id)
                if job:
                    job.status = "failed"
                    job.error = f"{type(exc).__name__}: {exc}"
                    job.finished_at = time.time()
                    job.note(f"worker error: {job.error}")
                    self.store.persist(job)

    # ---------------------------------------------------------------- processing
    def _process(self, job_id: str, payload: dict, kind: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.note("worker picked up the job")

        incidents = self._pending.pop((kind, job_id), None) or normalize(payload, hint=kind).incidents
        if not incidents:
            job.status = "skipped"
            job.finished_at = time.time()
            self.store.persist(job)
            return

        # The job tracks ONE primary incident end-to-end; the rest stay recorded.
        inc = incidents[0]
        job.note(f"primary incident {inc.incident_id} target={inc.target} severity={inc.severity.value}")

        screenshot = self.screenshot
        want_visual = self.inspect and bool(screenshot)
        want_patch = job.autopilot in ("patch", "feedback", "live") and bool(self.repo)
        # `feedback` lands on the project's own feedback repo (never a stranger's);
        # `live` lands on --publish-repo. Both require a validated diff.
        publish_target = (self.feedback_repo if job.autopilot == "feedback"
                          else self.publish_repo)
        want_publish = job.autopilot in ("feedback", "live") and bool(publish_target)

        if want_visual:
            job.note(f"visual stage: inspecting {Path(screenshot).name}")

        verdict = self.pipeline.run(
            inc,
            inspect=want_visual,
            screenshot=str(screenshot) if want_visual else None,
            remediate=want_patch,
            repo=self.repo if want_patch else None,
            candidate_files=self.candidate_files or None,
        )
        job.triage = verdict.triage
        job.visual = verdict.visual
        job.note(f"triage signature={verdict.triage.get('signature')} "
                 f"confidence={verdict.triage.get('confidence')} "
                 f"reasoning_tokens={verdict.triage.get('reasoning_tokens')}")
        if verdict.triage.get("error"):
            job.note(f"triage error: {verdict.triage['error']}")

        if verdict.visual and not verdict.visual.get("error"):
            job.note(f"visual: defaced={verdict.visual.get('defaced')} "
                     f"layout_broken={verdict.visual.get('layout_broken')} "
                     f"findings={len(verdict.visual.get('findings') or [])}")

        job.remediation = verdict.remediation
        if job.remediation and job.remediation.get("validated"):
            job.note(f"patch validated: {job.remediation.get('files_changed')} "
                     f"attempts={job.remediation.get('attempts')}")
        elif job.remediation:
            job.note(f"patch NOT validated: {job.remediation.get('reason') or job.remediation.get('error')}")

        if want_publish:
            job.note(f"publishing (LIVE -> {publish_target}): branch + commit + pull request")
            from app.remediator.patchgen import Remediation

            rem = Remediation(**{k: v for k, v in (job.remediation or {}).items()
                                 if k in Remediation.__dataclass_fields__})
            pub = self._publisher_factory(publish_target)
            result = pub.publish(rem, inc, _TriageView(verdict.triage),
                                 verdict={"visual": verdict.visual})
            job.publish = result.to_dict()
            job.note(f"publish: {result.summary_line()}")
        elif want_patch and job.remediation:
            job.note(f"publish held (autopilot={job.autopilot}) — diff is sandbox-only")

        job.status = "done"
        job.finished_at = time.time()
        job.note(f"job complete in {job.elapsed}s")
        self.store.persist(job)

    # ------------------------------------------------------------- sync helper
    def process_now(self, payload: dict, *, hint: str | None = None,
                    headers: dict | None = None, autopilot: str | None = None) -> Job:
        """Ingest + process synchronously (demos, tests, replay)."""
        batch = normalize(payload, hint=hint, headers=headers)
        job = Job(kind=batch.kind, skipped=list(batch.skipped),
                  autopilot=autopilot or self.autopilot)
        job.incidents = [i.to_dict() for i in batch.incidents]
        job.note(f"normalized kind={batch.kind} incidents={batch.count}")
        if not batch.incidents:
            job.status = "skipped"
            job.finished_at = time.time()
            job.note("nothing actionable in this delivery")
            return job
        self.store.put(job)
        self._pending[(batch.kind, job.job_id)] = batch.incidents
        self._process(job.job_id, payload, batch.kind)
        return self.store.get(job.job_id) or job


class _TriageView:
    """Attribute view over the triage dict, for the publisher's template."""

    def __init__(self, d: dict):
        self._d = d or {}

    def __getattr__(self, item: str):
        v = self._d.get(item)
        return "" if v is None else v


# ---------------------------------------------------------------------- transport
def make_handler(service: SentinelWebhookService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SentinelPulse/0.2"
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------------- utilities
        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, indent=2, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):   # quiet by default; the store is the log
            if os.environ.get("SENTINEL_WEBHOOK_VERBOSE"):
                super().log_message(fmt, *args)

        def _headers(self) -> dict:
            return {k.lower(): v for k, v in self.headers.items()}

        def _read_body(self) -> bytes:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > MAX_BODY:
                raise ValueError(f"body too large: {n} bytes (cap {MAX_BODY})")
            return self.rfile.read(n) if n else b""

        # ------------------------------------------------------------------ verbs
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path in ("/", "/healthz"):
                self._send(200, {
                    "ok": True, "service": "sentinelpulse-webhook",
                    "autopilot": service.autopilot,
                    "publish_repo": service.publish_repo or None,
                    "feedback_repo": service.feedback_repo or None,
                    "candidate_files": service.candidate_files or None,
                    "dry_run": service.dry_run,
                    "queued": service._q.qsize(),
                    "workers": service._n_workers,
                    "hmac": bool(service.hmac_secret),
                    "uptime_s": round(time.time() - _STARTED, 1),
                })
            elif path == "/jobs":
                self._send(200, {"jobs": [j.brief() for j in service.store.recent(25)]})
            elif path.startswith("/jobs/"):
                job = service.store.get(path.split("/", 2)[2])
                self._send(200, job.to_dict()) if job else self._send(404, {"error": "no such job"})
            else:
                self._send(404, {"error": "not found", "routes": [
                    "POST /webhook", "POST /webhook/<source>", "GET /healthz",
                    "GET /jobs", "GET /jobs/<id>"]})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/")
            if not path.startswith("/webhook"):
                self._send(404, {"error": "not found in this lane",
                                 "hint": "webhook deliveries belong on /webhook"})
                return
            hint = path.split("/", 3)[2] if path.count("/") >= 2 else None

            try:
                raw = self._read_body()
            except ValueError as exc:
                self._send(413, {"error": str(exc)})
                return

            headers = self._headers()
            if service.hmac_secret:
                sig = (headers.get("x-sentinelpulse-signature")
                       or headers.get("sentry-hook-signature")
                       or headers.get("x-hub-signature-256"))
                if not verify_hmac(service.hmac_secret, raw, sig):
                    self._send(401, {"error": "signature verification failed"})
                    return

            try:
                payload = json.loads(raw.decode(errors="replace") or "{}")
            except json.JSONDecodeError as exc:
                self._send(400, {"error": f"invalid JSON: {exc}"})
                return

            job = service.submit(payload, hint=hint, headers=headers,
                                 autopilot=headers.get("x-sentinelpulse-autopilot"))
            code = 202 if job.status == "accepted" else 200
            self._send(code, {
                "accepted": job.status == "accepted",
                "job_id": job.job_id,
                "kind": job.kind,
                "incidents": len(job.incidents),
                "status": job.status,
                "skipped": job.skipped,
                "autopilot": job.autopilot,
                "poll": f"/jobs/{job.job_id}",
                "targets": [i.get("target") for i in job.incidents],
            })

    return Handler


_STARTED = time.time()


def serve(service: SentinelWebhookService, *, host: str = "127.0.0.1",
          port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Start the receiver (non-blocking). Returns the live server object."""
    service.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(service))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="sentinel-http", daemon=True).start()
    return httpd


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="SentinelPulse webhook receiver")
    ap.add_argument("--host", default=os.environ.get("SENTINEL_WEBHOOK_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("SENTINEL_WEBHOOK_PORT", DEFAULT_PORT)))
    ap.add_argument("--repo", default=os.environ.get("SENTINEL_LOCAL_REPO"),
                    help="local tree the remediator patches")
    ap.add_argument("--publish-repo", default=os.environ.get("SENTINEL_PUBLISH_REPO"),
                    help="owner/name the pull request lands on (autopilot=live)")
    ap.add_argument("--feedback-repo", default=os.environ.get("SENTINEL_FEEDBACK_REPO"),
                    help="owner/name the pull request lands on (autopilot=feedback)")
    ap.add_argument("--file", action="append", default=None, metavar="RELPATH",
                    help="defect path handed to the remediator (repeatable)")
    ap.add_argument("--autopilot", default=os.environ.get("SENTINEL_AUTOPILOT", "off"),
                    choices=AUTOPILOT_LEVELS)
    ap.add_argument("--screenshot", default=os.environ.get("SENTINEL_SCREENSHOT"))
    ap.add_argument("--no-inspect", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--hmac-secret", default=os.environ.get("SENTINEL_HMAC_SECRET"))
    ap.add_argument("--live", action="store_true",
                    help="with --autopilot live: actually open the pull request")
    ap.add_argument("--audit", default=str(ROOT / "runtime" / "webhook_jobs.jsonl"))
    a = ap.parse_args(argv)

    service = SentinelWebhookService(
        repo=a.repo, publish_repo=a.publish_repo, autopilot=a.autopilot,
        screenshot=a.screenshot, inspect=not a.no_inspect, workers=a.workers,
        hmac_secret=a.hmac_secret, dry_run=not a.live, audit_path=a.audit,
        candidate_files=a.file, feedback_repo=a.feedback_repo)
    httpd = serve(service, host=a.host, port=a.port)
    print(json.dumps({
        "listening": f"http://{a.host}:{a.port}",
        "autopilot": service.autopilot,
        "local_repo": service.repo,
        "publish_repo": service.publish_repo,
        "feedback_repo": service.feedback_repo,
        "candidate_files": service.candidate_files,
        "dry_run": service.dry_run,
        "audit": str(service.store.audit_path),
        "routes": ["POST /webhook", "POST /webhook/<source>", "GET /healthz",
                   "GET /jobs", "GET /jobs/<id>"],
    }, indent=2))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.shutdown()
        service.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())