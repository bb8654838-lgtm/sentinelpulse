#!/usr/bin/env python3
"""Render demo-bundle/bundle.json into one self-contained HTML report.

No CDN, no fonts, no JS dependencies: the file opens on an air-gapped laptop and
survives a bad conference wifi. Every figure is emitted from the bundle, so the
report cannot disagree with the evidence it cites.

Usage:
    python -m demos.l3.render_report
    python -m demos.l3.render_report --bundle demo-bundle/bundle.json --out demo-bundle/report.html
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CSS = """
:root{--bg:#0a0a0f;--panel:#12121a;--line:#232334;--ink:#e7e7f0;--dim:#8b8ba3;
--ok:#3ddc97;--warn:#ffb454;--bad:#ff5c7a;--accent:#6c8cff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#1a1a2e 0%,var(--bg) 55%);
color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:48px 24px 80px}
h1{font-size:28px;margin:0 0 6px;letter-spacing:-.02em}h2{font-size:16px;text-transform:uppercase;
letter-spacing:.12em;color:var(--dim);margin:40px 0 14px;font-weight:600}
.sub{color:var(--dim);margin:0 0 28px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}
.card{background:linear-gradient(180deg,#14141d,#101017);border:1px solid var(--line);
border-radius:14px;padding:16px 18px}
.card .k{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.1em}
.card .v{font-size:24px;font-weight:600;margin-top:6px;letter-spacing:-.02em}
.card .d{color:var(--dim);font-size:13px;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.08em}
tr:hover td{background:#14141d}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600}
.pill.drop{background:rgba(61,220,151,.12);color:var(--ok)}
.pill.esc{background:rgba(108,140,255,.14);color:var(--accent)}
.pill.noise{background:rgba(139,139,163,.12);color:var(--dim)}
.pill.signal{background:rgba(255,180,84,.14);color:var(--warn)}
code{background:#191922;border:1px solid var(--line);padding:1px 6px;border-radius:6px;font-size:13px}
.bar{height:10px;border-radius:999px;background:#1c1c28;overflow:hidden;margin-top:8px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#8fa6ff)}
.bar.alt>i{background:linear-gradient(90deg,var(--ok),#8affc9)}
.claim{display:flex;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}
.claim b{min-width:34px;color:var(--accent)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--dim)}
footer{margin-top:56px;color:var(--dim);font-size:12px;border-top:1px solid var(--line);padding-top:18px}
"""


def esc(v) -> str:
    return html.escape(str(v))


def bars(bundle: dict) -> str:
    base = bundle["bench"]["arms"]["baseline_gate_off"]
    tier = bundle["bench"]["arms"]["tiered_gate_on"]
    rows = []
    for label, key, unit in (("tokens", "tokens", ""), ("model calls", "model_calls", ""),
                             ("noise calls", "calls_on_noise", ""),
                             ("latency", "latency_s", "s")):
        b, t = base[key], tier[key]
        mx = max(b, t, 1)
        rows.append(
            f'<div class="card"><div class="k">{esc(label)}</div>'
            f'<div class="v">{esc(t)}{esc(unit)} <span style="color:var(--dim);font-size:14px">'
            f'from {esc(b)}{esc(unit)}</span></div>'
            f'<div class="bar"><i style="width:{max(3, round(100 * b / mx))}%"></i></div>'
            f'<div class="bar alt"><i style="width:{max(3, round(100 * t / mx))}%"></i></div>'
            f'<div class="d">grey = gate off · green = gate on</div></div>'
        )
    return "".join(rows)


def render(bundle: dict) -> str:
    b = bundle["bench"]
    rows = []
    for f in b["per_fixture"]:
        route = f["routed"] or "?"
        cls = "drop" if route == "drop" else "esc"
        rows.append(
            f"<tr><td>{esc(f['fixture'])}</td>"
            f"<td><span class='pill {'noise' if f['label'] == 'noise' else 'signal'}'>"
            f"{esc(f['label'])}</span></td>"
            f"<td><span class='pill {cls}'>{esc(route)}</span></td>"
            f"<td class='mono'>{esc(', '.join(f['stages']))}</td>"
            f"<td>{esc(f['tokens'])}</td>"
            f"<td class='mono'>{esc(f['signature'])}</td></tr>"
        )
    claims = "".join(
        f"<div class='claim'><b>{esc(c['id'])}</b><div>{esc(c['claim'])}"
        f"<div class='mono'>{esc(c['value'])} · {esc(c['evidence'])}</div></div></div>"
        for c in bundle["claims"]
    )
    freeze_rows = "".join(
        f"<tr><td class='mono'>{esc(a['path'])}</td><td class='mono'>{esc(a.get('sha256', 'MISSING')[:32])}</td>"
        f"<td>{esc(a.get('bytes', ''))}</td></tr>"
        for a in bundle["freeze"]["artifacts"]
    )
    shots = "".join(
        f"<li class='mono'>{esc(s['path'])} · {esc(s['bytes'])} B · {esc(s['sha256'][:16])}</li>"
        for s in bundle["screenshots"]
    ) or "<li class='mono'>none</li>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SentinelPulse — demo bundle</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>SentinelPulse</h1>
<p class="sub">{esc(bundle['product']['one_liner'])}<br>
Track <b>{esc(bundle['track'])}</b> · mode <b>{esc(bundle['mode'])}</b> · generated {esc(bundle['generated_at'])}</p>

<div class="grid">{bars(bundle)}</div>
<div class="grid" style="margin-top:14px">
  <div class="card"><div class="k">incident misses</div><div class="v" style="color:var(--ok)">
  {esc(b['incident_misses']['tiered'])}/{esc(b['fixtures'])}</div>
  <div class="d">gate off: {esc(b['incident_misses']['baseline'])}/{esc(b['fixtures'])}</div></div>
  <div class="card"><div class="k">rules-dropped noise</div><div class="v" style="color:var(--ok)">
  {esc(b['arms']['tiered_gate_on']['rules_dropped'])}</div>
  <div class="d">zero tokens spent on them</div></div>
  <div class="card"><div class="k">token delta</div><div class="v">
  {esc(b['deltas']['tokens_pct'])}%</div><div class="d">fewer tokens per batch</div></div>
</div>

<h2>Claims → evidence</h2>{claims}

<h2>Per-fixture routing (gate on)</h2>
<table><thead><tr><th>fixture</th><th>label</th><th>route</th><th>stages</th><th>tokens</th>
<th>signature</th></tr></thead><tbody>{''.join(rows)}</tbody></table>

<h2>Frozen inputs</h2>
<table><thead><tr><th>artifact</th><th>sha256</th><th>bytes</th></tr></thead>
<tbody>{freeze_rows}</tbody></table>

<h2>Captured screenshots</h2><ul>{shots}</ul>

<h2>Reproduce</h2><ul>
{''.join(f"<li><code>{esc(c)}</code></li>" for c in bundle['reproduce'])}
</ul>

<footer>Generated offline from <code>{esc(b['source'])}</code>. Every number above is read
from the frozen evidence file — none is typed by hand.</footer>
</div></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(ROOT / "demo-bundle" / "bundle.json"))
    ap.add_argument("--out", default=str(ROOT / "demo-bundle" / "report.html"))
    args = ap.parse_args(argv)
    bundle = json.loads(Path(args.bundle).read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(bundle))
    print(f"report -> {out} ({out.stat().st_size} B, self-contained)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
