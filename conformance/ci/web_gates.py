#!/usr/bin/env python3
"""
web_gates.py — bring the WEBSITE layer under the same red/green harness.

Two modes (run_suite gates):
  web-unit     node --test tests/web/unit/    (Pages functions: sanitizer, preview,
               catalog probes, auth flows, admin gating, PyPI fallback — all against
               the REAL exported modules with stubbed fetch + in-memory KV)
  web-browser  headless-Chromium smoke of the /tool SPA against the CONTROLLED
               FIXTURE (settings headers on the wire, endpoint derivation, product
               discovery incl. query:<term> + manual override). Serves public/ on
               :8189 itself; expects the fixture already on :8184 (run_suite boots it).

Exit 0 pass · 1 fail · 2 honest skip (node/browser/deps unavailable — CI installs
them so skips only ever happen on bare local machines).
"""
import glob, os, shutil, subprocess, sys, time, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

def find_chrome():
    for env in ("CHROME_PATH", "SPCK_CHROME"):
        p = os.environ.get(env)
        if p and os.path.exists(p):
            return p
    pats = [
        os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell"),
        os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell"),
        os.path.expanduser("~/.cache/puppeteer/chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell"),
        os.path.expanduser("~/.cache/puppeteer/chrome/*/chrome-linux64/chrome"),
    ]
    for pat in pats:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None

def unit():
    if not shutil.which("node"):
        print("node not available — skipping web unit tests"); return 2
    files = sorted(glob.glob(str(ROOT / "tests" / "web" / "unit" / "*.test.mjs")))
    if not files:
        print("no unit test files found"); return 1
    r = subprocess.run(["node", "--test", *files],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    tail = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith(("# pass", "# fail", "not ok"))]
    print("\n".join(tail[-12:]) or (r.stdout + r.stderr)[-800:])
    print("web-unit:", "PASS" if r.returncode == 0 else "FAIL")
    return 0 if r.returncode == 0 else 1

def browser():
    if not shutil.which("node"):
        print("node not available — skipping browser smoke"); return 2
    chrome = find_chrome()
    if not chrome:
        print("no headless Chromium found — skipping browser smoke "
              "(CI installs chrome-headless-shell; locally set CHROME_PATH)"); return 2
    webdir = ROOT / "tests" / "web"
    if not (webdir / "node_modules" / "puppeteer-core").exists():
        i = subprocess.run(["npm", "i", "--no-audit", "--no-fund"], cwd=str(webdir),
                           capture_output=True, text=True, timeout=180)
        if i.returncode != 0:
            print("npm install failed — skipping browser smoke:", i.stderr[-300:]); return 2
    static = subprocess.Popen([sys.executable, "-m", "http.server", "8189",
                               "-d", str(ROOT / "public"), "--bind", "127.0.0.1"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    try:
        env = {**os.environ, "CHROME_PATH": chrome,
               "PAGE_URL": "http://127.0.0.1:8189/check.html",
               "BASE": "http://127.0.0.1:8189",
               "FIXTURE": "http://127.0.0.1:8184"}
        rc = 0
        for script in ("responsive_smoke.mjs", "site_smoke.mjs"):
            r = subprocess.run(["node", str(webdir / "browser" / script)],
                               cwd=str(webdir), env=env, capture_output=True, text=True, timeout=180)
            print(f"[{script}]"); print((r.stdout + r.stderr).strip()[-1000:])
            if r.returncode == 1:              # a real failure wins over any skip
                rc = 1
            elif r.returncode == 2 and rc == 0:
                rc = 2
        return rc
    finally:
        static.terminate()

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "unit"
    sys.exit(unit() if mode == "unit" else browser())
