#!/usr/bin/env python3
"""
merchant_checks_04_08.py — 2026-04-08-scoped behavioral checks (discount area grind).

These register ids mean something DIFFERENT in the 2026-01-11/01-23 registers (the
04-08 registers renumbered the DSC family — e.g. DSC-003 is replacement semantics
here but case-insensitivity there), so every check is version-locked (versions=)
and this file is named *_04_08 so coverage/matrix.py attributes its ids to
2026-04-08 only.

Reference target: the controlled fixture in 04-08 mode (run_suite gate
merchant-catalog). All amounts follow the 04-08 sign convention: discounts.applied
amounts are POSITIVE integers; totals[]/line_items[].totals[] discount entries are
NEGATIVE (discount.md "Amount convention").

Config (under config.discount): valid_code (order-level, no allocations),
second_valid_code, invalid_code, case_insensitive:true,
item: {code, product_id, quantity} — a code that discounts specific line items,
automatic: {product_id, quantity} — a cart that triggers a rule-based discount.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                    # noqa: E402
from merchant_checks import MCheck, _hdr                      # noqa: E402

V0408 = ("2026-04-08",)

def _dcfg(ctx):
    return ctx.config.get("discount") or {}

def _payload(ctx, items, codes=None):
    """Create-checkout payload for an explicit cart [(product_id, qty), ...]."""
    p = {"currency": ctx.config.get("currency", "USD"),
         "line_items": [{"id": f"li_{i+1}", "quantity": q,
                         "item": {"id": pid, "price": 1000}, "totals": []}
                        for i, (pid, q) in enumerate(items)],
         "payment": {"instruments": [], "handlers": ctx.config.get("payment_handlers", [])},
         "status": "incomplete", "ucp": {"version": ctx.version}, "totals": [], "links": []}
    if codes is not None:
        p["discounts"] = {"codes": codes}
    return p

def _create(ctx, items, codes=None):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, items, codes), _hdr())

def _applied(r):
    d = (r.json or {}).get("discounts") if isinstance(r.json, dict) else None
    return d.get("applied") if isinstance(d, dict) else None

def _codes(r):
    d = (r.json or {}).get("discounts") if isinstance(r.json, dict) else None
    return d.get("codes") if isinstance(d, dict) else None

def _totals_of(obj, ttype):
    return [t for t in (obj.get("totals") or [])
            if isinstance(t, dict) and t.get("type") == ttype]

# ---- DSC-003: submitting discounts.codes REPLACES the previous set ------------
def f_replacement(ctx):
    """Create with code A, then update with codes [B] — B must replace A."""
    a, b = _dcfg(ctx).get("valid_code"), _dcfg(ctx).get("second_valid_code")
    c = _create(ctx, [(ctx.product_id, 1)], [a])
    cid = (c.json or {}).get("id")
    li = ((c.json or {}).get("line_items") or [{}])[0]
    body = {"currency": (c.json or {}).get("currency", "USD"),
            "line_items": [{"id": li.get("id"),
                            "item": {"id": (li.get("item") or {}).get("id")},
                            "quantity": 1}],
            "payment": {"instruments": []},
            "discounts": {"codes": [b]}}
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}", "PUT", body, _hdr())

def p_replacement(r, ctx):
    """DSC-003@04-08: after resubmitting codes=[B], A is GONE from both codes[]
    and applied[]; B is present."""
    a, b = _dcfg(ctx).get("valid_code"), _dcfg(ctx).get("second_valid_code")
    if r.status != 200:
        return DEVIATION
    codes, ap = _codes(r), _applied(r)
    if not isinstance(codes, list) or not isinstance(ap, list):
        return DEVIATION
    if a in codes or any(x.get("code") == a for x in ap if isinstance(x, dict)):
        return DEVIATION                          # replaced code survived
    applied_codes = {x.get("code") for x in ap if isinstance(x, dict)}
    return CLEAN if b in codes and b in applied_codes else DEVIATION

# ---- DSC-005: codes are matched case-insensitively -----------------------------
def f_lowercase(ctx):
    return _create(ctx, [(ctx.product_id, 1)], [_dcfg(ctx).get("valid_code").lower()])

def p_lowercase_applied(r, ctx):
    """DSC-005@04-08: a lowercased valid code still applies (amount > 0)."""
    if r.status not in (200, 201):
        return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list) or not ap:
        return DEVIATION
    want = _dcfg(ctx).get("valid_code", "").upper()
    return CLEAN if any(isinstance(x, dict) and (x.get("code") or "").upper() == want
                        and isinstance(x.get("amount"), int) and x["amount"] > 0
                        for x in ap) else DEVIATION

# ---- DSC-008/018/019/020/022: reflection + total-type selection + invariants ---
def f_mixed_discounts(ctx):
    """One response carrying BOTH an order-level code (no allocations) and an
    item-level code (allocations): the richest shape the invariants bite on."""
    item = _dcfg(ctx).get("item") or {}
    return _create(ctx, [(ctx.product_id, 1), (item.get("product_id"), item.get("quantity", 1))],
                   [_dcfg(ctx).get("valid_code"), item.get("code")])

def p_reflected(r, ctx):
    """DSC-008@04-08: discount amounts are reflected in totals[] AND
    line_items[].totals[] — order-level code -> a negative totals[type=discount];
    item-level code -> a negative line_items[].totals[type=items_discount]."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    order = _totals_of(r.json, "discount")
    if not order or not all(isinstance(t.get("amount"), int) and t["amount"] < 0
                            for t in order):
        return DEVIATION
    line_hits = [t for li in r.json.get("line_items") or []
                 for t in _totals_of(li, "items_discount")]
    if not line_hits or not all(isinstance(t.get("amount"), int) and t["amount"] < 0
                                for t in line_hits):
        return DEVIATION
    return CLEAN

def p_total_type_selection(r, ctx):
    """DSC-018@04-08: allocation-bearing discounts contribute to items_discount;
    discounts WITHOUT allocations contribute to discount — verified by AMOUNT:
    -totals[items_discount] == sum(allocated applied amounts) and
    -totals[discount] == sum(unallocated applied amounts)."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list) or not ap:
        return DEVIATION
    alloc_sum = sum(x.get("amount", 0) for x in ap
                    if isinstance(x, dict) and x.get("allocations"))
    plain_sum = sum(x.get("amount", 0) for x in ap
                    if isinstance(x, dict) and not x.get("allocations"))
    items_t = sum(t.get("amount", 0) for t in _totals_of(r.json, "items_discount"))
    order_t = sum(t.get("amount", 0) for t in _totals_of(r.json, "discount"))
    if alloc_sum <= 0 or plain_sum <= 0:
        return DEVIATION                          # scenario must exercise BOTH kinds
    return CLEAN if items_t == -alloc_sum and order_t == -plain_sum else DEVIATION

def p_items_discount_invariant(r, ctx):
    """DSC-019@04-08: totals[type=items_discount].amount equals
    sum(line_items[].totals[type=items_discount].amount)."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    top = sum(t.get("amount", 0) for t in _totals_of(r.json, "items_discount"))
    lines = sum(t.get("amount", 0) for li in r.json.get("line_items") or []
                for t in _totals_of(li, "items_discount"))
    if top == 0 or lines == 0:
        return DEVIATION                          # scenario must produce item discounts
    return CLEAN if top == lines else DEVIATION

def p_amounts_positive(r, ctx):
    """DSC-020@04-08: every discounts.applied amount is a POSITIVE integer."""
    if r.status not in (200, 201):
        return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list) or not ap:
        return DEVIATION
    return CLEAN if all(isinstance(x, dict) and isinstance(x.get("amount"), int)
                        and not isinstance(x.get("amount"), bool) and x["amount"] > 0
                        for x in ap) else DEVIATION

def p_allocation_sum(r, ctx):
    """DSC-022@04-08: for every allocation-bearing applied discount,
    sum(allocations[].amount) equals applied_discount.amount."""
    if r.status not in (200, 201):
        return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list):
        return DEVIATION
    with_alloc = [x for x in ap if isinstance(x, dict) and x.get("allocations")]
    if not with_alloc:
        return DEVIATION                          # scenario must produce allocations
    for x in with_alloc:
        if sum(a.get("amount", 0) for a in x["allocations"]) != x.get("amount"):
            return DEVIATION
    return CLEAN

# ---- DSC-012: automatic discounts carry automatic:true and NO code -------------
def f_automatic(ctx):
    auto = _dcfg(ctx).get("automatic") or {}
    return _create(ctx, [(auto.get("product_id"), auto.get("quantity", 1))])

def p_automatic_flag(r, ctx):
    """DSC-012@04-08: a rule-based discount appears in applied with automatic:true
    and NO code field."""
    if r.status not in (200, 201):
        return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list) or not ap:
        return DEVIATION
    return CLEAN if any(isinstance(x, dict) and x.get("automatic") is True
                        and "code" not in x for x in ap) else DEVIATION

CHECKS_04_08 = [
    MCheck("discount.codes_replacement", ["DSC-003"], "MUST", f_replacement, p_replacement,
           ["status:500",
            "set:discounts={\"codes\":[$DVALID,$DSECOND],\"applied\":[{\"code\":$DVALID,\"amount\":100},{\"code\":$DSECOND,\"amount\":100}]}",
            "drop:discounts", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.second_valid_code",), transport="rest", versions=V0408),
    MCheck("discount.case_insensitive_codes", ["DSC-005"], "MUST", f_lowercase,
           p_lowercase_applied,
           ["status:500", "set:discounts={\"codes\":[],\"applied\":[]}",
            "drop:discounts", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.case_insensitive",), transport="rest", versions=V0408),
    MCheck("discount.reflected_in_totals", ["DSC-008"], "MUST", f_mixed_discounts,
           p_reflected,
           ["status:500", "set:totals=[{\"type\":\"subtotal\",\"amount\":1000},{\"type\":\"total\",\"amount\":1000}]",
            "set:line_items=[]", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.item",), transport="rest", versions=V0408),
    MCheck("discount.total_type_selection", ["DSC-018"], "MUST", f_mixed_discounts,
           p_total_type_selection,
           ["status:500", "set:totals=[{\"type\":\"subtotal\",\"amount\":1000},{\"type\":\"total\",\"amount\":1000}]",
            "set:discounts={\"codes\":[],\"applied\":[]}", "corrupt-json"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.item",), transport="rest", versions=V0408),
    MCheck("discount.items_discount_invariant", ["DSC-019"], "MUST", f_mixed_discounts,
           p_items_discount_invariant,
           ["status:500", "set:line_items=[]",
            "set:totals=[{\"type\":\"subtotal\",\"amount\":1000},{\"type\":\"items_discount\",\"amount\":-1},{\"type\":\"total\",\"amount\":999}]",
            "corrupt-json"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.item",), transport="rest", versions=V0408),
    MCheck("discount.amounts_positive", ["DSC-020"], "MUST", f_mixed_discounts,
           p_amounts_positive,
           ["status:500",
            "set:discounts={\"codes\":[$DVALID],\"applied\":[{\"code\":$DVALID,\"amount\":-100}]}",
            "set:discounts={\"codes\":[$DVALID],\"applied\":[{\"code\":$DVALID,\"amount\":10.5}]}",
            "drop:discounts", "corrupt-json"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount",), transport="rest", versions=V0408),
    MCheck("discount.allocation_sum_invariant", ["DSC-022"], "MUST", f_mixed_discounts,
           p_allocation_sum,
           ["status:500",
            "set:discounts={\"codes\":[$DVALID],\"applied\":[{\"code\":$DVALID,\"amount\":999,\"allocations\":[{\"path\":\"$.line_items[0]\",\"amount\":1}]}]}",
            "drop:discounts", "corrupt-json"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.item",), transport="rest", versions=V0408),
    MCheck("discount.automatic_no_code", ["DSC-012"], "MUST", f_automatic,
           p_automatic_flag,
           ["status:500",
            "set:discounts={\"codes\":[],\"applied\":[{\"code\":\"BULK\",\"title\":\"Bulk\",\"amount\":500}]}",
            "set:discounts={\"codes\":[],\"applied\":[]}", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.automatic",), transport="rest", versions=V0408),
]
