#!/usr/bin/env python3
"""haise-flow: screenshot a built page in light and dark with headless Chrome / Edge
and report JavaScript console errors.

usage: render_check.py <page.html> [--width 2048] [--height 1800] [--out-dir DIR] [--browser PATH]

Writes <stem>.visual-check.<W>x<H>.light.png and .dark.png next to the page (or
in --out-dir). Look at both PNGs before reporting: overlapping text, labels
covering lines, clipped legend, unreadable dark colours. Exit 0 ok, 1 console
errors found, 2 no browser / render failed. Standard library only.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CANDIDATES = [
    os.environ.get("HAISE_BROWSER", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge", "microsoft-edge"]


def find_browser(explicit):
    for c in [explicit] + CANDIDATES:
        if c and Path(c).is_file():
            return c
    for n in NAMES:
        p = shutil.which(n)
        if p:
            return p
    return None


def run(browser, args, timeout=120):
    return subprocess.run([browser] + args, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--height", type=int, default=1800)
    ap.add_argument("--out-dir")
    ap.add_argument("--browser")
    a = ap.parse_args(argv)
    page = Path(a.page).resolve()
    if not page.is_file():
        print(f"ERROR: {page} not found", file=sys.stderr)
        return 2
    browser = find_browser(a.browser)
    if not browser:
        print("ERROR: no Chrome/Edge found; set HAISE_BROWSER or pass --browser", file=sys.stderr)
        return 2
    out_dir = Path(a.out_dir).resolve() if a.out_dir else page.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    url = page.as_uri()
    rc = 0
    with tempfile.TemporaryDirectory(prefix="haise-flow-") as prof:
        base = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                f"--user-data-dir={prof}", "--virtual-time-budget=15000"]
        for theme in ("light", "dark"):
            png = out_dir / f"{page.stem}.visual-check.{a.width}x{a.height}.{theme}.png"
            extra = ["--force-dark-mode", "--blink-settings=preferredColorScheme=0"] if theme == "dark" else \
                    ["--blink-settings=preferredColorScheme=1"]
            r = run(browser, base + extra + [f"--window-size={a.width},{a.height}", f"--screenshot={png}", url])
            if not png.is_file():
                print(f"ERROR: screenshot failed ({theme}): {r.stderr[-400:]}", file=sys.stderr)
                return 2
            print(f"{theme}: {png}")
        r = run(browser, base + ["--enable-logging=stderr", "--v=0", "--dump-dom", url])
        errs = [l for l in r.stderr.splitlines()
                if ("CONSOLE" in l or "Uncaught" in l) and ("rror" in l or "Uncaught" in l)]
        if errs:
            rc = 1
            print("CONSOLE ERRORS:")
            for l in errs[:20]:
                print("  ", l.strip()[:300])
        else:
            print("console: no errors")
        if "archify-diagram-title" not in r.stdout:
            print("WARN: rendered DOM has no diagram title; check the page opens", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
