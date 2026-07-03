#!/usr/bin/env python3
"""
area_discount.py — 2026-01-23 discount conformance checks (spec dcf7eac).

Discounts are applied by submitting `discounts.codes` on an UPDATE
(PUT /checkout-sessions/{id}); the response carries `discounts.applied[]`
(each with code/title/amount) plus a `totals[type=discount]` entry. Seeded
Flower Shop codes: 10OFF (10%), WELCOME20 (20%), FIXED500 ($5 fixed).

Each fetch_fn creates a fresh checkout, applies codes, and returns the final
(captured) response. Mutations inject defects into that captured response; the
engine self-validates kill-rate (clean must pass, every mutant must deviate).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import v2026_01_23 as core                          # noqa: E402
from engine import Check, fetch                      # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "selfcheck"))
from verdict_gate import CLEAN, DEVIATION            # noqa: E402

# Citation scope: these checks encode the 2026-01-11/2026-01-23 registers' semantics
# (the 2026-04-08 registers renumbered these ids onto DIFFERENT requirements — see
# coverage/matrix.py introspection, which reads this marker for attribution).
VERSIONS = ("2026-01-11", "2026-01-23")


def _apply(base, codes):
    """Create a fresh checkout, then PUT discounts.codes; return the update Resp."""
    c = (core._create(base).json or {})
    cid = c.get("id")
    li = (c.get("line_items") or [{}])[0]
    body = {"id": cid, "currency": c.get("currency", "USD"),
            "line_items": [{"id": li.get("id"),
                            "item": {"id": (li.get("item") or {}).get("id")},
                            "quantity": 1}],
            "payment": {"instruments": (c.get("payment") or {}).get("instruments", [])},
            "discounts": {"codes": codes}}
    return fetch(base, f"/checkout-sessions/{cid}", "PUT", body, core._ucp_headers())


def _discounts(r):
    return (r.json or {}).get("discounts") if isinstance(r.json, dict) else None

def _applied(r):
    d = _discounts(r)
    return d.get("applied") if isinstance(d, dict) else None

def _has_discount_total(r):
    tot = (r.json or {}).get("totals") if isinstance(r.json, dict) else None
    return isinstance(tot, list) and any(
        isinstance(t, dict) and t.get("type") in ("discount", "items_discount") for t in tot)


# ---- fetch_fns -------------------------------------------------------------
def f_single(base):        return _apply(base, ["10OFF"])
def f_multiple(base):      return _apply(base, ["10OFF", "WELCOME20"])
def f_reject_one(base):    return _apply(base, ["10OFF", "INVALID_CODE"])
def f_fixed(base):         return _apply(base, ["FIXED500"])
def f_unknown_only(base):  return _apply(base, ["TOTALLY_BOGUS"])


# ---- predicates ------------------------------------------------------------
def chk_single(r):
    # DSC-004/DSC-011: valid code applied -> appears in applied[] with code+amount,
    # and a discount total is surfaced.
    if r.status != 200: return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list) or not ap: return DEVIATION
    first = ap[0]
    if not (isinstance(first, dict) and first.get("code") == "10OFF"
            and isinstance(first.get("amount"), int) and first["amount"] > 0):
        return DEVIATION
    return CLEAN if _has_discount_total(r) else DEVIATION

def chk_multiple(r):
    # DSC-005: both valid codes applied (accept-both).
    if r.status != 200: return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list): return DEVIATION
    codes = {d.get("code") for d in ap if isinstance(d, dict)}
    return CLEAN if {"10OFF", "WELCOME20"} <= codes else DEVIATION

def chk_reject_one(r):
    # DSC-006/DSC-007: accept-one-reject-one. Valid 10OFF in applied; rejected
    # INVALID_CODE echoed back in discounts.codes but NOT in applied.
    if r.status != 200: return DEVIATION
    d = _discounts(r)
    if not isinstance(d, dict): return DEVIATION
    ap = d.get("applied"); codes = d.get("codes")
    if not isinstance(ap, list) or not isinstance(codes, list): return DEVIATION
    applied_codes = {x.get("code") for x in ap if isinstance(x, dict)}
    if "10OFF" not in applied_codes: return DEVIATION           # valid one applied
    if "INVALID_CODE" in applied_codes: return DEVIATION        # rejected NOT applied
    if "INVALID_CODE" not in codes: return DEVIATION            # rejected echoed back
    return CLEAN

def chk_fixed(r):
    # DSC-011: fixed-amount code surfaces code + exact amount (500 minor units).
    if r.status != 200: return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list) or not ap: return DEVIATION
    first = ap[0]
    if not isinstance(first, dict): return DEVIATION
    return CLEAN if (first.get("code") == "FIXED500" and first.get("amount") == 500) else DEVIATION

def chk_unknown_ignored(r):
    # DSC-007 (surfacing): an unknown-only code is rejected/ignored — echoed in
    # codes but produces no applied discount and no discount total.
    if r.status != 200: return DEVIATION
    d = _discounts(r)
    if not isinstance(d, dict): return DEVIATION
    ap = d.get("applied")
    if ap:  # non-empty applied means the bogus code was (wrongly) applied
        return DEVIATION
    if _has_discount_total(r): return DEVIATION
    return CLEAN if isinstance(d.get("codes"), list) and "TOTALLY_BOGUS" in d["codes"] else DEVIATION


CHECKS = [
    # Single valid discount reflected in applied[] + discount total.
    Check("discount.single_applied", ["DSC-004", "DSC-011"], "MUST", f_single, chk_single,
          ["status:500", "drop:discounts", "drop:discounts.applied",
           "drop:discounts.applied.0.code", "set:totals=[]",
           "set:discounts={\"applied\":[]}", "corrupt-json", "empty"]),

    # Accept-both: multiple valid codes all appear in applied[].
    Check("discount.multiple_accept_both", ["DSC-005"], "MUST", f_multiple, chk_multiple,
          ["status:500", "drop:discounts", "drop:discounts.applied",
           "drop:discounts.applied.1.code",
           "set:discounts={\"applied\":[{\"code\":\"10OFF\"}]}",
           "corrupt-json", "empty"]),

    # Accept-one-reject-one: valid applied, invalid echoed in codes but not applied.
    Check("discount.accept_one_reject_one", ["DSC-006", "DSC-007"], "MUST", f_reject_one, chk_reject_one,
          ["status:500", "drop:discounts", "drop:discounts.applied",
           "drop:discounts.codes",
           "set:discounts={\"codes\":[\"10OFF\",\"INVALID_CODE\"],\"applied\":[{\"code\":\"10OFF\"},{\"code\":\"INVALID_CODE\"}]}",
           "set:discounts={\"codes\":[\"10OFF\"],\"applied\":[{\"code\":\"10OFF\"}]}",
           "corrupt-json", "empty"]),

    # Fixed-amount discount surfaces exact code + amount.
    Check("discount.fixed_amount", ["DSC-011"], "MUST", f_fixed, chk_fixed,
          ["status:500", "drop:discounts", "drop:discounts.applied",
           "drop:discounts.applied.0.amount",
           "set:discounts={\"applied\":[{\"code\":\"FIXED500\",\"amount\":999}]}",
           "corrupt-json", "empty"]),

    # Unknown-only code rejected/ignored: echoed in codes, nothing applied, no discount total.
    Check("discount.unknown_code_rejected", ["DSC-007"], "MUST", f_unknown_only, chk_unknown_ignored,
          ["status:500", "drop:discounts", "drop:discounts.codes",
           "set:discounts={\"codes\":[\"TOTALLY_BOGUS\"],\"applied\":[{\"code\":\"TOTALLY_BOGUS\",\"amount\":100}]}",
           "corrupt-json", "empty"]),
]
