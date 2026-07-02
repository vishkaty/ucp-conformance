#!/usr/bin/env python3
"""
merchant_checks_01_23.py — 2026-01-23-scoped behavioral checks (WF#1 vetted backlog).

These register ids mean something DIFFERENT (or are non-MUST) in 2026-04-08 — e.g.
DSC-010 is the automatic-discount MUST here but a messages[] SHOULD there — so every
check is version-locked (versions=) and this file is named *_01_23 so
coverage/matrix.py attributes its ids to 2026-01-23 only.

Reference target: the controlled fixture in 01-23 mode (run_suite gate
merchant-ctrl-01-23). The Flower Shop golden CANNOT exercise these MUSTs — it never
emits automatic:true or line_items[].discount and it rejects lowercased codes — which
is exactly why the fixture exists (see ops/wf1_confirmed_01_23.json for the vetted
specs). On any server whose config lacks the scenario keys the checks are not-tested,
never a false deviation.

Config (under config.discount):
  automatic: {product_id, quantity}   a cart that triggers a rule-based discount
  item:      {code, product_id, quantity}   a code that discounts specific line items
  valid_code + case_insensitive:true  golden matches codes case-insensitively (DSC-003)

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                    # noqa: E402
from merchant_checks import MCheck, _hdr                      # noqa: E402

V0123 = ("2026-01-23",)

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

# ---- DSC-010: automatic discounts -> applied w/ automatic:true and NO code ----
def dsc010_resp(ctx):
    """Drive a cart that triggers a merchant automatic discount WITHOUT any codes."""
    a = _dcfg(ctx).get("automatic") or {}
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, [(a.get("product_id"), int(a.get("quantity", 1)))]), _hdr())

def p_automatic_no_code(r):
    """An applied entry with automatic===true exists; every automatic entry has NO
    code key, an integer amount >= 0, and a title."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    d = r.json.get("discounts")
    ap = d.get("applied") if isinstance(d, dict) else None
    if not isinstance(ap, list) or not ap:
        return DEVIATION
    autos = [x for x in ap if isinstance(x, dict) and x.get("automatic") is True]
    if not autos:
        return DEVIATION
    for x in autos:
        if "code" in x:                                   # spec: NO code field
            return DEVIATION
        if not (isinstance(x.get("amount"), int) and x["amount"] >= 0):
            return DEVIATION
        if not (isinstance(x.get("title"), str) and x["title"]):
            return DEVIATION
    return CLEAN

# ---- DSC-018: totals[items_discount].amount == sum(line_items[].discount) ----
def dsc018_resp(ctx):
    """Drive a cart with an item-level discount code so line discounts + the
    items_discount total are both populated (non-vacuous invariant)."""
    it = _dcfg(ctx).get("item") or {}
    items = [(it.get("product_id"), int(it.get("quantity", 1))), (ctx.product_id, 1)]
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, items, codes=[it.get("code")]), _hdr())

def p_items_discount_invariant(r):
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    tot, lis = r.json.get("totals"), r.json.get("line_items")
    if not isinstance(tot, list) or not isinstance(lis, list):
        return DEVIATION
    entries = [t for t in tot if isinstance(t, dict) and t.get("type") == "items_discount"]
    if len(entries) != 1:            # the seeded item discount MUST surface, exactly once
        return DEVIATION
    s = sum(li.get("discount", 0) for li in lis if isinstance(li, dict))
    if not (isinstance(entries[0].get("amount"), int) and isinstance(s, int) and s > 0):
        return DEVIATION             # s>0: scenario is real, never vacuously equal
    return CLEAN if entries[0]["amount"] == s else DEVIATION

# ---- DSC-003: codes are matched case-insensitively ---------------------------
def dsc003_resp(ctx):
    """Submit the valid code with its letter-case flipped; it MUST still apply."""
    code = _dcfg(ctx).get("valid_code") or ""
    flipped = code.lower() if code != code.lower() else code.upper()
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, [(ctx.product_id, 1)], codes=[flipped]), _hdr())

def p_case_insensitive_applied(r, ctx):
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    d = r.json.get("discounts")
    ap = d.get("applied") if isinstance(d, dict) else None
    if not isinstance(ap, list):
        return DEVIATION
    want = (_dcfg(ctx).get("valid_code") or "").upper()
    ok = any(isinstance(x, dict) and isinstance(x.get("code"), str)
             and x["code"].upper() == want
             and isinstance(x.get("amount"), int) and x["amount"] > 0 for x in ap)
    return CLEAN if ok else DEVIATION

CHECKS_01_23 = [
    MCheck("discount.automatic_no_code", ["DSC-010"], "MUST", dsc010_resp,
           p_automatic_no_code,
           ['set:discounts={"applied":[{"title":"Bulk saver","amount":500,'
            '"automatic":true,"code":"HIDDEN"}]}',            # code present -> violates NO-code
            'set:discounts={"applied":[{"title":"Bulk saver","amount":500}]}',  # automatic flag absent
            'set:discounts={"applied":[]}',                    # not surfaced at all
            'set:discounts.applied.0.amount=-5',               # negative amount
            "drop:discounts.applied", "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.discount",
           cfg_needs=("discount.automatic",), transport="rest", versions=V0123),
    MCheck("discount.items_discount_invariant", ["DSC-018"], "MUST", dsc018_resp,
           p_items_discount_invariant,
           ["set:line_items.0.discount=1",                     # sum no longer matches
            "drop:line_items.0.discount",                      # sum collapses to 0
            'set:totals=[{"type":"subtotal","amount":4900},'
            '{"type":"items_discount","amount":999},'
            '{"type":"total","amount":4420}]',                 # total diverges from sum
            'set:totals=[{"type":"subtotal","amount":4900},'
            '{"type":"total","amount":4420}]',                 # items_discount omitted entirely
            "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.item",), transport="rest", versions=V0123),
    MCheck("discount.case_insensitive_codes", ["DSC-003"], "MUST", dsc003_resp,
           p_case_insensitive_applied,
           ['set:discounts={"codes":["10off"],"applied":[]}',  # code rejected
            'set:discounts.applied.0.amount=0',                # applied but worthless
            "drop:discounts", "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.valid_code", "discount.case_insensitive"),
           transport="rest", versions=V0123),
]
