#!/usr/bin/env python3
"""
merchant_checks_04_08_discovery.py — 2026-04-08-scoped behavioral checks for the
DISCOVERY-AND-PROFILE and VERSION-NEGOTIATION areas.

VERSION-LOCK: every DISC/NEG id here names a DIFFERENT requirement in the
2026-01-11/2026-01-23 registers (e.g. DISC-001 there = reverse-domain naming;
NEG-001 there = profile-URI-per-request), so every check carries
versions=("2026-04-08",) and this file's name carries the 04_08 token for
coverage/matrix.py attribution.

Reference target: the controlled fixture in 04-08 mode. The negotiation checks are
config-gated (config.negotiation.*): each key names a platform-profile URL that
makes a fetching business exhibit one negotiation failure. The controlled fixture
recognizes its seeded URLs (see fixtures/merchant/server.py negotiate_platform);
a real merchant needs URLs that genuinely exhibit the failure (e.g. a resolvable
URL that 404s for `unreachable_profile_url`) — without config the checks skip
honestly (not-tested), never fake a verdict.

DISC-001 (profile served over HTTPS) is transport-layer: on a plain-HTTP dev golden
it reports not-tested (INCONCLUSIVE), and its kill proof lives in the TLS harness
gate (selfcheck/validate_tls_check.py: clean on the TLS golden listener, deviation
on an https profile URL with no TLS service). The engine-level mutations below
additionally prove the predicate is live wherever the probe DOES run.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import json, sys, pathlib, urllib.request, urllib.error
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import engine                                                  # noqa: E402
from engine import Resp, fetch, CLEAN, DEVIATION               # noqa: E402
from merchant_checks import MCheck, _hdr, _create_payload      # noqa: E402
from verdict_gate import INCONCLUSIVE                          # noqa: E402
from tls_check_01_11_01_23 import tls_probe                    # noqa: E402

V0408 = ("2026-04-08",)

def _ncfg(ctx):
    return ctx.config.get("negotiation") or {}

def _profile_url(ctx):
    """The business profile URL under the server base (query preserved — gateway
    fixtures route tenants via ?domain=...)."""
    p = urlsplit(ctx.base)
    path = (p.path.rstrip("/") or "") + "/.well-known/ucp"
    return urlunsplit((p.scheme, p.netloc, path, p.query, ""))

# ---- DISC-001: business profile MUST be served over HTTPS ----------------------
def f_profile_https(ctx):
    """TLS probe of the profile URL, serialized as the Resp body so the engine's
    mutation kill-rate applies to the graded fields."""
    return Resp(200, {}, json.dumps(tls_probe(_profile_url(ctx))).encode())

def p_profile_https(r):
    """DISC-001@04-08: an https profile URL with a working TLS service. Any TLS
    version satisfies THIS row (the 1.3-minimum is CHK-051@01-era, a different MUST).
    Plain-HTTP dev goldens are not exercisable -> not-tested, never a deviation."""
    t = r.json if isinstance(r.json, dict) else None
    if t is None:
        return DEVIATION                       # probe record corrupted (kill proof)
    if not t.get("applicable"):
        return INCONCLUSIVE                    # http dev golden -> not-tested
    return CLEAN if t.get("handshake_ok") else DEVIATION

# ---- DISC-002: profile endpoints MUST NOT use redirects (3xx) -------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None                            # surface the 3xx instead of following

def fetch_noredirect(url, headers=None):
    """GET without following redirects (urllib auto-follows; verifiers MUST NOT)."""
    req = urllib.request.Request(url, headers=headers or {})
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=engine._SSL_CTX))
    try:
        with opener.open(req, timeout=10) as r:
            return Resp(r.status, r.getheaders(), r.read())
    except urllib.error.HTTPError as e:        # 3xx/4xx/5xx land here un-followed
        return Resp(e.code, e.headers.items(), e.read())
    except Exception as e:
        return Resp(0, {}, str(e).encode())

def f_profile_get(ctx):
    return fetch_noredirect(_profile_url(ctx))

def p_no_redirect(r):
    """DISC-002@04-08: the profile fetch is answered directly (200 + JSON object);
    any 3xx is the deviation. Other failures (404/5xx) are a different row's problem
    -> inconclusive, so this check never mis-attributes them to redirect policy."""
    if 300 <= r.status < 400:
        return DEVIATION
    if r.status == 200 and isinstance(r.json, dict):
        return CLEAN
    return INCONCLUSIVE

# ---- DISC-003: profile Cache-Control policy -------------------------------------
def _cache_control(r):
    for k, v in r.headers.items():
        if k.lower() == "cache-control":
            return v
    return None

def p_cache_control(r):
    """DISC-003@04-08: Cache-Control present with `public` and max-age >= 60, and
    none of private/no-store/no-cache."""
    if r.status != 200 or not isinstance(r.json, dict):
        return INCONCLUSIVE                    # no profile served -> not this row
    cc = _cache_control(r)
    if not cc:
        return DEVIATION
    directives = [d.strip().lower() for d in cc.split(",")]
    if "public" not in directives:
        return DEVIATION
    if any(b in directives for b in ("private", "no-store", "no-cache")):
        return DEVIATION
    ages = [d for d in directives if d.startswith("max-age=")]
    try:
        if not ages or int(ages[0].split("=", 1)[1]) < 60:
            return DEVIATION
    except ValueError:
        return DEVIATION
    return CLEAN

# ---- DISC-004: reject profile URLs not served over HTTPS ------------------------
def f_http_profile_url(ctx):
    """Otherwise-valid create whose UCP-Agent names a plain-http platform profile."""
    h = _hdr()
    h["UCP-Agent"] = 'profile="http://spck.dev/agent"'
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _create_payload(ctx), h)

def p_rejected_4xx(r):
    """DISC-004@04-08: the request is REJECTED (4xx). The spec's mapping table pins
    invalid_profile_url -> 400, but the cited MUST (overview.md#L1098) only mandates
    rejection, so any 4xx passes (a 2xx acceptance or a 5xx crash deviates)."""
    return CLEAN if 400 <= r.status < 500 else DEVIATION

# ---- DISC-005: service endpoints MUST be valid https URLs -----------------------
def f_profile_plain(ctx):
    return fetch(ctx.base, "/.well-known/ucp", "GET", None, _hdr())

def _service_entries(profile):
    ucp = profile.get("ucp", profile) if isinstance(profile, dict) else {}
    for entries in (ucp.get("services") or {}).values():
        for s in entries if isinstance(entries, list) else []:
            if isinstance(s, dict):
                yield s

def p_endpoints_https(r, ctx):
    """DISC-005@04-08: every declared service endpoint parses as a URL with scheme
    https. Dev tolerance: an http endpoint is tolerated ONLY when the profile itself
    was fetched over http from the SAME host:port (pure loopback dev golden — the
    transport gap is then DISC-001's finding, not a bogus endpoint). An http endpoint
    pointing anywhere else, a non-http(s) scheme, or an unparseable URL deviates."""
    if r.status != 200 or not isinstance(r.json, dict):
        return INCONCLUSIVE
    services = list(_service_entries(r.json))
    if not services:
        return INCONCLUSIVE                    # nothing declared -> nothing to grade
    base = urlsplit(ctx.base)
    for s in services:
        ep = s.get("endpoint")
        if not isinstance(ep, str):
            return DEVIATION
        u = urlsplit(ep)
        if u.scheme == "https" and u.netloc:
            continue
        if (u.scheme == "http" and base.scheme == "http"
                and u.netloc == base.netloc):
            continue                           # loopback dev golden tolerance
        return DEVIATION
    return CLEAN

# ---- NEG-001..004: negotiation error mapping (config-gated probe URLs) ----------
def _neg_probe(ctx, cfg_key):
    """Otherwise-valid create with UCP-Agent naming the config-supplied platform
    profile URL that exhibits one negotiation failure."""
    h = _hdr()
    h["UCP-Agent"] = f'profile="{_ncfg(ctx).get(cfg_key)}"'
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _create_payload(ctx), h)

def f_neg_unsupported_version(ctx):
    return _neg_probe(ctx, "unsupported_version_profile_url")

def p_version_unsupported(r):
    """NEG-001@04-08: HTTP 422 with a version_unsupported transport-error body.
    DISCREPANCY (register note/AMB): the official suite's protocol_test asserts 400,
    but it is pinned to spec 2026-01-23; the 04-08 spec maps version_unsupported to
    422. The spec is authoritative — a 400 here grades as a deviation (the flag),
    never a silent pass."""
    if r.status != 422 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("code") == "version_unsupported" else DEVIATION

def f_neg_incompatible_caps(ctx):
    return _neg_probe(ctx, "incompatible_caps_profile_url")

def p_capabilities_incompatible(r):
    """NEG-002@04-08: HTTP 200 with the error in the UCP body (ucp.status=error +
    an error message code=capabilities_incompatible). A 4xx here is WRONG (AMB-006):
    negotiation failure is a business outcome, not a transport error."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    if (r.json.get("ucp") or {}).get("status") != "error":
        return DEVIATION
    msgs = r.json.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return DEVIATION
    return CLEAN if any(isinstance(m, dict) and m.get("type") == "error"
                        and m.get("code") == "capabilities_incompatible"
                        for m in msgs) else DEVIATION

def f_neg_unreachable(ctx):
    return _neg_probe(ctx, "unreachable_profile_url")

def p_profile_unreachable(r):
    """NEG-003@04-08: a resolved-but-unfetchable platform profile -> HTTP 424 with
    a profile_unreachable transport-error body."""
    if r.status != 424 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("code") == "profile_unreachable" else DEVIATION

def f_neg_malformed(ctx):
    return _neg_probe(ctx, "malformed_profile_url")

def p_profile_malformed(r):
    """NEG-004@04-08: a fetched-but-invalid platform profile (not JSON / violates
    schema) -> HTTP 422 with a profile_malformed transport-error body."""
    if r.status != 422 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("code") == "profile_malformed" else DEVIATION

_SVC_BAD = '{"dev.ucp.shopping":[{"transport":"rest","endpoint":%s}]}'

CHECKS_04_08_DISCOVERY = [
    MCheck("discovery.profile_https", ["DISC-001"], "MUST", f_profile_https,
           p_profile_https,
           ["set:handshake_ok=false", "corrupt-json", "empty"],
           transport="rest", versions=V0408),
    MCheck("discovery.profile_no_redirect", ["DISC-002"], "MUST NOT", f_profile_get,
           p_no_redirect,
           ["status:301", "status:302", "status:307", "status:308"],
           transport="rest", versions=V0408),
    MCheck("discovery.profile_cache_control", ["DISC-003"], "MUST", f_profile_get,
           p_cache_control,
           ["hdrop:Cache-Control",
            "hset:Cache-Control=private, max-age=300",
            "hset:Cache-Control=no-store",
            "hset:Cache-Control=public, no-cache, max-age=300",
            "hset:Cache-Control=public, max-age=30",
            "hset:Cache-Control=public"],
           transport="rest", versions=V0408),
    MCheck("discovery.reject_http_profile_url", ["DISC-004"], "MUST",
           f_http_profile_url, p_rejected_4xx,
           ["status:200", "status:201", "status:303", "status:502"],
           needs=("product",), transport="rest", versions=V0408),
    MCheck("discovery.endpoints_https", ["DISC-005"], "MUST", f_profile_plain,
           p_endpoints_https,
           ["set:services=" + _SVC_BAD % '"notaurl"',
            "set:services=" + _SVC_BAD % '"http://evil.example.com/api"',
            "set:services=" + _SVC_BAD % '"ftp://files.example.com/api"',
            "set:services=" + _SVC_BAD % 'null'],
           transport="rest", versions=V0408),
    MCheck("negotiation.version_unsupported_422", ["NEG-001"], "MUST",
           f_neg_unsupported_version, p_version_unsupported,
           ["status:400", "status:200", "set:code=\"unsupported_version\"",
            "corrupt-json", "empty"],
           needs=("product",), cfg_needs=("negotiation.unsupported_version_profile_url",),
           transport="rest", versions=V0408),
    MCheck("negotiation.capabilities_incompatible_200", ["NEG-002"], "MUST",
           f_neg_incompatible_caps, p_capabilities_incompatible,
           ["status:422", "status:400",
            "set:ucp={\"version\":\"2026-04-08\"}",
            "set:messages=[]", "drop:messages", "corrupt-json"],
           needs=("product",), cfg_needs=("negotiation.incompatible_caps_profile_url",),
           transport="rest", versions=V0408),
    MCheck("negotiation.profile_unreachable_424", ["NEG-003"], "MUST",
           f_neg_unreachable, p_profile_unreachable,
           ["status:200", "status:400", "set:code=\"unreachable\"",
            "corrupt-json", "empty"],
           needs=("product",), cfg_needs=("negotiation.unreachable_profile_url",),
           transport="rest", versions=V0408),
    MCheck("negotiation.profile_malformed_422", ["NEG-004"], "MUST",
           f_neg_malformed, p_profile_malformed,
           ["status:200", "status:400", "set:code=\"malformed\"",
            "corrupt-json", "empty"],
           needs=("product",), cfg_needs=("negotiation.malformed_profile_url",),
           transport="rest", versions=V0408),
]
