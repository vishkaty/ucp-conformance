#!/usr/bin/env python3
"""
merchant_checks_04_08_order.py — 2026-04-08-scoped behavioral checks (ORDER area).

ID-DRIFT: every ORD id cited here means a DIFFERENT requirement in the 2026-01-11/
01-23 registers (the 04-08 registers renumbered the ORD family — e.g. ORD-002@01-23
is the top-level required-fields row, ORD-007@01-23 is adjustments-required-fields),
so every check is version-locked (versions=("2026-04-08",)) and this file is named
*_04_08_order so coverage/matrix.py attributes its ids to 2026-04-08 only.

Reference target: the controlled fixture in 04-08 mode. The post-order adjustment
scenario (ORD-002/007/009) is driven through the fixture's TEST-ONLY hook
POST /testing/orders/{id}/adjust (precedent: the Flower golden's
/testing/simulate-shipping) and is config-gated on order.simulate_adjustment, so the
checks skip honestly on merchants that expose no such scenario driver.

Config (under config.order): simulate_adjustment (truthy = the merchant serves the
test hook), second_product_id (a second purchasable product so the removal scenario
has a surviving line item).

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                          # noqa: E402
from merchant_checks import MCheck, _hdr, order_get_resp            # noqa: E402
from tls_check_01_11_01_23 import chk051_resp, p_tls13_minimum      # noqa: E402

V0408 = ("2026-04-08",)
_REDUCTION_TYPE = "refund"     # the adjustment type the test hook is driven with


def _ocfg(ctx):
    return ctx.config.get("order") or {}


def _order_after_refund(ctx):
    """Create a TWO-line-item checkout, complete it, then fully refund the first
    line item through the test hook — the order now carries a REMOVED line item
    and a signed reduction adjustment. Returns the final GET /orders/{id}."""
    second = _ocfg(ctx).get("second_product_id")
    p = {"currency": ctx.config.get("currency", "USD"),
         "line_items": [{"id": "li_1", "quantity": 1,
                         "item": {"id": ctx.product_id, "price": 1000}, "totals": []},
                        {"id": "li_2", "quantity": 2,
                         "item": {"id": second, "price": 1000}, "totals": []}],
         "payment": {"instruments": [], "handlers": ctx.config.get("payment_handlers", [])},
         "status": "incomplete", "ucp": {"version": ctx.version}, "totals": [], "links": []}
    c = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr())
    cid = (c.json or {}).get("id")
    done = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete", "POST",
                 ctx.config.get("complete_payment"), _hdr())
    oid = ((done.json or {}).get("order") or {}).get("id")
    before = fetch(ctx.shopping_endpoint, f"/orders/{oid}", "GET", None, _hdr())
    lis = (before.json or {}).get("line_items") or []
    target = next((li for li in lis if isinstance(li, dict)
                   and (li.get("item") or {}).get("id") == ctx.product_id),
                  lis[0] if lis else {})
    fetch(ctx.shopping_endpoint, f"/testing/orders/{oid}/adjust", "POST",
          {"line_item_id": target.get("id"),
           "quantity": ((target.get("quantity") or {}).get("total")) or 1,
           "type": _REDUCTION_TYPE}, _hdr())
    return fetch(ctx.shopping_endpoint, f"/orders/{oid}", "GET", None, _hdr())


# ---- ORD-002: line_items include ALL items that ever existed on the order ------
def p_ever_existed(r, ctx):
    """ORD-002@04-08: after a full-quantity reduction of one of two line items,
    BOTH remain in line_items — the removed one with quantity.total == 0 and the
    derived status "removed" (order.md Status Derivation), the other still live."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    lis = [li for li in r.json.get("line_items") or [] if isinstance(li, dict)]
    if len(lis) < 2:
        return DEVIATION            # a line item that ever existed was dropped
    removed = [li for li in lis if (li.get("quantity") or {}).get("total") == 0]
    if len(removed) != 1 or removed[0].get("status") != "removed":
        return DEVIATION
    kept = [li for li in lis if li is not removed[0]]
    for li in kept:
        t = (li.get("quantity") or {}).get("total")
        if not isinstance(t, int) or isinstance(t, bool) or t <= 0:
            return DEVIATION
    return CLEAN


# ---- ORD-007 / ORD-009: adjustment quantities and totals are SIGNED ------------
def _reductions(r):
    """The adjustments of the reduction type our scenario created (type is an open
    string per the spec; a merchant may also surface OTHER adjustments — additions
    carry positive signs conformantly, so only OUR reduction is graded)."""
    adjs = (r.json or {}).get("adjustments") if isinstance(r.json, dict) else None
    if not isinstance(adjs, list):
        return None
    return [a for a in adjs if isinstance(a, dict) and a.get("type") == _REDUCTION_TYPE]


def p_adj_quantities_signed(r, ctx):
    """ORD-007@04-08: a reduction adjustment's line_items[].quantity values are
    NEGATIVE integers (signed — negative for reductions)."""
    if r.status != 200:
        return DEVIATION
    red = _reductions(r)
    if not red:
        return DEVIATION            # scenario must surface the reduction adjustment
    for a in red:
        lis = a.get("line_items")
        if not isinstance(lis, list) or not lis:
            return DEVIATION
        for li in lis:
            q = (li or {}).get("quantity")
            if not isinstance(q, int) or isinstance(q, bool) or q >= 0:
                return DEVIATION
    return CLEAN


def p_adj_totals_signed(r, ctx):
    """ORD-009@04-08: a reduction adjustment's totals[].amount values are NEGATIVE
    integers (signed — negative for money returned to the buyer)."""
    if r.status != 200:
        return DEVIATION
    red = _reductions(r)
    if not red:
        return DEVIATION
    for a in red:
        totals = a.get("totals")
        if not isinstance(totals, list) or not totals:
            return DEVIATION
        for t in totals:
            amt = (t or {}).get("amount")
            if not isinstance(amt, int) or isinstance(amt, bool) or amt >= 0:
                return DEVIATION
    return CLEAN


# ---- ORD-021: all REST response bodies are valid JSON --------------------------
def p_valid_json_body(r, ctx):
    """ORD-021@04-08: the Get Order response body parses as JSON (RFC 8259). The
    scenario is the merchant's own happy path (create -> complete -> GET), so a
    non-200 there is graded too — the flow is fully driven by the merchant's config."""
    if r.status != 200:
        return DEVIATION
    return CLEAN if r.json is not None else DEVIATION


_CFG_ADJ = ("order.simulate_adjustment", "order.second_product_id", "complete_payment")

CHECKS_04_08_ORDER = [
    MCheck("order.line_items_ever_existed", ["ORD-002"], "MUST",
           _order_after_refund, p_ever_existed,
           ["status:500",
            "drop:line_items.0",                       # the removed item vanishes
            "set:line_items.0.quantity.total=1",       # removal not reflected
            "set:line_items.0.status=\"processing\"",  # status not derived 'removed'
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_CFG_ADJ, transport="rest", versions=V0408),
    MCheck("order.adjustment_quantities_signed", ["ORD-007"], "MUST",
           _order_after_refund, p_adj_quantities_signed,
           ["status:500",
            "set:adjustments.0.line_items.0.quantity=1",   # unsigned reduction
            "drop:adjustments.0.line_items",
            "drop:adjustments", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_CFG_ADJ, transport="rest", versions=V0408),
    MCheck("order.adjustment_totals_signed", ["ORD-009"], "MUST",
           _order_after_refund, p_adj_totals_signed,
           ["status:500",
            "set:adjustments.0.totals.0.amount=1000",      # unsigned money-back
            "drop:adjustments.0.totals",
            "drop:adjustments", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=_CFG_ADJ, transport="rest", versions=V0408),
    MCheck("order.response_valid_json", ["ORD-021"], "MUST",
           order_get_resp, p_valid_json_body,
           ["status:500", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=("complete_payment",), transport="rest", versions=V0408),
    # ORD-022 — HTTPS + minimum TLS 1.3 on the ORDER REST binding (order-rest.md
    # Transport Security; restated for businesses at order-rest.md L260). Reuses the
    # PROVEN transport probe/predicate from tls_check_01_11_01_23 (CHK-051@01-era is
    # the same MUST for checkout): kill proof = the dedicated TLS reference gate
    # (selfcheck/validate_tls_check.py grades the same predicate CLEAN on the
    # TLS-1.3-only listener, DEVIATION on the 1.2-accepting mutant, honest
    # not-tested on plain HTTP), so mutations=[] here like CHK-051.
    MCheck("order.https_tls13_minimum", ["ORD-022"], "MUST",
           chk051_resp, p_tls13_minimum,
           [],
           capability="dev.ucp.shopping.order", transport="rest", versions=V0408),
]
