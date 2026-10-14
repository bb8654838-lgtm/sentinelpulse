#!/usr/bin/env python3
"""
SentinelPulse :: mock incident harness  (demos/mock_incidents.py)

Real, reproducible fixtures. Nothing here is a stub: the DNS case is a genuine
NXDOMAIN, the 5xx case is a genuine HTTP fetch, the visual cases are genuine
rendered screenshots with genuine defects drawn into them.

  python demos/mock_incidents.py build   -> write fixtures to demos/fixtures/
  python demos/mock_incidents.py list    -> show the catalogue
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.incident import Incident, IncidentFactory, Severity, Source  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# .invalid and .example are RFC-reserved TLDs: NXDOMAIN is guaranteed by DNS itself,
# so this fixture is stable and cannot collide with a real host.
NX_HOST = "api.client.invalid"
BAD_URL = "https://httpbin.org/status/503"
OK_URL = "https://httpbin.org/html"


def catalogue() -> list[dict]:
    return [
        {"id": "dns-nxdomain", "source": "dns", "target": NX_HOST,
         "what": "guaranteed NXDOMAIN (RFC 2606 .invalid TLD)"},
        {"id": "http-5xx", "source": "http", "target": BAD_URL,
         "what": "upstream 503 from a live endpoint"},
        {"id": "header-gap", "source": "http", "target": OK_URL,
         "what": "live page missing hardening headers"},
        {"id": "visual-break", "source": "visual", "target": "mock://dashboard",
         "what": "rendered page: missing image, collapsed grid, overlay banner"},
        {"id": "code-bug", "source": "manual", "target": "app/probes/health.py",
         "what": "real Python defect in the repo for the remediator to fix"},
    ]


# --------------------------------------------------------------- visual fixtures
def _render_defective_page(path: Path) -> Path:
    """Draw a rendered-page mock with deliberate, human-visible defects.

    Defects encoded (so the VLM has something real to find):
      * broken image container  -> grey box with the classic torn-image glyph
      * collapsed layout        -> second card escaped its grid and overlaps text
      * defacement overlay      -> red full-width banner with unauthorised text
    """
    from PIL import Image, ImageDraw

    W, H = 1280, 900
    img = Image.new("RGB", (W, H), (247, 248, 250))
    d = ImageDraw.Draw(img)

    # page chrome
    d.rectangle([0, 0, W, 64], fill=(32, 38, 50))
    d.text((24, 24), "ACME OPS  |  production dashboard", fill=(230, 233, 240))
    d.text((24, 84), "Service health", fill=(20, 24, 32))

    # valid card
    d.rectangle([24, 120, 400, 300], outline=(203, 209, 218), width=2, fill=(255, 255, 255))
    d.text((44, 140), "api-gateway", fill=(20, 24, 32))
    d.text((44, 168), "200 OK   42ms", fill=(20, 130, 80))
    d.rectangle([44, 210, 380, 226], fill=(230, 240, 235))

    # DEFECT 1: broken asset container (torn-image glyph)
    d.rectangle([432, 120, 808, 300], outline=(203, 209, 218), width=2, fill=(238, 239, 242))
    d.rectangle([580, 170, 660, 250], outline=(150, 156, 166), width=3, fill=(222, 224, 228))
    d.line([580, 250, 660, 170], fill=(150, 156, 166), width=3)
    d.line([580, 170, 660, 250], fill=(150, 156, 166), width=3)
    d.text((530, 264), "logo-main.png  FAILED TO LOAD", fill=(176, 42, 42))

    # DEFECT 2: collapsed grid — card escaped its slot and overlaps the text block
    d.rectangle([24, 340, 500, 520], outline=(203, 209, 218), width=2, fill=(255, 255, 255))
    d.text((44, 360), "orders-api", fill=(20, 24, 32))
    d.rectangle([860, 300, 1240, 470], outline=(176, 42, 42), width=3, fill=(255, 245, 245))
    d.text((880, 320), "orders-api (escaped grid)", fill=(176, 42, 42))
    d.line([500, 430, 860, 380], fill=(176, 42, 42), width=2)

    # DEFECT 3: defacement / clickjacking overlay
    d.rectangle([0, 560, W, 640], fill=(178, 24, 24))
    d.text((24, 590), "SYSTEM OWNED - click here to claim prize", fill=(255, 255, 255))
    d.text((24, 660), "This banner was not part of the original page.", fill=(70, 76, 88))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def _render_clean_page(path: Path) -> Path:
    """Control image: same layout, no defects. Guards against false positives."""
    from PIL import Image, ImageDraw

    W, H = 1280, 900
    img = Image.new("RGB", (W, H), (247, 248, 250))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 64], fill=(32, 38, 50))
    d.text((24, 24), "ACME OPS  |  production dashboard", fill=(230, 233, 240))
    for x in (24, 432, 840):
        d.rectangle([x, 120, x + 376, 300], outline=(203, 209, 218), width=2, fill=(255, 255, 255))
        d.text((x + 20, 140), "service-ok", fill=(20, 24, 32))
        d.text((x + 20, 168), "200 OK   38ms", fill=(20, 130, 80))
        d.rectangle([x + 20, 210, x + 356, 226], fill=(230, 240, 235))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def build_fixtures() -> dict:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    broken = _render_defective_page(FIXTURES / "dashboard_broken.png")
    clean = _render_clean_page(FIXTURES / "dashboard_clean.png")
    cat = {"fixtures": {
        "broken_screenshot": str(broken),
        "clean_screenshot": str(clean),
        "nxdomain_host": NX_HOST,
        "bad_url": BAD_URL,
        "ok_url": OK_URL,
    }}
    (FIXTURES / "catalogue.json").write_text(json.dumps(cat, indent=2))
    return cat


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "build":
        print(json.dumps(build_fixtures(), indent=2))
    else:
        for row in catalogue():
            print(f"{row['id']:16s} {row['source']:7s} {row['target']:34s} {row['what']}")