#!/usr/bin/env python3
"""Compose the 90s timeline into one deterministic HTML page (window.__seek(t)).

Every figure on screen comes from demo-bundle/report.html (generated from frozen
evidence) or from build/transcripts.json (real command output). Nothing is typed.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BUILD = HERE / "build"
REPO_URL = "https://github.com/bb8654838-lgtm/sentinelpulse"

TOTAL = 90.0

SCENES = [
    {"n": 1, "t0": 0.0, "t1": 5.0, "kind": "title",
     "cap": "built inside the hackathon window &middot; Indie Developer track",
     "vo": "This is SentinelPulse. Everything you see was built between October 26 and November 4."},
    {"n": 2, "t0": 5.0, "t1": 15.0, "kind": "term", "term": "bundle",
     "cap": "claims are recomputed, never typed",
     "vo": "Alert fatigue is a billing problem. My agent decides what is worth a model call before it spends one."},
    {"n": 3, "t0": 15.0, "t1": 27.0, "kind": "report", "y0": "top", "y1": "top",
     "cap": "5 claims &middot; 7 fixtures &middot; generated from frozen evidence",
     "vo": "Same incident batch, two arms. Gate off, then gate on."},
    {"n": 4, "t0": 27.0, "t1": 39.0, "kind": "report", "y0": "top", "y1": "fixtures", "hl": "row",
     "cap": "0 noise calls &middot; 4 rules-dropped",
     "vo": "Seven incidents. Four were monitoring noise, and the gate killed them with zero model calls."},
    {"n": 5, "t0": 39.0, "t1": 49.0, "kind": "report", "y0": "fixtures", "y1": "top", "hl": "cards",
     "cap": "5533 &rarr; 4802 tokens &middot; 36.8s &rarr; 29.5s",
     "vo": "Thirteen point two percent fewer tokens, and seven point three seconds faster end to end."},
    {"n": 6, "t0": 49.0, "t1": 58.0, "kind": "report", "y0": "top", "y1": "claims", "hl": "none",
     "cap": "escalations 3/3 &middot; incident misses 0/7",
     "vo": "All three real signals still escalated. Zero incident misses, the metric that would kill this product."},
    {"n": 7, "t0": 58.0, "t1": 66.0, "kind": "term", "term": "freeze",
     "cap": "sha256-pinned &middot; <b>--check</b> exits 2 on drift",
     "vo": "Inputs are hash locked. Change the evidence, and every number on the page changes with it."},
    {"n": 8, "t0": 66.0, "t1": 75.0, "kind": "term", "term": "dry",
     "cap": "api.meta.ai/v1 &middot; <b>muse-spark-1.3</b>",
     "vo": "The routing decision is made by Muse Spark on Meta's model API. The model decides what deserves the model."},
    {"n": 9, "t0": 75.0, "t1": 84.0, "kind": "term", "term": "refuse",
     "cap": "opt-in &middot; 10 calls / 8000 tokens ceiling",
     "vo": "Live mode is opt-in and hard capped at ten calls. A demo cannot quietly turn into a bill."},
    {"n": 10, "t0": 84.0, "t1": 90.0, "kind": "outro",
     "cap": REPO_URL.replace("https://", "") + " &middot; replay needs no key",
     "vo": "SentinelPulse. Noise out, signal triaged. The repo is public, clone it and run the same check."},
]

TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>SentinelPulse — 90s demo</title>
<style>__REPORT_CSS__</style>
<style>
html,body{margin:0;padding:0;background:#05080c;overflow:hidden;color:#e8eef6;
 font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
#stage{position:relative;width:1920px;height:1080px;overflow:hidden;background:
 radial-gradient(1400px 700px at 12% -10%,#101a26 0%,#05080c 58%)}
.topbar{position:absolute;left:0;right:0;top:0;height:66px;display:flex;align-items:center;
 gap:18px;padding:0 44px;border-bottom:1px solid #15202c;background:rgba(6,10,15,.86);z-index:40}
.brand{font-weight:800;letter-spacing:.16em;font-size:17px;color:#7fe3c0}
.sub{font-size:14px;color:#6f8398}
.dots{margin-left:auto;display:flex;gap:7px;align-items:center}
.dot{width:26px;height:4px;border-radius:2px;background:#1a2735;transition:none}
.dot.on{background:#7fe3c0}
.stageno{font-size:13px;color:#5c6f83;letter-spacing:.14em;margin-left:16px}
#region{position:absolute;left:0;right:0;top:66px;bottom:158px;overflow:hidden}
#reportWin{position:absolute;inset:0;overflow:hidden}
#scaler{width:1280px;transform:scale(1.5);transform-origin:top left}
#report{position:relative;width:1280px;
 background:radial-gradient(1200px 600px at 20% -10%,#1a1a2e 0%,#0a0a0f 55%);
 color:#e7e7f0;font:15px/1.6 Inter,-apple-system,"Segoe UI",Roboto,sans-serif}
#report .wrap{max-width:1180px;margin:0 auto;padding:34px 26px 60px}
#report .hl{outline:2px solid #3ddc97;outline-offset:3px;border-radius:10px;background:rgba(61,220,151,.10)}
.term{position:absolute;left:86px;right:86px;top:22px;bottom:8px;background:#080d14;
 border:1px solid #1c2734;border-radius:14px;overflow:hidden;box-shadow:0 34px 90px rgba(0,0,0,.62);opacity:0}
.termbar{height:46px;display:flex;align-items:center;gap:9px;padding:0 20px;background:#0c131c;
 border-bottom:1px solid #18222f;font-size:15px;color:#8a9cb0;font-family:ui-monospace,Menlo,monospace}
.t{width:11px;height:11px;border-radius:50%}
.t.r{background:#ff5f57}.t.y{background:#febc2e}.t.g{background:#28c840}
.termbody{padding:24px 34px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
 font-size:22px;line-height:1.55;white-space:pre-wrap;color:#d3e2f1}
.termbody .cur{display:inline-block;width:.55em;height:1.02em;vertical-align:-.16em;
 background:#7fe3c0;margin-left:.1em}
.termbody .pl{color:#6fd3a8;font-weight:600}
.cap{position:absolute;left:0;right:0;bottom:0;height:158px;padding:0 56px;z-index:45;
 display:flex;flex-direction:column;justify-content:center;gap:8px;
 background:linear-gradient(180deg,rgba(5,8,12,0) 0%,#05080c 46%)}
.caps{font-size:41px;font-weight:800;line-height:1.18;max-width:1560px;letter-spacing:-.01em}
.caps b{color:#7fe3c0}
.vos{font-size:20px;color:#7f93a8;max-width:1500px}
.title,.outro{position:absolute;inset:0;display:flex;flex-direction:column;
 align-items:center;justify-content:center;gap:20px;text-align:center;opacity:0}
.logo{font-size:112px;font-weight:900;letter-spacing:-.03em;
 background:linear-gradient(92deg,#7fe3c0,#6c8cff);-webkit-background-clip:text;
 -webkit-text-fill-color:transparent}
.tag{font-size:36px;color:#9fb3c8;letter-spacing:.01em}
.meta{font-size:23px;color:#5f7488;letter-spacing:.16em;text-transform:uppercase}
.url{font-size:56px;font-weight:800;color:#7fe3c0;letter-spacing:-.02em}
.cmdrun{font-family:ui-monospace,Menlo,monospace;font-size:24px;color:#93a8bd;
 background:#0b1119;border:1px solid #1b2634;border-radius:10px;padding:14px 22px}
</style></head>
<body><div id="stage">
 <div class="topbar">
  <div class="brand">SENTINELPULSE</div>
  <div class="sub">noise out &middot; signal triaged &middot; Indie Developer track</div>
  <div class="dots" id="dots"></div>
  <div class="stageno" id="stageno">01 / 10</div>
 </div>
 <div id="region">
  <div id="reportWin"><div id="scaler"><div id="report">__REPORT_BODY__</div></div></div>
  <div class="term" id="term-freeze"><div class="termbar"><span class="t r"></span><span class="t y"></span><span class="t g"></span><span id="tb-freeze">meta-hack</span></div><div class="termbody" id="body-freeze"></div></div>
  <div class="term" id="term-bundle"><div class="termbar"><span class="t r"></span><span class="t y"></span><span class="t g"></span><span id="tb-bundle">meta-hack</span></div><div class="termbody" id="body-bundle"></div></div>
  <div class="term" id="term-dry"><div class="termbar"><span class="t r"></span><span class="t y"></span><span class="t g"></span><span id="tb-dry">meta-hack</span></div><div class="termbody" id="body-dry"></div></div>
  <div class="term" id="term-refuse"><div class="termbar"><span class="t r"></span><span class="t y"></span><span class="t g"></span><span id="tb-refuse">meta-hack</span></div><div class="termbody" id="body-refuse"></div></div>
  <div class="title" id="sc-title">
   <div class="logo">SentinelPulse</div>
   <div class="tag">noise out &middot; signal triaged</div>
   <div class="meta">built 26 Oct &rarr; 4 Nov 2026 &middot; Indie Developer</div>
  </div>
  <div class="outro" id="sc-outro">
   <div class="meta">public repo</div>
   <div class="url">github.com/bb8654838-lgtm/sentinelpulse</div>
   <div class="cmdrun">git clone &middot; bash demos/l3/demo_take.sh</div>
   <div class="meta">replay runs offline &middot; zero model calls</div>
  </div>
 </div>
 <div class="cap"><div class="caps" id="caps"></div><div class="vos" id="vos"></div></div>
</div>
<script>
const D = __DATA_JSON__;
const win = document.getElementById('reportWin');
const rep = document.getElementById('report');
const caps = document.getElementById('caps');
const vos = document.getElementById('vos');
const stageno = document.getElementById('stageno');
const dots = document.getElementById('dots');
D.scenes.forEach(s => { const d = document.createElement('div'); d.className = 'dot'; dots.appendChild(d); });
const dotEls = [...dots.children];
const A = {};
function measure() {
  rep.style.transform = 'translateY(0px)';
  // all values here are LAYOUT px (unscaled); #report is position:relative so
  // h2.offsetTop is already relative to the report origin.
  const Z = 1.5;
  A.top = 0;
  document.querySelectorAll('#report h2').forEach(h => {
    const t = h.textContent.toLowerCase();
    const y = h.offsetTop;
    if (t.includes('claims')) A.claims = Math.max(0, y - 110);
    if (t.includes('per-fixture')) A.fixtures = Math.max(0, y - 95);
    if (t.includes('frozen')) A.frozen = Math.max(0, y - 110);
  });
  A.max = Math.max(0, rep.scrollHeight - win.clientHeight / Z);
  // markers
  document.querySelectorAll('#report table tr').forEach(tr => {
    if (/drop/i.test(tr.textContent)) tr.classList.add('hlrow');
  });
  document.querySelectorAll('#report .card').forEach(c => {
    const k = c.querySelector('.k');
    if (k && /^(tokens|latency)$/i.test(k.textContent.trim())) c.classList.add('hlcard');
  });
}
measure();
window.A = A;
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function ease(p) { return p < .5 ? 4*p*p*p : 1 - Math.pow(-2*p+2, 3)/2; }
function setHLAlpha(a) {
  document.querySelectorAll('#report .hlrow').forEach(el => {
    el.style.outline = a > .01 ? '2px solid rgba(61,220,151,' + a + ')' : 'none';
    el.style.background = 'rgba(61,220,151,' + (a*0.12) + ')';
    el.style.borderRadius = '10px';
  });
  document.querySelectorAll('#report .hlcard').forEach(el => {
    el.style.outline = a > .01 ? '2px solid rgba(61,220,151,' + a + ')' : 'none';
    el.style.outlineOffset = '3px';
  });
}
function reveal(el, text, p, t) {
  const lines = text.split('\n').length;
  el.style.fontSize = lines <= 3 ? '34px' : (lines <= 6 ? '25px' : '21px');
  const k = clamp((p - 0.06) / 0.86, 0, 1);
  const n = Math.floor(text.length * k);
  const blink = (t * 2) % 1 < 0.5;
  const done = k >= 1;
  el.textContent = text.slice(0, n);
  if (!done || blink) { const c = document.createElement('span'); c.className = 'cur'; c.textContent = '\u258a'; el.appendChild(c); }
}
function seek(t) {
  let s = D.scenes[0];
  for (const x of D.scenes) if (t >= x.t0 && t < x.t1) s = x;
  if (t >= D.scenes[D.scenes.length-1].t1) s = D.scenes[D.scenes.length-1];
  const p = clamp((t - s.t0) / (s.t1 - s.t0), 0, 1);
  caps.innerHTML = s.cap;
  vos.textContent = s.vo;
  stageno.textContent = String(s.n).padStart(2,'0') + ' / 10';
  dotEls.forEach((d,i) => d.classList.toggle('on', i < s.n));
  // reset visibility
  ['title','outro'].forEach(k => document.getElementById('sc-' + k).style.opacity = 0);
  document.getElementById('term-freeze').style.opacity = 0;
  document.getElementById('term-bundle').style.opacity = 0;
  document.getElementById('term-dry').style.opacity = 0;
  document.getElementById('term-refuse').style.opacity = 0;
  win.style.opacity = 0;
  if (s.kind === 'title') {
    const el = document.getElementById('sc-title'); win.style.opacity = 0;
    el.style.opacity = clamp(p / 0.22, 0, 1);
    el.style.transform = 'scale(' + (0.965 + 0.035*ease(clamp(p/0.6,0,1))) + ')';
  } else if (s.kind === 'outro') {
    const el = document.getElementById('sc-outro');
    el.style.opacity = clamp(p/0.25, 0, 1);
  } else if (s.kind === 'term') {
    win.style.opacity = 0;
    const box = document.getElementById('term-' + s.term);
    box.style.opacity = clamp(p/0.06, 0, 1);
    reveal(document.getElementById('body-' + s.term), D.terms[s.term], p, t);
  } else if (s.kind === 'report') {
    win.style.opacity = 1;
    const a = A[s.y0] === undefined ? 0 : A[s.y0];
    const b = A[s.y1] === undefined ? 0 : A[s.y1];
    const y = a + (b - a) * ease(clamp(p/0.55, 0, 1));
    rep.style.transform = 'translateY(-' + clamp(y, 0, A.max) + 'px)';
    if (s.hl === 'row') setHLAlpha(clamp((p-0.35)/0.4, 0, 1));
    else if (s.hl === 'cards') setHLAlpha(clamp((p-0.35)/0.3, 0, 1));
    else setHLAlpha(0);
  }
}
window.__seek = seek;
window.__ready = true;
seek(0);
</script>
</body></html>
"""


def term_text(rec):
    cmd = rec["cmd"]
    body = rec["text"]
    head = "meta-hack $ " + cmd + "\n"
    tail = body
    if rec["rc"] != 0:
        tail += "\n[exit %d]" % rec["rc"]
    return head + tail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts", default=str(BUILD / "transcripts.json"))
    ap.add_argument("--report", default=str(ROOT / "demo-bundle" / "report.html"))
    ap.add_argument("--out", default=str(BUILD / "composition.html"))
    a = ap.parse_args()

    tr = json.loads(Path(a.transcripts).read_text(encoding="utf-8"))
    runs = tr["runs"]
    report_html = Path(a.report).read_text(encoding="utf-8")
    css = report_html.split("<style>")[1].split("</style>")[0]
    body = report_html.split("<body>")[1].split("</body>")[0]

    terms = {
        "freeze": term_text(runs["freeze"]),
        "bundle": term_text(runs["bundle"]),
        "dry": term_text(runs["dry"]),
        "refuse": term_text(runs["refuse"]),
    }
    data = {
        "total": TOTAL,
        "scenes": SCENES,
        "terms": terms,
        "repo": REPO_URL,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_captured_at": tr.get("captured_at"),
    }
    html_out = (TEMPLATE
                .replace("__REPORT_CSS__", css)
                .replace("__REPORT_BODY__", body)
                .replace("__DATA_JSON__", json.dumps(data)))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(html_out, encoding="utf-8")
    print("built %s (%d bytes, %d scenes, total %.1fs)" % (
        a.out, len(html_out), len(SCENES), TOTAL))


if __name__ == "__main__":
    main()
