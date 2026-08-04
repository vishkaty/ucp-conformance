#!/usr/bin/env python3
"""
merchant_checks_01_11_01_23_neg.py — 01-era (2026-01-11/2026-01-23) capability-
negotiation checks (DISCOVERY/NEGOTIATION area).

overview.md "Business Requirements" + "Intersection Algorithm" (verbatim-identical
at 2026-01-11 and 2026-01-23) makes the business, on receiving a request that names
a platform profile URI, compute the platform/business capability INTERSECTION
(NEG-010) and PRUNE any extension whose parent capability is not in it (NEG-011).
When the intersection EMPTIES — disjoint sets, or an orphan extension whose parent
is absent — no compatible capability remains and, per overview.md "Error Handling",
the business MUST return an error response. The pinned Error Handling example is a
top-level `{"status":"requires_escalation","messages":[{"type":"error",...}]}`
envelope; the predicate asserts exactly that spec-grounded shape (a business that
IGNORED negotiation would instead return a created checkout session), NOT any
04-08-only concept (`ucp.status`, a `capabilities_incompatible` register code —
neither exists in the pinned 01-era tree).

The suite is the platform: each check boots a loopback platform-profile server
(webhook_harness.Harness0408, whose served capability SET is chosen per scenario;
the fixture's negotiator reads only the profile's capability KEYS, so the profile
document's own version strings are inert to 01-era negotiation). Every scenario
carries a BASELINE precondition so an error verdict is attributable to the specific
negotiation step, not to loopback negotiation being broken (an unmet precondition
-> INCONCLUSIVE honest skip, never a false deviation).

NEG-009 ("fetch AND validate the platform profile") is deliberately NOT cited: the
"fetch" half is evidenced (both scenarios differ only in served capabilities, so the
outcome depends on the fetched document), but the "validate" half is never exercised
(no malformed-profile rejection is modeled), so crediting the full row would
overstate. NEG-009 stays a documented GAP.

The 2026-04-08 registers renumbered these onto OVR-005/OVR-012, so every check is
versions=("2026-01-11","2026-01-23") and this file's *_01_11_01_23* name keeps the
NEG-* citations off the 2026-04-08 lane. Config-gated on `negotiation.harness`
(a remote merchant cannot reach a local profile server -> honest skip), the same
loopback carve-out the webhook harness documents.

NOTE: imported lazily by merchant_checks.all_checks() — pulls MCheck/_hdr from it.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, fetch, CLEAN, DEVIATION, INCONCLUSIVE       # noqa: E402
from merchant_checks import MCheck, _hdr                             # noqa: E402

VERSIONS = ("2026-01-11", "2026-01-23")
_NEG_GATE = ("negotiation.harness",)
_DISCOUNT = "dev.ucp.shopping.discount"


def _create_under(ctx, caps):
    """Create with UCP-Agent naming a LOOPBACK platform profile advertising `caps`
    (Harness0408 serves the profile; the fixture MUST fetch it and negotiate)."""
    from webhook_harness import Harness0408
    with Harness0408(capabilities=caps) as h:
        hdrs = _hdr()
        hdrs["UCP-Agent"] = f'profile="{h.profile_url}"'
        return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                     {"line_items": [{"item": {"id": ctx.product_id}, "quantity": 1}]},
                     hdrs)


def _created_ok(r):
    """True when the business created a working checkout session (has an id and a
    non-error envelope) — i.e. it proceeded."""
    return r.status in (200, 201) and isinstance(r.json, dict) and r.json.get("id") \
        and (r.json.get("ucp") or {}).get("status") != "error"


def _vacuous(msg):
    return Resp(0, {}, ('{"probe":"%s"}' % msg).encode())


def f_intersection_disjoint(ctx):
    """Baseline: a platform advertising checkout+order negotiates a NON-empty
    intersection and the create SUCCEEDS. Golden: a platform advertising ONLY a
    capability the business does not have (disjoint) -> the intersection is empty."""
    if not _created_ok(_create_under(ctx, ["dev.ucp.shopping.checkout",
                                           "dev.ucp.shopping.order"])):
        return _vacuous("baseline create under a checkout+order platform profile "
                        "did not succeed; disjoint-intersection scenario "
                        "unattributable")
    return _create_under(ctx, ["com.spck.unrelated_capability"])


def f_orphan_excluded(ctx):
    """NEG-011 needs the RAW intersection to be non-empty so that ONLY extension
    validation (step 2) can empty it — which requires the BUSINESS to advertise the
    orphaned extension. Precondition: the business's own profile declares the
    discount extension (else the empty result would be plain disjointness, not
    orphan-exclusion, and NEG-011 would be unattributable -> INCONCLUSIVE). Baseline:
    a platform advertising checkout+discount+order keeps discount (its parent
    checkout is in the intersection) and the create SUCCEEDS. Golden: a platform
    advertising ONLY the discount extension — the raw intersection is {discount}
    (non-empty), so only orphan-exclusion (parent checkout absent) can empty it."""
    if _DISCOUNT not in ctx.capabilities:
        return _vacuous("business does not advertise the discount extension; a "
                        "{discount}-only platform would empty by plain disjointness, "
                        "so orphan-exclusion (NEG-011) is unattributable")
    if not _created_ok(_create_under(ctx, ["dev.ucp.shopping.checkout",
                                           _DISCOUNT, "dev.ucp.shopping.order"])):
        return _vacuous("baseline create under a checkout+discount platform profile "
                        "did not succeed; orphan-exclusion scenario unattributable")
    return _create_under(ctx, [_DISCOUNT])


def p_negotiation_refused(r, ctx):
    """The negotiated intersection emptied, so the business did NOT create a working
    checkout session and instead returned an error response (overview.md "Error
    Handling": a top-level status:requires_escalation envelope carrying a type:error
    message). A business that ignored negotiation would return a created session
    (id + non-error envelope) — that is the deviation this catches. Only the pinned,
    version-neutral facts are asserted (no created session + a type:error message);
    the business's own error `code` string is not asserted."""
    if r.status == 0:
        return INCONCLUSIVE                  # baseline/precondition unmet: honest skip
    if not isinstance(r.json, dict):
        return DEVIATION                     # malformed / corrupt
    if r.status >= 500:
        return DEVIATION                     # a crash is never a conformant refusal
    if _created_ok(r):
        return DEVIATION                     # proceeded despite no common capability
    j = r.json
    has_error = any(isinstance(m, dict) and m.get("type") == "error"
                    for m in j.get("messages") or []) \
        or (j.get("ucp") or {}).get("status") == "error"
    return CLEAN if has_error else DEVIATION


_KILLS = ["set:id=\"chk_leaked\"", "drop:messages", "set:messages=[]",
          "set:messages=[{\"type\":\"warning\",\"code\":\"x\"}]",
          "status:500", "corrupt-json", "empty"]

CHECKS_01_11_01_23_NEG = [
    MCheck("negotiation.intersection_disjoint_01era", ["NEG-010"], "MUST",
           f_intersection_disjoint, p_negotiation_refused, _KILLS,
           needs=("product",), cfg_needs=_NEG_GATE, transport="rest",
           versions=VERSIONS),
    MCheck("negotiation.orphan_extension_excluded_01era", ["NEG-011"], "MUST",
           f_orphan_excluded, p_negotiation_refused, _KILLS,
           needs=("product",), cfg_needs=_NEG_GATE, transport="rest",
           versions=VERSIONS),
]
