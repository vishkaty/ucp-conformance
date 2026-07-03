#!/usr/bin/env python3
"""
merchant_checks_01_11_01_23_disc.py — DISC-014 (01-11 + 01-23): every spec and
schema URL advertised in the discovery profile MUST be valid and resolvable
(200 with parseable JSON or valid HTML). overview.md#L74.

This is the one requirement whose truth is a LIVE NETWORK FETCH of the (external)
authority-origin URLs a conformant profile advertises (https://ucp.dev/...). It is
therefore an OPT-IN check, gated on config.discovery.live_url_checks, which is
DELIBERATELY ABSENT from CONTROLLED_CONFIG — so NO run_suite/selftest gate ever
performs a network fetch. It is reference-gated HERMETICALLY by
selfcheck/validate_disc014_check.py, which boots the fixture with --local-spec-urls
(every advertised URL repointed to a LOOPBACK path the fixture serves 200 for) and
--break-spec-url (one URL 404s, the mutant). Loopback is not the network, so the
reference gate stays hermetic while exercising the real check logic.

For a REAL merchant the operator opts in (config.discovery.live_url_checks: true) and
accepts that the check fetches the merchant's external spec/schema URLs.

versions=(2026-01-11, 2026-01-23): DISC-014 exists in both registers with identical
text; the filename carries both version tokens so matrix.py attributes it to both.
04-08 renumbered discovery ids — never graded there (hard versions= lock).

NOTE: imported lazily by merchant_checks.all_checks() — do not import before it.
"""
import sys, pathlib, json, urllib.request, urllib.error, urllib.parse
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, Resp, CLEAN, DEVIATION              # noqa: E402
from merchant_checks import MCheck, _hdr                      # noqa: E402

V01ERA = ("2026-01-11", "2026-01-23")
_URL_KEYS = ("spec", "schema", "config_schema")
_MAX_REDIRECTS = 3
_TIMEOUT = 5


def _collect_urls(node, out):
    """Every spec/schema/config_schema (+ instrument_schemas) URL in the profile."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _URL_KEYS and isinstance(v, str) and "://" in v:
                out.append(v)
            elif k == "instrument_schemas" and isinstance(v, list):
                out.extend(s for s in v if isinstance(s, str) and "://" in s)
            else:
                _collect_urls(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_urls(item, out)


def _resolvable(url):
    """True iff url resolves to a 200 with parseable JSON or HTML, following at most
    _MAX_REDIRECTS redirects, within _TIMEOUT. Any error/timeout/over-redirect = False."""
    seen = url
    for _ in range(_MAX_REDIRECTS + 1):
        req = urllib.request.Request(seen, method="GET", headers={"Accept": "*/*"})
        opener = urllib.request.build_opener(_NoAutoRedirect)
        try:
            with opener.open(req, timeout=_TIMEOUT) as r:
                status, headers, body = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, headers, body = e.code, e.headers, e.read()
        except Exception:
            return False
        if status in (301, 302, 303, 307, 308):
            loc = headers.get("Location")
            if not loc:
                return False
            seen = urllib.parse.urljoin(seen, loc)
            continue
        if status != 200:
            return False
        ctype = (headers.get("Content-Type") or "").lower()
        if "json" in ctype or "html" in ctype:
            return True
        # content-type absent/generic: accept if the body parses as JSON or looks HTML
        try:
            json.loads(body.decode("utf-8", "replace"))
            return True
        except Exception:
            return body.lstrip()[:1] == b"<"
    return False       # exceeded the redirect budget


class _NoAutoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None    # we count/cap redirects ourselves


def f_spec_urls_resolvable(ctx):
    """Fetch the profile, then resolve every advertised spec/schema URL. Returns a
    synthetic Resp summarizing the sweep so the predicate + mutation harness can
    grade it deterministically."""
    p = fetch(ctx.base, "/.well-known/ucp", "GET", None, _hdr())
    if p.status != 200 or not isinstance(p.json, dict):
        return Resp(p.status or 0, {}, b'{"checked":0,"resolved":0,"failures":["profile unavailable"]}')
    urls = []
    _collect_urls(p.json, urls)
    urls = sorted(set(urls))
    failures = [u for u in urls if not _resolvable(u)]
    summary = {"checked": len(urls), "resolved": len(urls) - len(failures),
               "failures": failures}
    return Resp(200, {"Content-Type": "application/json"},
                json.dumps(summary).encode())


def p_all_urls_resolvable(r, ctx):
    """DISC-014: every advertised spec/schema URL MUST resolve. CLEAN iff at least
    one URL was declared and all of them resolved."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    j = r.json
    if not isinstance(j.get("checked"), int) or j["checked"] < 1:
        return DEVIATION
    if j.get("failures"):
        return DEVIATION
    return CLEAN if j.get("resolved") == j["checked"] else DEVIATION


CHECKS_01_11_01_23_DISC = [
    MCheck("discovery.spec_urls_resolvable", ["DISC-014"], "MUST",
           f_spec_urls_resolvable, p_all_urls_resolvable,
           ["status:500", "set:checked=0", "set:resolved=0",
            "set:failures=[\"https://ucp.dev/broken\"]",
            "corrupt-json", "empty"],
           transport="rest", versions=V01ERA,
           cfg_needs=("discovery.live_url_checks",)),
]
