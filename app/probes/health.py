#!/usr/bin/env python3
"""
SentinelPulse :: health probes  (app/probes/health.py)

Deterministic, dependency-free collectors. They only MEASURE; they never
conclude. Conclusions belong to the reasoning core (app/core/pipeline.py).

probe_dns(host)        -> NXDOMAIN / SERVFAIL / OK
probe_http(url)        -> status, latency, server headers, body head
probe_headers(url)     -> hardening headers worth flagging
capture(url, out)      -> rendered screenshot via headless Chromium (if present)
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

try:  # importable both as `app.probes.health` and as a loose script
    from app.core.incident import Incident, IncidentFactory, Severity, Source
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.incident import Incident, IncidentFactory, Severity, Source

UA = "SentinelPulse-Probe/0.1"
CHROME = Path.home() / ".cache/ms-playwright/chromium-1234/chrome-linux/chrome"


# ------------------------------------------------------------------------- DNS
def probe_dns(host: str) -> Incident | None:
    """Resolve `host`. Returns an Incident on failure, None when healthy."""
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        code = getattr(exc, "errno", None)
        if code == socket.EAI_NONAME:      # -2 -> NXDOMAIN
            return IncidentFactory.nxdomain(host, "getaddrinfo EAI_NONAME")
        inc = Incident(                     # -3 EAI_AGAIN, -5 EAI_NODATA, etc.
            target=host, source=Source.DNS,
            summary=f"DNS failure (errno {code}) for {host}",
            severity=Severity.HIGH,
        )
        inc.add("dns", f"getaddrinfo failed: {exc}", host=host, errno=code)
        return inc
    return None


def resolve_ips(host: str) -> list[str]:
    try:
        return sorted({i[4][0] for i in socket.getaddrinfo(host, None)})
    except socket.gaierror:
        return []


# ------------------------------------------------------------------------ HTTP
def probe_http(url: str, timeout: float = 15.0) -> tuple[Incident | None, dict]:
    """Fetch `url`. Returns (Incident|None, metrics)."""
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    metrics: dict = {"url": url, "latency_ms": None, "status": None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode(errors="replace")
            metrics.update(
                status=resp.status,
                latency_ms=round((time.time() - t0) * 1000, 1),
                server=resp.headers.get("Server", ""),
                content_type=resp.headers.get("Content-Type", ""),
                headers={k.lower(): v for k, v in resp.headers.items()},
                body_head=body[:400],
            )
            return None, metrics
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode(errors="replace")
        metrics.update(status=exc.code, latency_ms=round((time.time() - t0) * 1000, 1),
                       headers={k.lower(): v for k, v in exc.headers.items()},
                       body_head=body[:400])
        return IncidentFactory.http_5xx(url, exc.code, body), metrics
    except (urllib.error.URLError, ssl.SSLError, TimeoutError) as exc:
        inc = Incident(target=url, source=Source.HTTP,
                       summary=f"connection failure for {url}", severity=Severity.CRITICAL)
        inc.add("http", f"{type(exc).__name__}: {exc}", url=url)
        metrics.update(error=str(exc))
        return inc, metrics


# --------------------------------------------------------------------- headers
HARDENING_HEADERS = {
    "strict-transport-security": "missing HSTS — protocol downgrade risk",
    "content-security-policy": "missing CSP — script injection surface",
    "x-content-type-options": "missing nosniff — MIME confusion surface",
    "x-frame-options": "missing frame guard — clickjacking surface",
}


def probe_headers(url: str, timeout: float = 12.0) -> tuple[Incident | None, dict]:
    _, m = probe_http(url, timeout=timeout)
    headers = m.get("headers") or {}
    if not headers:
        return None, m
    missing = {h: why for h, why in HARDENING_HEADERS.items() if h not in headers}
    incident = None
    if missing:
        incident = Incident(target=url, source=Source.HTTP,
                            summary=f"{len(missing)} hardening header(s) missing on {url}",
                            severity=Severity.LOW)
        for h, why in missing.items():
            incident.add("header", f"{h}: {why}", header=h)
    return incident, {"present": sorted(headers), "missing": sorted(missing)}


# ------------------------------------------------------------------ screenshot
def capture(url: str, out_path: str | Path, timeout: int = 60) -> Path | None:
    """Headless render via the lab's Chromium. Returns path, or None if absent."""
    if not CHROME.exists():
        return None
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
        f"--screenshot={out}", "--window-size=1440,1000",
        "--virtual-time-budget=8000", url,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None
    return out if out.exists() else None


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "api.client.example.com"
    inc = probe_dns(target)
    print(json.dumps(inc.to_dict() if inc else {"status": "healthy", "target": target},
                     indent=2))