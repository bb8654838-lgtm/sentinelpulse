#!/usr/bin/env python3
"""
SentinelPulse :: incident model  (app/core/incident.py)

One normalised shape for every failure SentinelPulse can see, so the triage
loop, the visual inspector and the remediator all speak the same language.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Source(str, Enum):
    DNS = "dns"                    # NXDOMAIN / SERVFAIL / no-record
    HTTP = "http"                  # 5xx, 4xx, TLS, timeout
    VISUAL = "visual"              # layout breakage, defacement, clickjacking overlay
    ASSET = "asset"                # broken image/css/js container
    MANUAL = "manual"              # injected by the mock harness


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class Evidence:
    """Raw proof collected by a probe. Never a conclusion — just measured facts."""

    kind: str                       # dns|http|screenshot|asset|header
    detail: str                     # human-readable one-liner
    data: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.kind}] {self.detail}"


@dataclass
class Incident:
    """A single failure event moving through the SentinelPulse pipeline."""

    target: str                     # host, URL or asset under inspection
    source: Source
    summary: str                    # short symptom, e.g. "NXDOMAIN for api.client.example.com"
    evidence: list[Evidence] = field(default_factory=list)
    severity: Severity = Severity.MEDIUM
    signature: str = ""             # classified error signature (set by triage)
    root_cause: str = ""            # filled by the reasoning core
    plan: list[str] = field(default_factory=list)
    incident_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    def add(self, kind: str, detail: str, **data: Any) -> "Incident":
        self.evidence.append(Evidence(kind=kind, detail=detail, data=data))
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source"] = self.source.value
        d["severity"] = self.severity.value
        return d

    def brief(self) -> str:
        """Compact text block fed to the reasoning model."""
        lines = [
            f"incident_id : {self.incident_id}",
            f"target      : {self.target}",
            f"source      : {self.source.value}",
            f"severity    : {self.severity.value}",
            f"summary     : {self.summary}",
        ]
        if self.evidence:
            lines.append("evidence    :")
            lines += [f"  - {e}" for e in self.evidence]
        return "\n".join(lines)


class IncidentFactory:
    """Convenience constructors for the mock harness."""

    @staticmethod
    def nxdomain(host: str, resolver_note: str = "") -> Incident:
        inc = Incident(
            target=host, source=Source.DNS,
            summary=f"NXDOMAIN for {host}",
            severity=Severity.HIGH,
        )
        inc.add("dns", f"resolver answered NXDOMAIN (RCODE 3) for {host}",
                host=host, rcode=3, note=resolver_note)
        return inc

    @staticmethod
    def http_5xx(url: str, status: int, body_head: str = "") -> Incident:
        inc = Incident(
            target=url, source=Source.HTTP,
            summary=f"HTTP {status} from {url}",
            severity=Severity.CRITICAL if status >= 500 else Severity.HIGH,
        )
        inc.add("http", f"upstream returned {status}", url=url, status=status,
                body_head=body_head[:400])
        return inc

    @staticmethod
    def visual(url: str, screenshot: str, note: str = "") -> Incident:
        inc = Incident(
            target=url, source=Source.VISUAL,
            summary=f"visual anomaly on {url}",
            severity=Severity.HIGH,
        )
        inc.add("screenshot", f"rendered capture stored at {screenshot}",
                path=screenshot, note=note)
        return inc
