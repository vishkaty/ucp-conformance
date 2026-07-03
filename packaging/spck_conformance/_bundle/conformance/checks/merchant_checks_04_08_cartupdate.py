#!/usr/bin/env python3
"""
merchant_checks_04_08_cartupdate.py — 2026-04-08-scoped behavioral check for the
cart Update operation (CART-017, wave-2 accounting area).

Register row (conformance/requirements/2026-04-08/cart.json):
  CART-017  "For Update Cart (full replacement), the platform MUST send the entire
            cart resource" — cart.md 'Update Cart' L176-177: "Performs a full
            replacement of the cart session. The platform MUST send the entire cart
            resource. The provided resource replaces the existing cart state on the
            business side."

Subject-binding (per the register's F10 un-exemption note): the FIRST clause binds
the requester — satisfied by this suite's own probe, which sends the entire cart
resource. The SECOND clause ("the provided resource replaces the existing cart
state on the business side") IS merchant-observable: create a 2-line cart, PUT the
entire resource with ONE different line, then GET — the stored state must show
ONLY the replaced line with recomputed totals. A merge-semantics server (stale
line survives / stale quantity / stale totals) deviates.

ID-DRIFT: the CART family does not exist in the 2026-01-11/2026-01-23 registers at
all (cart is a 2026-04-08 capability), so the check is version-locked
(versions=V0408) and the filename carries 04_08 for matrix.py attribution.

Config gating: needs cart.second_product_id (a second distinct product so the
replaced-away line is unambiguous — two lines of the SAME product could be
legitimately consolidated by a conformant merchant). Merchants without it skip
honestly (not-tested), never false-deviate.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                    # noqa: E402
from merchant_checks import MCheck, _hdr                      # noqa: E402

V0408 = ("2026-04-08",)


def _second_pid(ctx):
    return (ctx.config.get("cart") or {}).get("second_product_id")


def f_cart_replace(ctx):
    """Create a 2-line cart (products A+B), PUT the ENTIRE resource with only
    (B, qty 3) — the requester-side clause of CART-017 — then GET the cart:
    the final response is the stored state after the full replacement."""
    a, b = ctx.product_id, _second_pid(ctx)
    cur = ctx.config.get("currency", "USD")
    created = fetch(ctx.shopping_endpoint, "/carts", "POST",
                    {"currency": cur,
                     "line_items": [{"item": {"id": a}, "quantity": 1},
                                    {"item": {"id": b}, "quantity": 1}]}, _hdr())
    cid = (created.json or {}).get("id")
    fetch(ctx.shopping_endpoint, f"/carts/{cid}", "PUT",
          {"id": cid, "currency": cur,                 # the entire cart resource
           "line_items": [{"item": {"id": b}, "quantity": 3}]}, _hdr())
    return fetch(ctx.shopping_endpoint, f"/carts/{cid}", "GET", None, _hdr())


def p_cart_replaced(r, ctx):
    """CART-017@04-08: after PUT [(B,3)] on a cart that held [(A,1),(B,1)], the
    stored cart shows ONLY the replaced state: exactly one line item, item B,
    quantity 3, A gone, and totals recomputed from the replaced line (the cart
    subtotal equals the sum of the line-level subtotal entries when present —
    a surviving pre-PUT total is a merge artifact)."""
    a, b = ctx.product_id, _second_pid(ctx)
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    j = r.json
    if (j.get("ucp") or {}).get("status") == "error":
        return DEVIATION                       # e.g. not_found outcome — no stored cart
    items = j.get("line_items")
    if not isinstance(items, list) or len(items) != 1:
        return DEVIATION                       # stale line survived (merge) or lost line
    li = items[0]
    if (li.get("item") or {}).get("id") != b or li.get("quantity") != 3:
        return DEVIATION                       # stale item or stale quantity
    if any((x.get("item") or {}).get("id") == a for x in items if isinstance(x, dict)):
        return DEVIATION                       # replaced-away product survived
    # price recompute: when line-level subtotal entries exist, the cart subtotal
    # must equal their sum (conditional so merchants that don't itemize per-line
    # subtotals are not false-flagged; the controlled golden does itemize).
    line_subs = [t.get("amount") for t in (li.get("totals") or [])
                 if isinstance(t, dict) and t.get("type") == "subtotal"]
    cart_subs = [t.get("amount") for t in (j.get("totals") or [])
                 if isinstance(t, dict) and t.get("type") == "subtotal"]
    if line_subs and cart_subs and sum(line_subs) != cart_subs[0]:
        return DEVIATION                       # totals not recomputed from replaced state
    return CLEAN


CHECKS_04_08_CARTUPDATE = [
    MCheck("cart.update_full_replacement", ["CART-017"], "MUST",
           f_cart_replace, p_cart_replaced,
           ["status:500",
            # merged-state mutant: the pre-PUT line for product A survives next to
            # the replaced line — the exact defect full-replacement forbids
            "set:line_items=[{\"id\":\"li_1\",\"item\":{\"id\":$PRODUCT},\"quantity\":1,"
            "\"totals\":[{\"type\":\"subtotal\",\"amount\":1000}]},"
            "{\"id\":\"li_2\",\"item\":{\"id\":$PRODUCT2},\"quantity\":3,"
            "\"totals\":[{\"type\":\"subtotal\",\"amount\":3000}]}]",
            # stale-quantity mutant: the PUT's quantity change was ignored
            "set:line_items.0.quantity=1",
            # stale-totals mutant: totals not recomputed from the replaced state
            "set:totals=[{\"type\":\"subtotal\",\"amount\":1},{\"type\":\"total\",\"amount\":1}]",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.cart", needs=("product",),
           cfg_needs=("cart.second_product_id",), transport="rest", versions=V0408),
]
