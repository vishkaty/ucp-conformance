#!/usr/bin/env python3
"""
merchant_checks_04_08_totals.py — LIVE checkout-lane totals invariants + order
confirmation fields (2026-04-08).

Origin (disclosed): the P2-13 suite-vs-suite kill-rate comparison
(conformance/compare/) injected register-cited MUST-defects into the flower
golden and our live merchant runner MISSED six of them, because the TOT-*
invariants were only enforced (a) on the CART object, gated on the cart
capability the flower golden does not declare, and (b) in the fixture/schema
lane, which never observes a live server. The official suite caught five of
the six live. These checks close that class structurally: the totals rows bind
the CHECKOUT totals component, so they must run on live checkout responses,
gated only on the core checkout capability.

Not overfit to the comparison's mutants: each check enforces the FULL register
row it cites (all entry types, all entries, both directions), with its own
kill-mutations, so validate_merchant_checks proves it clean-pass + kill_safe on
the golden like every other check.

Register (2026-04-08): TOT-005/TOT-006 (exactly one subtotal / one total),
TOT-015 (additive types non-negative), TOT-014 + DSC-021 (subtractive types
strictly negative), TOT-020 (every entry has type + amount), ORD-020 (order
confirmation requires id and permalink_url).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION, INCONCLUSIVE          # noqa: E402
from merchant_checks import (MCheck, _hdr, _create_payload,       # noqa: E402
                             create_resp, complete_resp)

V0408 = ("2026-04-08",)

ADDITIVE = ("subtotal", "fulfillment", "tax", "fee")
SUBTRACTIVE = ("discount", "items_discount")


def _totals(r):
    t = r.json.get("totals") if isinstance(r.json, dict) else None
    return t if isinstance(t, list) else None


def create_with_discount_resp(ctx):
    """Create carrying the merchant's valid discount code, so the response's
    totals[] carries at least one subtractive entry to grade."""
    p = _create_payload(ctx)
    p["discounts"] = {"codes": [(ctx.config.get("discount") or {}).get("valid_code")]}
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr())


def p_subtotal_and_total(r):
    """TOT-005/TOT-006: exactly one totals entry of type subtotal AND of type total."""
    if r.status not in (200, 201):
        return DEVIATION
    t = _totals(r)
    if t is None:
        return DEVIATION
    kinds = [x.get("type") for x in t if isinstance(x, dict)]
    return CLEAN if kinds.count("subtotal") == 1 and kinds.count("total") == 1 else DEVIATION


def p_entry_type_and_amount(r):
    """TOT-020: every top-level totals entry carries both type and an integer amount."""
    if r.status not in (200, 201):
        return DEVIATION
    t = _totals(r)
    if not t:
        return DEVIATION
    return CLEAN if all(isinstance(x, dict) and isinstance(x.get("type"), str)
                        and isinstance(x.get("amount"), int) for x in t) else DEVIATION


def p_additive_non_negative(r):
    """TOT-015: subtotal/fulfillment/tax/fee entries MUST have non-negative amounts."""
    if r.status not in (200, 201):
        return DEVIATION
    t = _totals(r)
    if t is None:
        return DEVIATION
    hits = [x for x in t if isinstance(x, dict) and x.get("type") in ADDITIVE]
    if not hits:
        return DEVIATION                      # a checkout without even a subtotal entry
    return CLEAN if all(isinstance(x.get("amount"), int) and x["amount"] >= 0
                        for x in hits) else DEVIATION


def p_subtractive_negative(r):
    """TOT-014 (+DSC-021): discount/items_discount entries MUST be strictly negative.
    Graded on a response created WITH the merchant's valid code; if the server
    reflects no subtractive entry at all there is nothing to grade the sign of
    (that omission is DSC-008's business, not this row's) -> inconclusive."""
    if r.status not in (200, 201):
        return DEVIATION
    t = _totals(r)
    if t is None:
        return DEVIATION
    hits = [x for x in t if isinstance(x, dict) and x.get("type") in SUBTRACTIVE]
    if not hits:
        return INCONCLUSIVE
    return CLEAN if all(isinstance(x.get("amount"), int) and x["amount"] < 0
                        for x in hits) else DEVIATION


def p_order_confirmation(r):
    """ORD-020: the order confirmation on complete carries id AND permalink_url."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    o = r.json.get("order")
    if not isinstance(o, dict):
        return DEVIATION
    return CLEAN if o.get("id") and o.get("permalink_url") else DEVIATION


CHECKS_TOTALS_0408 = [
    MCheck("totals.checkout_subtotal_and_total", ["TOT-005", "TOT-006"], "MUST",
           create_resp, p_subtotal_and_total,
           ["status:500", "set:totals=[]",
            "set:totals=[{\"type\":\"subtotal\",\"amount\":100}]",
            "set:totals=[{\"type\":\"total\",\"amount\":100}]",
            "set:totals=[{\"type\":\"subtotal\",\"amount\":100},{\"type\":\"subtotal\",\"amount\":100},{\"type\":\"total\",\"amount\":100}]",
            "drop:totals", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           transport="rest", versions=V0408),
    MCheck("totals.checkout_entry_type_and_amount", ["TOT-020"], "MUST",
           create_resp, p_entry_type_and_amount,
           ["status:500", "set:totals=[]",
            "set:totals=[{\"type\":\"subtotal\"},{\"type\":\"total\",\"amount\":100}]",
            "set:totals=[{\"amount\":100},{\"type\":\"total\",\"amount\":100}]",
            "set:totals=[{\"type\":\"subtotal\",\"amount\":\"100\"},{\"type\":\"total\",\"amount\":100}]",
            "drop:totals", "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           transport="rest", versions=V0408),
    MCheck("totals.checkout_additive_non_negative", ["TOT-015"], "MUST",
           create_resp, p_additive_non_negative,
           ["status:500",
            "set:totals=[{\"type\":\"subtotal\",\"amount\":-100},{\"type\":\"total\",\"amount\":100}]",
            "set:totals=[{\"type\":\"subtotal\",\"amount\":100},{\"type\":\"tax\",\"amount\":-5},{\"type\":\"total\",\"amount\":95}]",
            "set:totals=[{\"type\":\"total\",\"amount\":100}]",
            "drop:totals", "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           transport="rest", versions=V0408),
    MCheck("totals.checkout_subtractive_negative", ["TOT-014", "DSC-021"], "MUST",
           create_with_discount_resp, p_subtractive_negative,
           ["status:500",
            "set:totals=[{\"type\":\"subtotal\",\"amount\":3500},{\"type\":\"discount\",\"amount\":350},{\"type\":\"total\",\"amount\":3150}]",
            "set:totals=[{\"type\":\"subtotal\",\"amount\":3500},{\"type\":\"discount\",\"amount\":0},{\"type\":\"total\",\"amount\":3500}]",
            "corrupt-json"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.valid_code",), transport="rest", versions=V0408),
    MCheck("order.confirmation_fields", ["ORD-020"], "MUST",
           complete_resp, p_order_confirmation,
           ["status:500", "drop:order", "drop:order.id", "drop:order.permalink_url",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=("complete_payment",), transport="rest", versions=V0408),
]
