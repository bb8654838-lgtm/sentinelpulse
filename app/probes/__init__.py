"""Deterministic health probes. Measure only — never conclude."""
from .health import capture, probe_dns, probe_headers, probe_http, resolve_ips  # noqa: F401

__all__ = ["capture", "probe_dns", "probe_headers", "probe_http", "resolve_ips"]
