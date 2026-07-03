#!/usr/bin/env python3
"""
merchant_checks_04_08_lifecycle.py — 2026-04-08-scoped behavioral checks for the
checkout-lifecycle + totals + cart areas (parallel area grind).

Every check is version-locked (versions=V0408) and the filename carries 04_08 so
coverage/matrix.py attributes the ids to 2026-04-08 only. ID-DRIFT verified against
the 2026-01-23 register: CHK-044/CHK-045 name unrelated requirements there
(complete_in_progress / completed status semantics) and the TOT/CART families do
not exist at 01-23 at all.

Register rows covered (conformance/requirements/2026-04-08/):
  checkout-lifecycle.json  CHK-044 (REST bodies valid JSON per RFC 8259)
                           CHK-045 (HTTPS + minimum TLS 1.3)
  totals.json              TOT-017 (sum(lines[].amount) == parent entry amount)
  cart.json                CART-022 (cart REST bodies valid JSON per RFC 8259)
                           CART-023 (cart REST over HTTPS + minimum TLS 1.3)
                           CART-024 (UCP-Agent header required on cart requests)

Subject-binding notes:
  * CHK-044/CART-022 bind BOTH directions; only the response half is
    merchant-observable (our probe authors the requests), so these checks grade
    response-body well-formedness. RFC 8259 precision: parsed with a
    parse_constant trap (NaN/Infinity are Python extensions the RFC forbids)
    and an explicit UTF-8 decode (RFC 8259 §8.1).
  * CART-024's quote binds the requester; the merchant-observable contrapositive
    (reject a request missing the mandatory header, 400 Bad Request per
    cart-rest.md's status-code table) follows the shipped precedent for the same
    rule on checkout (validation.requires_ucp_agent — CHK-052@01-23 /
    CHK-046@04-08, merchant_checks.py).
  * CHK-045/CART-023 are transport-layer: they reuse the SAME probe + predicate
    function objects as transport.https_tls13_minimum (CHK-051@01-23/01-11,
    tls_check_01_11_01_23.py) — mutations=[] because response-mutation cannot
    inject transport defects; the kill proof for those exact functions is the
    dedicated reference gate selfcheck/validate_tls_check.py (clean on the
    TLS-1.3-only listener, DEVIATION on the TLS-1.2-accepting mutant,
    INCONCLUSIVE — never a false deviation — on plain-HTTP dev goldens).

Config gating: TOT-017 needs `totals.sublines` truthy in the merchant config (a
merchant that never itemizes totals entries has nothing to grade — the invariant
is vacuous, so the check skips honestly instead of false-deviating).

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                     # noqa: E402
from merchant_checks import MCheck, _hdr, p_4xx, cart_create_resp  # noqa: E402
from tls_check_01_11_01_23 import chk051_resp, p_tls13_minimum  # noqa: E402

V0408 = ("2026-04-08",)

def _payload(ctx, items):
    """Create-checkout request for [(product_id, qty), ...] — 04-08-conformant:
    NO top-level id (ucp_request:omit, CHK-035)."""
    return {"currency": ctx.config.get("currency", "USD"),
            "line_items": [{"id": f"li_{i+1}", "quantity": q,
                            "item": {"id": pid, "price": 1000}, "totals": []}
                           for i, (pid, q) in enumerate(items)],
            "payment": {"instruments": [], "handlers": ctx.config.get("payment_handlers", [])},
            "status": "incomplete", "ucp": {"version": ctx.version}, "totals": [], "links": []}

def create_resp_0408(ctx):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, [(ctx.product_id, 1)]), _hdr())

def create_two_items_resp(ctx):
    """Two line items, so totals sub-lines have a multi-line breakdown to sum."""
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, [(ctx.product_id, 1), (ctx.product_id, 2)]), _hdr())

def not_found_resp(ctx):
    """GET a checkout id that cannot exist — whatever the outcome (404 protocol
    error or 200 application outcome), the BODY must still be valid JSON."""
    return fetch(ctx.shopping_endpoint, "/checkout-sessions/ucp_nonexistent_chk_44",
                 "GET", None, _hdr())

def cart_no_agent_resp(ctx):
    """Otherwise-valid cart create with the mandatory UCP-Agent header removed."""
    h = _hdr(); h.pop("UCP-Agent", None)
    return fetch(ctx.shopping_endpoint, "/carts", "POST",
                 {"line_items": [{"item": {"id": ctx.product_id}, "quantity": 2}],
                  "currency": ctx.config.get("currency", "USD")}, h)

# ---- CHK-044 / CART-022: REST response bodies are valid JSON (RFC 8259) --------
def p_body_valid_json(r):
    """CLEAN iff the raw response body is a single well-formed RFC 8259 JSON text:
    UTF-8 decodable, parseable, and free of the NaN/Infinity extensions the RFC
    forbids. Status is deliberately NOT graded — an error response's body must be
    valid JSON too, and a 4xx with a JSON body is conformant here."""
    def _no_const(x):
        raise ValueError(f"non-RFC 8259 literal: {x}")
    try:
        json.loads((r.body or b"").decode("utf-8"), parse_constant=_no_const)
        return CLEAN
    except Exception:
        return DEVIATION

# ---- TOT-017: sum(lines[].amount) MUST equal the parent entry's amount ---------
def _entries_with_lines(obj):
    return [t for t in (obj.get("totals") or [])
            if isinstance(t, dict) and t.get("lines")]

def p_sublines_sum(r, ctx):
    """TOT-017@04-08: for EVERY totals entry (top-level and line-item-level) that
    carries a lines[] breakdown, the integer sum of lines[].amount equals the
    parent entry's amount. The merchant config promised sub-lines
    (totals.sublines), so a response with none to grade is a deviation, not a
    vacuous pass."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    entries = _entries_with_lines(r.json)
    for li in r.json.get("line_items") or []:
        if isinstance(li, dict):
            entries += _entries_with_lines(li)
    if not entries:
        return DEVIATION                      # scenario must exhibit sub-lines
    for t in entries:
        total = 0
        for ln in t["lines"]:
            a = ln.get("amount") if isinstance(ln, dict) else None
            if not isinstance(a, int) or isinstance(a, bool):
                return DEVIATION
            total += a
        if total != t.get("amount"):
            return DEVIATION
    return CLEAN

CHECKS_04_08_LIFECYCLE = [
    # CHK-044 — checkout REST response bodies are valid JSON (create + error paths).
    MCheck("checkout.response_body_valid_json", ["CHK-044"], "MUST", create_resp_0408,
           p_body_valid_json,
           ["corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="rest",
           versions=V0408),
    MCheck("checkout.error_body_valid_json", ["CHK-044"], "MUST", not_found_resp,
           p_body_valid_json,
           ["corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", transport="rest", versions=V0408),

    # CHK-045 — checkout REST endpoints over HTTPS with minimum TLS 1.3 (04-08
    # analog of CHK-051@01-23/01-11; same gate-proven probe/predicate — see module
    # docstring for the transport-layer kill proof).
    MCheck("checkout.https_tls13_minimum_04_08", ["CHK-045"], "MUST", chk051_resp,
           p_tls13_minimum,
           [],   # transport-layer: kill proof = selfcheck/validate_tls_check.py
           capability="dev.ucp.shopping.checkout", transport="rest", versions=V0408),

    # TOT-017 — sub-lines sum to the parent totals entry amount (prose-only
    # arithmetic invariant; DSC-019/022 predicate pattern).
    MCheck("totals.sublines_sum_invariant", ["TOT-017"], "MUST", create_two_items_resp,
           p_sublines_sum,
           ["status:500",
            'set:totals.0.lines=[{"display_text":"Item","amount":1}]',  # sum broken
            "drop:totals.0.lines.0",                                    # one line lost
            "drop:totals.0.lines",                                      # promised scenario gone
            "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("totals.sublines",), transport="rest", versions=V0408),

    # CART-022 — cart REST response bodies are valid JSON.
    MCheck("cart.response_body_valid_json", ["CART-022"], "MUST", cart_create_resp,
           p_body_valid_json,
           ["corrupt-json", "empty"],
           capability="dev.ucp.shopping.cart", needs=("product",), transport="rest",
           versions=V0408),

    # CART-023 — cart REST endpoints over HTTPS with minimum TLS 1.3 (cart ops are
    # served from the same declared REST shopping endpoint the probe interrogates).
    MCheck("cart.https_tls13_minimum", ["CART-023"], "MUST", chk051_resp,
           p_tls13_minimum,
           [],   # transport-layer: kill proof = selfcheck/validate_tls_check.py
           capability="dev.ucp.shopping.cart", transport="rest", versions=V0408),

    # CART-024 — cart requests MUST carry the UCP-Agent header: a request without
    # it is invalid and MUST NOT be served (400 per cart-rest.md status codes);
    # precedent: validation.requires_ucp_agent (CHK-052@01-23 / CHK-046@04-08).
    MCheck("cart.requires_ucp_agent", ["CART-024"], "MUST", cart_no_agent_resp, p_4xx,
           ["status:200", "status:201"],
           capability="dev.ucp.shopping.cart", needs=("product",), transport="rest",
           versions=V0408),
]
