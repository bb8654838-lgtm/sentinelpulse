#!/usr/bin/env python3
"""
SentinelPulse :: webhook normalizers  (app/webhook/normalizers.py)

Alert sources all speak different dialects. This module is the only place that
knows those dialects; everything downstream sees a single `Incident`.

Supported dialects (detected from the payload SHAPE, not just the URL hint):

  sentry        Sentry issue-alert webhook      {"action":..,"data":{"event":{..}}}
  alertmanager  Prometheus Alertmanager v4      {"status":"firing","alerts":[{..}]}
  uptimekuma    Uptime Kuma heartbeat           {"heartbeat":{"status":0},..}
  grafana       Grafana unified alerting        {"state":"alerting","ruleName":..}
  pagerduty     PagerDuty v3 webhook            {"event":{"event_type":..}}
  sentinelpulse native shape — post an Incident straight in
  generic       anything else: pull URLs/hosts/severity out of the text

Design rules
------------
* Normalizers only TRANSLATE. They never conclude a root cause; that is the
  reasoning core's job (app/core/pipeline.py).
* A payload that resolves to a healthy/benign signal yields ZERO incidents,
  so uptime monitors posting recovery beats do not open tickets.
* Nothing here raises on a malformed payload. A receiver that 500s on bad
  input is a receiver that loses alerts.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Any

try:
    from app.core.incident import Incident, Severity, Source
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.incident import Incident, Severity, Source

# --------------------------------------------------------------------- patterns
URL_RE = re.compile(r"https?://[^\s'\"<>)\]},]+")
HOST_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|io|dev|ai|co|xyz|app|sh|cloud|internal|local|invalid|test)\b",
    re.I,
)
STATUS_RE = re.compile(r"\b(5\d{2}|4\d{2})\b")

SEVERITY_WORDS: dict[str, Severity] = {
    "critical": Severity.CRITICAL, "fatal": Severity.CRITICAL,
    "emergency": Severity.CRITICAL, "sev1": Severity.CRITICAL, "p1": Severity.CRITICAL,
    "high": Severity.HIGH, "error": Severity.HIGH, "major": Severity.HIGH,
    "alerting": Severity.HIGH, "page": Severity.HIGH, "sev2": Severity.HIGH, "p2": Severity.HIGH,
    "medium": Severity.MEDIUM, "warning": Severity.MEDIUM, "warn": Severity.MEDIUM,
    "alert": Severity.MEDIUM, "sev3": Severity.MEDIUM, "p3": Severity.MEDIUM,
    "low": Severity.LOW, "minor": Severity.LOW, "notice": Severity.LOW,
    "info": Severity.INFO, "informational": Severity.INFO, "debug": Severity.INFO,
}

_SOURCE_BY_NAME = {
    "dns": Source.DNS,
    "http": Source.HTTP,
    "visual": Source.VISUAL,
    "asset": Source.ASSET,
    "manual": Source.MANUAL,
}


@dataclass
class NormalizedBatch:
    """Result of translating one webhook delivery."""

    kind: str = "generic"
    incidents: list[Incident] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # benign beats, explained
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.incidents)


# ----------------------------------------------------------------- small helpers
def severity_of(value: Any, default: Severity = Severity.MEDIUM) -> Severity:
    """Map any provider spelling onto our Severity enum."""
    if isinstance(value, Severity):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        # Uptime Kuma / Sentry numeric levels
        return {0: Severity.CRITICAL, 1: Severity.HIGH, 2: Severity.MEDIUM,
                3: Severity.LOW, 4: Severity.INFO, 5: Severity.INFO}.get(value, default)
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in SEVERITY_WORDS:
        return SEVERITY_WORDS[text]
    for word, sev in SEVERITY_WORDS.items():
        if word in text:
            return sev
    return default


def first_url(*texts: Any) -> str:
    for t in texts:
        if not t:
            continue
        if isinstance(t, str):
            m = URL_RE.search(t)
            if m:
                return m.group(0).rstrip(".,;")
        elif isinstance(t, dict):
            got = first_url(*t.values())
            if got:
                return got
    return ""


def first_host(*texts: Any) -> str:
    for t in texts:
        if isinstance(t, str) and t:
            m = HOST_RE.search(t)
            if m:
                return m.group(0)
    return ""


def _target_from(*texts: Any, fallback: str = "unknown-target") -> str:
    return first_url(*texts) or first_host(*texts) or fallback


def _status_from(*texts: Any) -> int | None:
    for t in texts:
        if isinstance(t, str) and t:
            m = STATUS_RE.search(t)
            if m:
                return int(m.group(1))
    return None


def _source_from_text(text: str) -> Source:
    low = (text or "").lower()
    if any(k in low for k in ("nxdomain", "servfail", "dns", "resolver", "no-record")):
        return Source.DNS
    if any(k in low for k in ("screenshot", "layout", "deface", "overlay", "visual", "clickjack")):
        return Source.VISUAL
    if any(k in low for k in ("asset", "image", "404", "missing file", "stylesheet")):
        return Source.ASSET
    return Source.HTTP


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(n in haystack for n in needles)


# --------------------------------------------------------------------- dialects
def from_sentry(payload: dict, meta: dict) -> NormalizedBatch:
    """Sentry issue-alert webhook."""
    batch = NormalizedBatch(kind="sentry", meta=meta)
    action = str(payload.get("action", "created")).lower()
    event = ((payload.get("data") or {}).get("event") or {})
    if not event:
        batch.skipped.append(f"sentry payload without data.event (action={action})")
        return batch

    title = str(event.get("title") or "")
    culprit = str(event.get("culprit") or "")
    md = event.get("metadata") or {}
    err_type = str(md.get("type") or "")
    err_value = str(md.get("value") or "")
    web_url = str(event.get("web_url") or "")
    level = event.get("level", "error")
    req = event.get("request") or {}
    req_url = str(req.get("url") or "") if isinstance(req, dict) else ""
    tags = event.get("tags") or {}
    environment = tags.get("environment", "") if isinstance(tags, dict) else ""

    text_blob = " ".join([title, culprit, err_type, err_value, req_url])
    target = _target_from(req_url, culprit, title, fallback=culprit or title or "sentry-event")
    status = _status_from(err_value, title)

    sev = severity_of(level, Severity.HIGH)
    if status and status >= 500:
        sev = Severity.CRITICAL

    inc = Incident(
        target=target,
        source=_source_from_text(text_blob),
        summary=(title or f"{err_type}: {err_value}" or "Sentry event").strip()[:300],
        severity=sev,
    )
    inc.add("webhook", f"sentry delivery action={action}",
            provider="sentry", action=action, issue_url=web_url, environment=environment)
    inc.add("log", (f"{err_type}: {err_value}".strip(": ") or title or "no message")[:400],
            culprit=culprit, level=level)
    if status:
        inc.add("http", f"status observed in event text: {status}", status=status)
    inc.meta.update(provider="sentry", action=action, fingerprint=event.get("fingerprint"))
    batch.incidents.append(inc)
    return batch


def from_alertmanager(payload: dict, meta: dict) -> NormalizedBatch:
    """Prometheus Alertmanager v4 webhook. One alert -> one incident."""
    batch = NormalizedBatch(kind="alertmanager", meta=meta)
    alerts = payload.get("alerts") or []
    if not isinstance(alerts, list) or not alerts:
        batch.skipped.append("alertmanager payload with no alerts array")
        return batch

    for a in alerts:
        if not isinstance(a, dict):
            continue
        status = str(a.get("status", payload.get("status", "firing"))).lower()
        labels = a.get("labels") or {}
        annot = a.get("annotations") or {}
        alertname = str(labels.get("alertname") or "alert")
        instance = str(labels.get("instance") or labels.get("pod") or labels.get("service") or "")
        sev_word = str(labels.get("severity") or "warning")
        summary = str(annot.get("summary") or alertname)
        desc = str(annot.get("description") or "")

        if status == "resolved":
            batch.skipped.append(f"{alertname}/{instance or '-'} resolved — no ticket")
            continue

        sev = severity_of(sev_word, Severity.MEDIUM)
        if "down" in alertname.lower() or "unavailable" in alertname.lower():
            sev = Severity.CRITICAL if sev is Severity.MEDIUM else sev

        target = _target_from(instance, desc, summary,
                              fallback=f"{alertname}@{instance}" if instance else alertname)
        inc = Incident(
            target=target,
            source=_source_from_text(" ".join([alertname, summary, desc])),
            summary=f"{alertname}: {summary}"[:300],
            severity=sev,
        )
        inc.add("webhook", f"alertmanager {status}",
                provider="alertmanager", alertname=alertname, status=status,
                generator_url=a.get("generatorURL", ""))
        inc.add("log", desc[:400] or summary,
                labels={k: str(v) for k, v in labels.items()},
                starts_at=a.get("startsAt", ""))
        status_code = _status_from(desc, summary)
        if status_code:
            inc.add("http", f"status {status_code} named in alert", status=status_code)
        inc.meta.update(provider="alertmanager", alertname=alertname, status=status,
                        fingerprint=a.get("fingerprint"))
        batch.incidents.append(inc)
    return batch


def from_uptimekuma(payload: dict, meta: dict) -> NormalizedBatch:
    """Uptime Kuma heartbeat. status 0=down, 1=up, 2=pending, 3=maintenance."""
    batch = NormalizedBatch(kind="uptimekuma", meta=meta)
    hb = payload.get("heartbeat") or {}
    mon = payload.get("monitor") or {}
    if not hb:
        batch.skipped.append("uptime-kuma payload without heartbeat")
        return batch

    raw_status = hb.get("status", 1)
    try:
        status_code = int(raw_status)
    except (TypeError, ValueError):
        status_code = 1
    name = str(mon.get("name") or "monitor")
    url = str(mon.get("url") or "")
    msg = str(hb.get("msg") or "")

    if status_code == 1:
        batch.skipped.append(f"{name} heartbeat UP — no ticket")
        return batch
    if status_code == 3:
        batch.skipped.append(f"{name} in maintenance window — no ticket")
        return batch

    http_status = _status_from(msg, name)
    sev = Severity.CRITICAL if status_code == 0 else Severity.MEDIUM
    inc = Incident(
        target=url or name,
        source=_source_from_text(msg) if msg else Source.HTTP,
        summary=f"{name} is not responding ({msg or 'no message'})"[:300],
        severity=sev,
    )
    inc.add("webhook", "uptime-kuma heartbeat", provider="uptimekuma",
            monitor_id=mon.get("monitorID"), heartbeat_status=status_code)
    inc.add("http", msg[:400] or f"heartbeat status {status_code}",
            monitor_type=mon.get("type", ""), at=hb.get("time", ""),
            status=http_status)
    inc.meta.update(provider="uptimekuma", monitor=name, heartbeat_status=status_code)
    batch.incidents.append(inc)
    return batch


def from_grafana(payload: dict, meta: dict) -> NormalizedBatch:
    """Grafana unified-alerting webhook."""
    batch = NormalizedBatch(kind="grafana", meta=meta)
    state = str(payload.get("state") or payload.get("status") or "").lower()
    if state and state not in ("alerting", "no_data", "error"):
        batch.skipped.append(f"grafana state={state or 'unknown'} — no ticket")
        return batch

    title = str(payload.get("title") or payload.get("ruleName") or "grafana alert")
    message = str(payload.get("message") or "")
    rule_url = str(payload.get("ruleUrl") or "")
    matches = payload.get("evalMatches") or []

    vals = []
    for m in matches if isinstance(matches, list) else []:
        if isinstance(m, dict):
            vals.append(f"{m.get('metric', '?')}={m.get('value', '?')}")
    detail = "; ".join(vals)

    target = _target_from(message, title, detail, fallback=str(payload.get("ruleName") or title))
    inc = Incident(
        target=target,
        source=_source_from_text(" ".join([title, message, detail])),
        summary=(title if not message else f"{title}: {message}")[:300],
        severity=Severity.HIGH if state == "alerting" else Severity.MEDIUM,
    )
    inc.add("webhook", f"grafana state={state}", provider="grafana", state=state,
            rule_url=rule_url)
    inc.add("metric", detail[:400] or message[:400],
            rule_name=payload.get("ruleName", ""), matches=len(matches))
    inc.meta.update(provider="grafana", state=state)
    batch.incidents.append(inc)
    return batch


def from_pagerduty(payload: dict, meta: dict) -> NormalizedBatch:
    """PagerDuty v3 webhook (incident.trigger / .acknowledge / .resolve)."""
    batch = NormalizedBatch(kind="pagerduty", meta=meta)
    event = payload.get("event") or {}
    etype = str(event.get("event_type") or "")
    data = event.get("data") or {}
    if not data:
        batch.skipped.append(f"pagerduty payload without event.data (type={etype})")
        return batch
    if "resolve" in etype:
        batch.skipped.append("pagerduty resolve — no ticket")
        return batch

    title = str(data.get("title") or data.get("summary") or "pagerduty incident")
    service = str((data.get("service") or {}).get("summary") or "")
    urgency = str(data.get("urgency") or "high")
    body = data.get("body") or {}
    details = body.get("details") if isinstance(body, dict) else ""
    details = details if isinstance(details, str) else str(details or "")

    target = _target_from(details, title, service, fallback=service or title)
    inc = Incident(
        target=target,
        source=_source_from_text(" ".join([title, details])),
        summary=f"{title}"[:300],
        severity=severity_of(urgency, Severity.HIGH),
    )
    inc.add("webhook", f"pagerduty {etype}", provider="pagerduty", urgency=urgency,
            service=service, html_url=data.get("html_url", ""))
    inc.add("log", details[:400] or title, incident_number=data.get("number"))
    inc.meta.update(provider="pagerduty", event_type=etype, service=service)
    batch.incidents.append(inc)
    return batch


def from_native(payload: dict, meta: dict) -> NormalizedBatch:
    """SentinelPulse native shape — post an Incident straight in.

    {"target":"https://x", "source":"http", "summary":"...",
     "severity":"high", "evidence":[{"kind":"log","detail":"..","data":{}}],
     "meta":{}}
    """
    batch = NormalizedBatch(kind="sentinelpulse", meta=meta)
    if not payload.get("target") or not payload.get("summary"):
        batch.skipped.append("native payload requires `target` and `summary`")
        return batch

    src_name = str(payload.get("source", "manual")).lower()
    inc = Incident(
        target=str(payload["target"]),
        source=_SOURCE_BY_NAME.get(src_name, Source.MANUAL),
        summary=str(payload["summary"])[:300],
        severity=severity_of(payload.get("severity"), Severity.MEDIUM),
    )
    for e in payload.get("evidence") or []:
        if isinstance(e, dict) and e.get("kind"):
            inc.add(str(e["kind"]), str(e.get("detail", ""))[:400], **(e.get("data") or {}))
    if not inc.evidence:
        inc.add("webhook", "submitted via native webhook shape", provider="sentinelpulse")
    inc.meta.update(payload.get("meta") or {})
    inc.meta["provider"] = "sentinelpulse"
    batch.incidents.append(inc)
    return batch


def from_generic(payload: dict, meta: dict) -> NormalizedBatch:
    """Unknown dialect: salvage whatever measurable facts exist."""
    batch = NormalizedBatch(kind="generic", meta=meta)

    # GitHub / Slack / custom `text` payloads carry a human sentence.
    text = str(payload.get("text") or payload.get("message") or payload.get("summary")
               or payload.get("description") or "")
    if not text:
        text = " ".join(str(v) for v in payload.values() if isinstance(v, (str, int, float)))[:600]
    if not text.strip():
        batch.skipped.append("generic payload carried no usable text")
        return batch

    status = _status_from(text)
    if status and status < 400:
        batch.skipped.append(f"generic payload looks healthy (status {status})")
        return batch

    target = _target_from(text, fallback="unknown-target")
    inc = Incident(
        target=target,
        source=_source_from_text(text),
        summary=(text[:200] or "unclassified webhook").strip(),
        severity=Severity.HIGH if (status or 0) >= 500 else Severity.MEDIUM,
    )
    inc.add("webhook", "generic webhook accepted", provider=meta.get("provider", "generic"),
            keys=sorted(payload.keys())[:12])
    inc.add("log", text[:400])
    if status:
        inc.add("http", f"status {status} in payload text", status=status)
    inc.meta["provider"] = meta.get("provider", "generic")
    batch.incidents.append(inc)
    return batch


# ------------------------------------------------------------------- dispatcher
_DETECTORS: tuple[tuple[str, Any], ...] = (
    ("sentry", lambda p: isinstance(p.get("data"), dict) and isinstance(p["data"].get("event"), dict)),
    ("alertmanager", lambda p: isinstance(p.get("alerts"), list)),
    ("uptimekuma", lambda p: isinstance(p.get("heartbeat"), dict)),
    ("grafana", lambda p: "ruleName" in p or ("state" in p and "evalMatches" in p)),
    ("pagerduty", lambda p: isinstance(p.get("event"), dict) and "event_type" in p["event"]),
    ("sentinelpulse", lambda p: "target" in p and ("summary" in p or "source" in p)),
)

_BUILDERS = {
    "sentry": from_sentry,
    "alertmanager": from_alertmanager,
    "uptimekuma": from_uptimekuma,
    "grafana": from_grafana,
    "pagerduty": from_pagerduty,
    "sentinelpulse": from_native,
    "generic": from_generic,
}


def detect_kind(payload: dict, hint: str | None = None) -> str:
    """Identify the dialect. An explicit, known hint wins over shape sniffing."""
    if hint:
        h = hint.strip().lower().replace("-", "").replace("_", "")
        aliases = {"prometheus": "alertmanager", "am": "alertmanager", "kuma": "uptimekuma",
                   "uptime": "uptimekuma", "pd": "pagerduty", "native": "sentinelpulse",
                   "sp": "sentinelpulse", "sentryio": "sentry"}
        h = aliases.get(h, h)
        if h in _BUILDERS:
            return h
    for name, test in _DETECTORS:
        try:
            if test(payload):
                return name
        except Exception:  # noqa: BLE001 — a sniff must never break the receiver
            continue
    return "generic"


def normalize(payload: dict, *, hint: str | None = None,
              headers: dict | None = None) -> NormalizedBatch:
    """Translate one webhook body into zero or more Incidents."""
    meta = {
        "received_at": __import__("time").time(),
        "hint": hint or "",
        "delivery_id": (headers or {}).get("x-sentinelpulse-delivery",
                                          (headers or {}).get("x-github-delivery",
                                                              (headers or {}).get("sentry-hook-resource", ""))),
    }
    if not isinstance(payload, dict):
        return NormalizedBatch(kind="invalid", skipped=["payload was not a JSON object"], meta=meta)

    # Sentry sends an empty probe body with the "installation" resource on setup.
    if payload.get("resource") and not payload.get("data"):
        return NormalizedBatch(kind="sentry", skipped=["sentry installation probe"], meta=meta)

    kind = detect_kind(payload, hint)
    meta["provider"] = kind
    try:
        return _BUILDERS[kind](payload, meta)
    except Exception as exc:  # noqa: BLE001 — translate or bail, never explode
        return NormalizedBatch(kind=kind, skipped=[f"normalizer error: {type(exc).__name__}: {exc}"],
                               meta=meta)


def verify_hmac(secret: str, body: bytes, signature: str | None,
                *, algorithm: str = "sha256", prefix: str = "") -> bool:
    """Constant-time HMAC check. Accepts `sha256=<hex>` or bare hex."""
    if not secret:
        return True
    if not signature:
        return False
    sig = signature.strip()
    if prefix and sig.startswith(prefix):
        sig = sig[len(prefix):]
    if "=" in sig:
        sig = sig.split("=", 1)[1]
    digest = hmac.new(secret.encode(), body, getattr(hashlib, algorithm)).hexdigest()
    return hmac.compare_digest(digest, sig.lower())


if __name__ == "__main__":  # pragma: no cover — quick dialect smoke test
    import json
    import sys

    samples = {
        "sentry": {"action": "created", "data": {"event": {
            "title": "NXDOMAIN for api.client.example.com", "culprit": "dns.resolver",
            "level": "error", "web_url": "https://sentry.io/issues/1",
            "metadata": {"type": "DNSError", "value": "getaddrinfo NXDOMAIN"}}}},
        "alertmanager": {"version": "4", "status": "firing", "alerts": [{
            "status": "firing", "labels": {"alertname": "TargetDown", "instance": "edge-1",
                                           "severity": "critical"},
            "annotations": {"summary": "edge-1 is down", "description": "probe failed"}}]},
        "uptimekuma": {"heartbeat": {"status": 0, "msg": "connect ECONNREFUSED", "time": "now"},
                       "monitor": {"name": "api-gateway", "url": "https://api.client.example.com"}},
    }
    wanted = sys.argv[1:] or list(samples)
    for name in wanted:
        b = normalize(samples[name], hint=name)
        print(f"\n{name}: kind={b.kind} incidents={b.count} skipped={b.skipped}")
        for inc in b.incidents:
            print(json.dumps(inc.to_dict(), indent=2)[:600])