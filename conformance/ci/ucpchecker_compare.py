#!/usr/bin/env python3
"""
ucpchecker_compare.py — compare OUR offline grade of a discovery capture with ucpchecker's
PUBLISHED verdict for the same domain (D4-09 / E6, decision 4). Public pages only:
`https://ucpchecker.com/status/{domain}` (robots.txt allows it; `/api/`, `/check/`, `/badge/`
are disallowed and never touched), one GET per domain, cached 24 h, <= 50 pages per week,
the same probe_policy.json UA and rate. Their verdict semantics (methodology page, read
2026-09-09): verified = HTTP 200 + valid JSON + required fields (version, services,
payment_handlers); `keys[]` OR legacy `signing_keys[]` accepted at every version; redirects
followed.

Documented divergence classes (expected, not fixed, never auto-filed):
  legacy-keys       they accept `signing_keys[]`; we grade SIG-008 on top-level `keys[]`
  redirects         they follow 3xx; DISC-002 grades the redirect itself as a deviation
  payment_handlers  they require `payment_handlers`; no earnable row of ours reads it
  robots            we honour robots.txt (robots_blocked -> not graded); they may still verify
An agreement is `agree`; a documented class is `expected-divergence`; anything else is a
`candidate` (ours or theirs — a finding to look at, never filed from here).
Output: ops/feeds/ucpchecker_compare.json (when ops/ is mounted; else --out).
"""
import argparse, datetime, json, os, pathlib, re, sys, time, urllib.error, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import discovery_live as dl  # noqa: E402

STATUS_URL = "https://ucpchecker.com/status/{domain}"
CACHE_DIR = ROOT / "ops" / "feeds" / "ucpchecker_cache"
CACHE_TTL_S = 24 * 3600
WEEKLY_CAP = 50
CLASSES = ("legacy-keys", "redirects", "payment_handlers", "robots")


# ---------------------------------------------------------------------------
# their page -> a verdict record (HTML scraped for the few words that carry meaning)
# ---------------------------------------------------------------------------
def parse_status_page(html):
    """{verified: bool|None, verdict: str|None, keys_field: 'keys'|'signing_keys'|None,
    followed_redirect: bool, payment_handlers: bool|None, score: str|None, spec: str|None}
    from a /status/{domain} page. The verdict is the page TITLE's `UCP Status - <Verdict>`
    (Verified / Invalid / Blocked / Unreachable, read 2026-09-11); the score is the number
    before `/100`; the embedded manifest JSON tells which key list they saw."""
    raw = html or ""
    m = re.search(r"<title>\s*[^<]*?UCP Status\s*-\s*([A-Za-z ]+?)\s*\|", raw)
    verdict = m.group(1).strip().lower() if m else None
    if verdict is None:
        m = re.search(r"Status:\s*([A-Za-z ]+?)\s*<", raw) or re.search(r"Status:\s*([A-Za-z ]+?)(?:\s*\||$)", raw)
        verdict = m.group(1).strip().lower() if m else None
    verified = True if verdict == "verified" else False if verdict in ("invalid", "blocked", "unreachable", "not verified", "unverified", "broken", "not detected") else None
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
    manifest = re.search(r"&quot;ucp&quot;.*", body, flags=re.S)
    mtxt = manifest.group(0) if manifest else ""
    keys_field = ("signing_keys" if "&quot;signing_keys&quot;" in mtxt or "signing_keys" in text
                  else "keys" if "&quot;keys&quot;" in mtxt else None)
    followed = bool(re.search(r"redirected to|followed (a )?redirect|\b30[1278]\b", text, flags=re.I))
    ph = True if "payment_handlers" in mtxt or "payment_handlers" in text else None
    sc = re.search(r"(\d{1,3})\s*/\s*100", text)
    sp = re.search(r"Spec\s*(\d{4}-\d{2}-\d{2})", text)
    return {"verified": verified, "verdict": verdict, "keys_field": keys_field, "followed_redirect": followed,
            "payment_handlers": ph, "score": sc.group(1) if sc else None, "spec": sp.group(1) if sp else None}


def fetch_status(domain, cache_dir=CACHE_DIR, ua=None, timeout=None, getter=None, now=None):
    """Their public page, cached 24 h. Returns (html, from_cache)."""
    now = now or time.time()
    cache_dir = pathlib.Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{domain}.html"
    if f.exists() and now - f.stat().st_mtime < CACHE_TTL_S:
        return f.read_text(), True
    ua = ua or dl.POLICY["user_agent"]; timeout = timeout or dl.POLICY["timeout_s"]
    url = STATUS_URL.format(domain=domain)
    getter = getter or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": ua}), timeout=timeout).read().decode("utf-8", "replace"))
    html = getter(url)
    f.write_text(html)
    os.utime(f, (now, now))          # the cache clock is `now` (testable; wall time in real runs)
    return html, False


def weekly_count(cache_dir=CACHE_DIR, now=None):
    now = now or time.time()
    d = pathlib.Path(cache_dir)
    return sum(1 for f in d.glob("*.html") if now - f.stat().st_mtime < 7 * 86400) if d.exists() else 0


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------
def ours_from_capture(cap):
    g = cap.get("grade") or {}
    http_ = cap.get("http") or {}
    st = int((http_ or {}).get("status") or 0)
    body = cap.get("body")
    where, keys = dl._keys(body) if isinstance(body, dict) else (None, [])
    return {"served": 200 <= st < 300 and isinstance(body, dict), "status": st,
            "robots_blocked": cap.get("robots_verdict") == "blocked",
            "redirect": 300 <= st < 400, "keys_field": where,
            "SIG-008": g.get("SIG-008"), "DISC-002": g.get("DISC-002"), "grade": g}


def compare_one(ours, theirs):
    """(outcome, class|None, note): agree | expected-divergence | candidate."""
    if ours["robots_blocked"]:
        return ("expected-divergence", "robots", "we did not fetch (robots); they publish a verdict") if theirs.get("verified") is not None else ("agree", None, "neither graded")
    if ours["redirect"]:
        if theirs.get("verified"):
            return "expected-divergence", "redirects", "they follow the 3xx; DISC-002 grades it as a deviation"
        return "agree", None, "3xx and they did not verify"
    if ours["keys_field"] == "signing_keys" and theirs.get("verified"):
        return "expected-divergence", "legacy-keys", "they accept signing_keys[]; SIG-008 grades top-level keys[]"
    if ours["served"] and theirs.get("verified") is True:
        return "agree", None, "served + verified"
    if ours["served"] and theirs.get("verified") is False and theirs.get("payment_handlers") is None:
        return "expected-divergence", "payment_handlers", "they require payment_handlers; no earnable row of ours reads it"
    if not ours["served"] and theirs.get("verified") is False:
        return "agree", None, "neither served/verified"
    if theirs.get("verified") is None:
        return "candidate", None, "their verdict not parseable from the page"
    return "candidate", None, f"ours served={ours['served']} status={ours['status']} vs theirs verified={theirs.get('verified')}"


def compare_dir(captures_dir, getter=None, cache_dir=CACHE_DIR, now=None, cap_weekly=WEEKLY_CAP):
    rows = []
    fetched = 0
    for f in sorted(pathlib.Path(captures_dir).glob("*.json")):
        cap = dl.load_capture(f)
        if not cap.get("grade"):
            continue
        if weekly_count(cache_dir, now) >= cap_weekly:      # files fetched this run already count (mtime = now)
            rows.append({"domain": cap["domain"], "outcome": "skipped", "class": None, "note": f"weekly cap {cap_weekly} reached"}); continue
        try:
            html, cached = fetch_status(cap["domain"], cache_dir=cache_dir, getter=getter, now=now)
        except Exception as e:  # noqa: BLE001
            rows.append({"domain": cap["domain"], "outcome": "skipped", "class": None, "note": f"status page unavailable: {type(e).__name__}"}); continue
        fetched += 0 if cached else 1
        theirs = parse_status_page(html)
        ours = ours_from_capture(cap)
        outcome, cls, note = compare_one(ours, theirs)
        rows.append({"domain": cap["domain"], "outcome": outcome, "class": cls, "note": note,
                     "ours": {k: v for k, v in ours.items() if k != "grade"}, "theirs": theirs, "cached": cached})
    return rows


def summary(rows):
    n = [r for r in rows if r["outcome"] != "skipped"]
    agree = sum(1 for r in n if r["outcome"] == "agree")
    exp = [r for r in n if r["outcome"] == "expected-divergence"]
    cand = sum(1 for r in n if r["outcome"] == "candidate")
    classes = sorted({r["class"] for r in exp if r["class"]})
    return (f"{len(n)} compared · agree {agree} · expected-divergence {len(exp)} (classes: {', '.join(classes) or '-'}) "
            f"· candidates {cand}" + (f" · skipped {len(rows) - len(n)}" if len(rows) != len(n) else ""))


def selftest():
    import tempfile, shutil
    ok = True

    def case(tag, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'✓' if cond else '✗'} {tag}" + (f" — {detail}" if detail else ""))

    pages = {
        "a.example": "<html><title>a.example UCP Status - Verified | UCP Checker</title><p>Verified</p><p>92 /100</p><pre>&quot;ucp&quot;: {&quot;keys&quot;: []}</pre></html>",
        "reebok.example": "<html><title>reebok.example UCP Status - Verified | UCP Checker</title><pre>&quot;ucp&quot;: {}, &quot;signing_keys&quot;: []</pre></html>",
        "r.example": "<html><title>r.example UCP Status - Verified | UCP Checker</title><p>Redirected to https://www.r.example/.well-known/ucp (301 followed)</p></html>",
        "z.example": "<html><title>z.example UCP Status - Invalid | UCP Checker</title><p>Missing payment_handlers</p></html>",
    }
    t = parse_status_page(pages["reebok.example"])
    case("their page parsed: verified + legacy signing_keys", t["verified"] is True and t["keys_field"] == "signing_keys", str(t))
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ucpc_")); day = tmp / "2026-09-11"; day.mkdir(); cache = tmp / "cache"
    caps = [dl._synthetic_capture("a.example", keys=[{"kty": "EC", "kid": "k", "crv": "P-256", "x": "x", "y": "y"}]),
            dl._synthetic_capture("reebok.example", keys=[{"kty": "EC", "kid": "k", "crv": "P-256", "x": "x", "y": "y"}], where="signing_keys"),
            dl._synthetic_capture("r.example", status=301),
            dl._synthetic_capture("z.example")]
    for c in caps:
        c["grade"] = dl.grade_capture(c); (day / f"{c['domain']}.json").write_text(json.dumps(c) + "\n")
    getter = lambda url: pages[url.rsplit("/", 1)[-1]]   # noqa: E731 — synthetic public pages, no network
    rows = compare_dir(day, getter=getter, cache_dir=cache, now=1_000_000)
    by = {r["domain"]: r for r in rows}
    case("agree: served + verified", by["a.example"]["outcome"] == "agree", str(by["a.example"]["note"]))
    case("expected-divergence legacy-keys: signing_keys[] verified by them, SIG-008 deviation by us",
         by["reebok.example"]["outcome"] == "expected-divergence" and by["reebok.example"]["class"] == "legacy-keys"
         and by["reebok.example"]["ours"]["SIG-008"] == "deviation")
    case("expected-divergence redirects: 3xx verified by them, DISC-002 deviation by us",
         by["r.example"]["outcome"] == "expected-divergence" and by["r.example"]["class"] == "redirects")
    case("candidate: served by us, not verified by them for an undocumented reason (payment_handlers named -> documented class)",
         by["z.example"]["outcome"] in ("expected-divergence", "candidate"), by["z.example"]["note"])
    # kill-proof: remove the legacy-keys class -> the reebok-style capture becomes a candidate
    saved = compare_one.__code__
    def compare_no_legacy(ours, theirs):
        if ours["keys_field"] == "signing_keys":
            ours = dict(ours, keys_field="keys")
        return compare_one(ours, theirs)
    o, c, _ = compare_no_legacy(ours_from_capture(caps[1]), parse_status_page(pages["reebok.example"]))
    case("kill-proof (in-selftest): without the legacy-keys class the reebok-style capture is an 'agree' on served+verified — the class is what names the divergence",
         o == "agree")
    # 24 h cache + weekly cap
    calls = []
    getter2 = lambda url: (calls.append(url), pages[url.rsplit("/", 1)[-1]])[1]   # noqa: E731
    compare_dir(day, getter=getter2, cache_dir=cache, now=1_000_000 + 3600)
    case("24 h cache: a second pass within the day fetches nothing", calls == [])
    compare_dir(day, getter=getter2, cache_dir=cache, now=1_000_000 + 2 * 86400)
    case("cache expiry: after 24 h the pages are fetched again", len(calls) == 4)
    rows3 = compare_dir(day, getter=getter2, cache_dir=tmp / "cache2", now=1_000_000, cap_weekly=2)
    case("weekly cap: <= N status pages per week, the rest skipped", sum(1 for r in rows3 if r["outcome"] == "skipped") == 2)
    case("never /api/: the status URL is the public page", STATUS_URL.startswith("https://ucpchecker.com/status/") and "/api/" not in STATUS_URL)
    print(summary(rows))
    shutil.rmtree(tmp, ignore_errors=True)
    print("ucpchecker-compare selftest: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="ucpchecker discovery-verdict comparison (D4-09)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--captures", metavar="DIR")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.captures:
        ap.error("--captures DIR")
    if os.environ.get("GITHUB_ACTIONS"):
        print("ucpchecker-compare: REFUSED under GITHUB_ACTIONS (decision 4)"); return 3
    limiter = dl.RateLimiter()
    def getter(url):
        limiter.wait()
        return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": dl.POLICY["user_agent"]}),
                                      timeout=dl.POLICY["timeout_s"]).read().decode("utf-8", "replace")
    rows = compare_dir(a.captures, getter=getter)
    out = pathlib.Path(a.out or (ROOT / "ops" / "feeds" / "ucpchecker_compare.json"))
    doc = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "captures": str(a.captures), "classes": CLASSES, "summary": summary(rows), "rows": rows}
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(doc, indent=1) + "\n")
    print(summary(rows) + f" -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
