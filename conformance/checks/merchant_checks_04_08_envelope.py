#!/usr/bin/env python3
"""
merchant_checks_04_08_envelope.py — 2026-04-08 OVERVIEW-area profile/envelope duties
(the needs-receiver tier of overview.md: reverse-domain naming, spec-URL authority
binding, versioning discipline, the cross-cutting `ucp` response envelope, and
response capability selection driven by REAL platform-profile negotiation).

Version scoping: the OVR-* ids exist ONLY in the 2026-04-08 register (the 01-era
registers row these duties as DISC-*/NEG-* with different text), so every check is
version-locked (versions=V0408) and the file carries the 04_08 name token.

Receiver surfaces exercised here:
  * PROFILE duties (OVR-001/003/009/010) — served at /.well-known/ucp; graded on
    the discovered profile document plus live fetches of supported_versions URIs.
  * ENVELOPE duties (OVR-004/005/008) — graded across representative responses of
    every capability the merchant declares (checkout always; cart/catalog when
    declared), because the MUSTs bind EVERY response.
  * NEGOTIATION-DRIVEN capability selection (OVR-005 intersection / OVR-012
    orphan-extension exclusion) — the suite serves a LOOPBACK platform profile
    with a deliberately narrowed capability set (webhook_harness pattern) and
    asserts the response's ucp.capabilities reflect the intersection rules.
    Config-gated on negotiation.harness (the merchant can fetch a loopback
    platform profile — the same harness convention the webhook checks document).

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import sys, re, json, pathlib
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, Resp, CLEAN, DEVIATION, INCONCLUSIVE   # noqa: E402
from merchant_checks import MCheck, _hdr                          # noqa: E402

V0408 = ("2026-04-08",)

# shopping/types/reverse_domain_name.json (pinned): the collision-safe identifier
# grammar every capability/service name must satisfy.
_RDN = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$")

def _ucp_of(doc):
    """The UCP profile body of a served document (enveloped or flat)."""
    return doc.get("ucp", doc) if isinstance(doc, dict) else {}

def _profile_resp(ctx):
    """The DISCOVERED profile as a Resp golden (mutations inject profile defects)."""
    return Resp(200, {"Content-Type": "application/json"},
                json.dumps(_ucp_of(ctx.profile)).encode())

# ---- OVR-001: reverse-domain naming of every advertised capability/service ------
def p_reverse_domain_names(r):
    """OVR-001: 'All capability and service names MUST use the format
    {reverse-domain}.{service}.{capability}' — every key of the profile's
    services/capabilities registries satisfies the reverse-domain grammar
    (reverse_domain_name.json) with at least a reverse-domain (>=2 labels) plus a
    service segment (>=3 labels total; capability names carry a capability
    segment on top of that, but multi-label reverse-domains make a fixed higher
    bound over-strict, so >=3 is the honest floor for both registries)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    ucp = _ucp_of(r.json)
    caps = ucp.get("capabilities")
    svcs = ucp.get("services")
    if not isinstance(caps, dict) or not caps or not isinstance(svcs, dict) or not svcs:
        return DEVIATION
    for name in list(caps) + list(svcs):
        if not isinstance(name, str) or not _RDN.match(name) \
           or len(name.split(".")) < 3:
            return DEVIATION
    return CLEAN

# ---- OVR-003: spec/schema URL origin matches the namespace authority ------------
def _authority(name):
    """The namespace authority of a reverse-domain name: the first two labels
    reversed (overview.md Spec URL Binding table: dev.ucp.* -> ucp.dev,
    com.example.* -> example.com)."""
    parts = (name or "").split(".")
    return f"{parts[1]}.{parts[0]}" if len(parts) >= 2 else None

def p_url_authority(r):
    """OVR-003: for every advertised capability/service entry, the origin of its
    spec/schema URLs matches the authority derived from the entry's
    reverse-domain name. Entries without such URLs contribute nothing; the
    services registry always carries them (spec/schema are REQUIRED there), so
    the check can never pass vacuously on a conformant profile."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    ucp = _ucp_of(r.json)
    svcs = ucp.get("services")
    caps = ucp.get("capabilities")
    if not isinstance(svcs, dict) or not svcs:
        return DEVIATION
    checked = 0
    for registry in (svcs, caps if isinstance(caps, dict) else {}):
        for name, entries in registry.items():
            want = _authority(name)
            if not want:
                return DEVIATION
            for e in entries if isinstance(entries, list) else []:
                for k in ("spec", "schema"):
                    u = (e or {}).get(k)
                    if not isinstance(u, str) or "://" not in u:
                        continue
                    checked += 1
                    p = urlsplit(u)
                    if p.scheme != "https" or (p.hostname or "").lower() != want:
                        return DEVIATION
    return CLEAN if checked else DEVIATION      # a profile with no URLs at all: unverifiable

# ---- OVR-010: dated YYYY-MM-DD versions only ------------------------------------
_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def p_version_dated(r):
    """OVR-010: the profile `version` is a dated YYYY-MM-DD release and no
    supported_versions key is a non-date string (e.g. 'draft')."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    ucp = _ucp_of(r.json)
    v = ucp.get("version")
    if not isinstance(v, str) or not _DATED.match(v):
        return DEVIATION
    sv = ucp.get("supported_versions")
    if sv is not None:
        if not isinstance(sv, dict):
            return DEVIATION
        if any(not isinstance(k, str) or not _DATED.match(k) for k in sv):
            return DEVIATION
    return CLEAN

# ---- OVR-009: leaf profiles carry no supported_versions -------------------------
def f_leaf_profile(ctx):
    """Follow the FIRST supported_versions URI from the live primary profile and
    return the leaf profile response (config discovery.supported_versions asserts
    the merchant publishes the map, so absence is a deviation, not a skip)."""
    primary = fetch(ctx.base, "/.well-known/ucp", "GET")
    sv = _ucp_of(primary.json if isinstance(primary.json, dict) else {}) \
        .get("supported_versions")
    if not isinstance(sv, dict) or not sv:
        return Resp(0, {}, b'{"probe":"primary profile has no supported_versions map"}')
    uri = sv[sorted(sv)[0]]
    if not isinstance(uri, str) or "://" not in uri:
        return Resp(0, {}, b'{"probe":"supported_versions value is not a URI"}')
    p = urlsplit(uri)
    return fetch(f"{p.scheme}://{p.netloc}", p.path + (f"?{p.query}" if p.query else ""),
                 "GET")

def p_leaf_no_supported_versions(r):
    """OVR-009: 'Version-specific profiles are leaf documents ... and MUST NOT
    contain a supported_versions field.' The leaf must still BE a profile
    (version present) — a 404/garbage leaf cannot pass by absence."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    ucp = _ucp_of(r.json)
    if not isinstance(ucp.get("version"), str):
        return DEVIATION
    return DEVIATION if ("supported_versions" in ucp
                         or "supported_versions" in r.json) else CLEAN

# ---- OVR-004: the ucp envelope on EVERY response --------------------------------
def _envelope_ok(r):
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return False
    ucp = r.json.get("ucp")
    if not isinstance(ucp, dict) or not isinstance(ucp.get("version"), str):
        return False
    caps = ucp.get("capabilities")
    return bool(caps) and isinstance(caps, (dict, list))

def f_envelope_sweep(ctx):
    """One representative response per declared capability family: checkout create
    (always), cart create and catalog search (when declared). Returns the FIRST
    response missing the envelope, else the last — so the predicate grades a real
    offender when one exists and mutations act on a real response otherwise."""
    body = {"line_items": [{"item": {"id": ctx.product_id}, "quantity": 1}]}
    responses = [fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                       body, _hdr())]
    if "dev.ucp.shopping.cart" in ctx.capabilities:
        responses.append(fetch(ctx.shopping_endpoint, "/carts", "POST", body, _hdr()))
    if "dev.ucp.shopping.catalog.search" in ctx.capabilities:
        q = (ctx.config.get("catalog") or {}).get("paginated_query") or "*"
        responses.append(fetch(ctx.shopping_endpoint, "/catalog/search", "POST",
                               {"query": q}, _hdr()))
    return next((r for r in responses if not _envelope_ok(r)), responses[-1])

def p_envelope_every_response(r):
    """OVR-004: 'Businesses MUST include the ucp field in every response
    containing: version ... capabilities' — graded across the swept responses
    (the fetch returns any offender)."""
    return CLEAN if _envelope_ok(r) else DEVIATION

# ---- OVR-008: REST responses use Content-Type application/json ------------------
def f_create_plain(ctx):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 {"line_items": [{"item": {"id": ctx.product_id}, "quantity": 1}]},
                 _hdr())

def p_content_type_json(r):
    """OVR-008: 'Requests and responses MUST use application/json' — graded on the
    response media type (the suite's own requests already comply)."""
    if r.status not in (200, 201):
        return DEVIATION
    ctype = next((v for k, v in (r.headers or {}).items()
                  if k.lower() == "content-type"), "")
    return CLEAN if ctype.split(";")[0].strip().lower() == "application/json" \
        else DEVIATION

# ---- OVR-005 (relevance half): capabilities in the response fit the operation ---
_CHECKOUT_IRRELEVANT = ("dev.ucp.shopping.catalog", "dev.ucp.shopping.cart")

def p_caps_relevant(r, ctx):
    """OVR-005: 'Businesses MUST include in ucp.capabilities only the capabilities
    that are (1) in the negotiated intersection AND (2) relevant to this
    response's operation type.' On a checkout response: the checkout root
    capability is declared, every declared capability is one the profile
    advertises, and no catalog/cart capability (irrelevant to a checkout
    operation) appears."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    caps = (r.json.get("ucp") or {}).get("capabilities")
    if not isinstance(caps, dict) or "dev.ucp.shopping.checkout" not in caps:
        return DEVIATION
    for name in caps:
        if name not in ctx.capabilities:
            return DEVIATION                # outside the advertised (max) set
        if name == "dev.ucp.shopping.cart" or \
           name.startswith("dev.ucp.shopping.catalog"):
            return DEVIATION                # irrelevant to a checkout operation
    return CLEAN

# ---- OVR-005/OVR-012 (negotiation half): intersection + orphan extensions -------
def _harness_create(ctx, caps, path="/checkout-sessions", body=None):
    """Create with UCP-Agent naming a LOOPBACK platform profile whose capability
    set is `caps` — the merchant MUST fetch it and negotiate the intersection."""
    from webhook_harness import Harness0408
    with Harness0408(capabilities=caps) as h:
        hdrs = _hdr()
        hdrs["UCP-Agent"] = f'profile="{h.profile_url}"'
        return fetch(ctx.shopping_endpoint, path, "POST",
                     body or {"line_items": [{"item": {"id": ctx.product_id},
                                              "quantity": 1}]}, hdrs)

def f_intersection_no_discount(ctx):
    """Baseline (default agent, no negotiation narrowing): the checkout response
    declares the discount extension. Then negotiate against a platform profile
    WITHOUT discount — the intersection excludes it. Returns the negotiated
    response; a baseline that never declares discount is a probe error (the
    scenario would be vacuous)."""
    base = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 {"line_items": [{"item": {"id": ctx.product_id}, "quantity": 1}]},
                 _hdr())
    base_caps = ((base.json or {}).get("ucp") or {}).get("capabilities") \
        if isinstance(base.json, dict) else None
    if not isinstance(base_caps, dict) or "dev.ucp.shopping.discount" not in base_caps:
        return Resp(0, {}, b'{"probe":"baseline checkout response never declares the '
                           b'discount capability; the intersection scenario would be '
                           b'vacuous"}')
    return _harness_create(ctx, ["dev.ucp.shopping.checkout", "dev.ucp.shopping.order"])

def p_intersection_excludes(r, ctx):
    """OVR-005 clause 1: with the platform profile advertising NO discount
    capability, the negotiated intersection excludes it — the response's
    ucp.capabilities carry checkout but NOT discount."""
    if r.status == 0:
        return INCONCLUSIVE                 # scenario preconditions unmet: honest skip
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    caps = (r.json.get("ucp") or {}).get("capabilities")
    if not isinstance(caps, dict) or "dev.ucp.shopping.checkout" not in caps:
        return DEVIATION
    return DEVIATION if "dev.ucp.shopping.discount" in caps else CLEAN

def f_orphan_extension(ctx):
    """A platform profile advertising ONLY the discount extension (its parent,
    checkout, absent). The RAW intersection is non-empty ({discount} — both sides
    declare it), so ONLY extension validation (negotiation step 3) can empty it:
    excluding the orphan leaves no compatible capability and the create MUST
    yield the capabilities_incompatible outcome. A merchant that skips step 3
    would find {discount} 'compatible' and proceed. Baseline precondition: the
    same create under a checkout-carrying platform profile succeeds (so the
    failure below is attributable to the orphan exclusion, not to loopback
    negotiation being broken)."""
    ok = _harness_create(ctx, ["dev.ucp.shopping.checkout",
                               "dev.ucp.shopping.discount",
                               "dev.ucp.shopping.order"])
    if ok.status not in (200, 201) or not isinstance(ok.json, dict) \
       or not ok.json.get("id"):
        return Resp(0, {}, b'{"probe":"baseline create under a checkout+discount '
                           b'platform profile did not succeed; the orphan scenario '
                           b'cannot be attributed", "vacuous": true}')
    return _harness_create(ctx, ["dev.ucp.shopping.discount"])

def p_orphan_excluded(r, ctx):
    """OVR-012: 'Extensions without their parent capability in the intersection
    MUST be excluded' — with the orphaned discount excluded, no compatible
    capability remains and the response is the capabilities_incompatible error
    envelope (HTTP 200, ucp.status:error — overview.md Error Handling), never a
    created checkout."""
    if r.status == 0:
        return INCONCLUSIVE                 # scenario preconditions unmet: honest skip
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    if (r.json.get("ucp") or {}).get("status") != "error":
        return DEVIATION                    # the orphan was treated as compatible
    return CLEAN if any(isinstance(m, dict)
                        and m.get("code") == "capabilities_incompatible"
                        for m in r.json.get("messages") or []) else DEVIATION

_CAPS_WITH_CATALOG = json.dumps({
    "dev.ucp.shopping.checkout": [{"version": "2026-04-08"}],
    "dev.ucp.shopping.catalog.search": [{"version": "2026-04-08"}]})
_CAPS_WITH_DISCOUNT = json.dumps({
    "dev.ucp.shopping.checkout": [{"version": "2026-04-08"}],
    "dev.ucp.shopping.discount": [
        {"version": "2026-04-08", "extends": "dev.ucp.shopping.checkout"}]})
_CAPS_UNADVERTISED = json.dumps({
    "dev.ucp.shopping.checkout": [{"version": "2026-04-08"}],
    "com.evil.shopping.tracker": [{"version": "2026-04-08"}]})
_BAD_NAME_CAPS = json.dumps({"Checkout": [{"version": "2026-04-08"}]})
_BAD_NAME_SVCS = json.dumps({"shopping_service": [{"version": "2026-04-08"}]})
_EVIL_SVC = json.dumps({"dev.ucp.shopping": [
    {"version": "2026-04-08", "transport": "rest",
     "endpoint": "https://merchant.example/ucp",
     "spec": "https://evil.example/spec",
     "schema": "https://ucp.dev/schema.json"}]})

CHECKS_04_08_ENVELOPE = [
    MCheck("profile.reverse_domain_names", ["OVR-001"], "MUST",
           _profile_resp, p_reverse_domain_names,
           ["drop:capabilities", "set:capabilities={}",
            f"set:capabilities={_BAD_NAME_CAPS}",
            f"set:services={_BAD_NAME_SVCS}", "corrupt-json"],
           transport="rest", versions=V0408),
    MCheck("profile.spec_url_authority", ["OVR-003"], "MUST",
           _profile_resp, p_url_authority,
           ["drop:services", f"set:services={_EVIL_SVC}", "corrupt-json"],
           transport="rest", versions=V0408),
    MCheck("profile.version_dated", ["OVR-010"], "MUST NOT",
           _profile_resp, p_version_dated,
           ["drop:version", "set:version=\"draft\"",
            "set:supported_versions={\"draft\":\"https://x.example/p\"}",
            "corrupt-json"],
           transport="rest", versions=V0408),
    MCheck("profile.leaf_no_supported_versions", ["OVR-009"], "MUST NOT",
           f_leaf_profile, p_leaf_no_supported_versions,
           # `ucp?.version` — the leaf may nest under a `ucp` envelope (the
           # 2026-01-11 generation does) or be flat; the optional segment reaches
           # the version member in both shapes (engine._walk)
           ["status:404", "drop:ucp?.version",
            "set:supported_versions={\"2026-01-11\":\"https://x.example/p\"}",
            "corrupt-json", "empty"],
           cfg_needs=("discovery.supported_versions",),
           transport="rest", versions=V0408),
    MCheck("response.envelope_every_response", ["OVR-004"], "MUST",
           f_envelope_sweep, p_envelope_every_response,
           ["status:500", "drop:ucp", "set:ucp={\"version\":\"2026-04-08\"}",
            "set:ucp.capabilities={}", "corrupt-json", "empty"],
           needs=("product",), transport="rest", versions=V0408),
    MCheck("response.content_type_json", ["OVR-008"], "MUST",
           f_create_plain, p_content_type_json,
           ["status:500", "hdrop:Content-Type", "hset:Content-Type=text/plain",
            "hset:Content-Type=text/html; charset=utf-8"],
           needs=("product",), transport="rest", versions=V0408),
    MCheck("response.capabilities_relevant", ["OVR-005"], "MUST",
           f_create_plain, p_caps_relevant,
           ["status:500", "drop:ucp", "set:ucp.capabilities={}",
            f"set:ucp.capabilities={_CAPS_WITH_CATALOG}",
            f"set:ucp.capabilities={_CAPS_UNADVERTISED}", "corrupt-json"],
           needs=("product",), transport="rest", versions=V0408),
    MCheck("negotiation.intersection_capabilities", ["OVR-005"], "MUST",
           f_intersection_no_discount, p_intersection_excludes,
           ["status:500", "drop:ucp",
            f"set:ucp.capabilities={_CAPS_WITH_DISCOUNT}",
            "set:ucp.capabilities={}", "corrupt-json"],
           needs=("product",), cfg_needs=("negotiation.harness",),
           capability="dev.ucp.shopping.discount", transport="rest", versions=V0408),
    MCheck("negotiation.orphan_extension_excluded", ["OVR-012"], "MUST",
           f_orphan_extension, p_orphan_excluded,
           # status:201 + id-carrying body = the merchant that skipped step 3 and
           # created a checkout from the orphan-only 'intersection'
           ["status:201", "status:400",
            "set:ucp={\"version\":\"2026-04-08\",\"status\":\"success\"}",
            "set:messages=[]", "drop:messages", "corrupt-json"],
           needs=("product",), cfg_needs=("negotiation.harness",),
           capability="dev.ucp.shopping.discount", transport="rest", versions=V0408),
]
