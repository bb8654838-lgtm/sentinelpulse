#!/usr/bin/env python3
"""Frame-accurate capture: drives window.__seek(t) and screenshots each frame.

Deterministic: frame N is always t = N / fps, so re-running reproduces the file.
"""
import argparse
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=str(BUILD / "composition.html"))
    ap.add_argument("--out", default=str(HERE / "frames"))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--total", type=float, default=90.0)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=0, help="exclusive; 0 = all")
    ap.add_argument("--quality", type=int, default=90)
    ap.add_argument("--chromium", default="", help="explicit chrome binary (else autodetect)")
    a = ap.parse_args()

    n_frames = int(round(a.total * a.fps))
    end = a.end or n_frames
    end = min(end, n_frames)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    chrome = a.chromium or os.environ.get("CHROME_BIN", "")
    if not chrome:
        cands = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
                       key=lambda q: q.stat().st_mtime, reverse=True)
        chrome = str(cands[0]) if cands else ""
    if not chrome or not Path(chrome).exists():
        raise SystemExit("FATAL: no chromium binary found (set CHROME_BIN)")
    print("chromium:", chrome)

    t0 = time.time()
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=chrome, args=[
            "--headless=new", "--force-color-profile=srgb",
            "--hide-scrollbars", "--disable-lcd-text",
            "--force-device-scale-factor=1",
        ])
        pg = br.new_page(viewport={"width": 1920, "height": 1080},
                         device_scale_factor=1)
        pg.goto("file://" + str(Path(a.html).resolve()), wait_until="load")
        pg.wait_for_function("window.__ready === true", timeout=15000)
        pg.wait_for_timeout(400)
        print("anchors:", pg.evaluate("JSON.stringify(window.A || {})"))
        for i in range(a.start, end):
            t = i / a.fps
            pg.evaluate("t => window.__seek(t)", t)
            pg.screenshot(path=str(out / ("%05d.jpg" % i)),
                          type="jpeg", quality=a.quality, animations="disabled")
            if i % 300 == 0:
                el = time.time() - t0
                rate = (i - a.start + 1) / max(el, 1e-6)
                print("frame %d/%d  t=%.2fs  %.1f fps  eta %.0fs"
                      % (i, end, t, rate, (end - i) / max(rate, 1e-6)), flush=True)
        br.close()
    print("done %d frames in %.1fs -> %s" % (end - a.start, time.time() - t0, out))


if __name__ == "__main__":
    main()
