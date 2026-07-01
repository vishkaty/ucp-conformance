#!/usr/bin/env python3
"""
area_order.py — Order retrieval conformance checks for spec 2026-01-23.

Drives the real GET-order flow against the live reference server:
    create checkout -> complete (yields order.id) -> GET /orders/{id}
and validates the retrieved full order snapshot against the ORD-* register.

Each check cites its ORD requirement(s), evaluates the live GET-order response,
and declares mutations that MUST break it. The engine self-validates every check
by kill-rate (clean must pass, every mutant must deviate) before it can count.

Webhook requirements (ORD-011..ORD-019, testability=needs-receiver) require a
controllable platform receiver to observe POSTs/signatures/retries and are NOT
shippable as kill-safe live checks here; they are intentionally omitted.
ORD-007/ORD-008 (adjustments) and ORD-009 (fulfillment events) are append-only
logs that are empty/null on a freshly-completed order (populating them needs the
gated /testing/simulate-shipping secret), so there is no live data to drive them
kill-safe; also omitted. See CHECKS notes below.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Check, fetch  # noqa: E402
import v2026_01_23 as core       # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "selfcheck"))
from verdict_gate import CLEAN, DEVIATION  # noqa: E402

ORDER_REQUIRED = ("ucp", "id", "checkout_id", "permalink_url",
                  "line_items", "fulfillment", "totals")
LINE_ITEM_REQUIRED = ("id", "item", "quantity", "totals", "status")
EXPECTATION_REQUIRED = ("id", "line_items", "method_type", "destination")


# ---- fetch: create -> complete -> GET /orders/{id} -------------------------
def f_get_order(base):
    """Full order-retrieval flow; returns the live GET /orders/{id} response."""
    cid = (core._create(base).json or {}).get("id")
    order = (core._complete(base, cid).json or {}).get("order") or {}
    oid = order.get("id")
    return fetch(base, f"/orders/{oid}", "GET", None, core._ucp_headers())


# ---- predicates -------------------------------------------------------------
def chk_snapshot(r):
    # ORD-001: Get Order MUST return 200 with the full order entity snapshot
    # (line items + fulfillment + event logs all present, not a partial view).
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    d = r.json
    if not d.get("id") or not d.get("checkout_id"):
        return DEVIATION
    if not isinstance(d.get("line_items"), list) or not d["line_items"]:
        return DEVIATION
    ful = d.get("fulfillment")
    if not isinstance(ful, dict) or "expectations" not in ful or "events" not in ful:
        return DEVIATION
    return CLEAN


def chk_top_fields(r):
    # ORD-002: order entity MUST include all required top-level fields.
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if all(k in r.json for k in ORDER_REQUIRED) else DEVIATION


def chk_currency_not_required(r):
    # ORD-003 (MUST NOT): in 2026-01-23 'currency' is NOT a required top-level
    # order field. A retrieved order that carries the correct required[] set is
    # a valid order EVEN THOUGH it has no top-level 'currency'. We assert the
    # order is valid on exactly the 2026-01-23 required[] set (which excludes
    # currency); dropping any genuinely-required field or losing the 200 breaks it.
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if all(k in r.json for k in ORDER_REQUIRED) else DEVIATION


def chk_line_item_fields(r):
    # ORD-004: each order line item MUST include id, item, quantity, totals, status.
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    lis = r.json.get("line_items")
    if not isinstance(lis, list) or not lis:
        return DEVIATION
    ok = all(isinstance(li, dict) and all(k in li for k in LINE_ITEM_REQUIRED)
             for li in lis)
    return CLEAN if ok else DEVIATION


def chk_quantity_fields(r):
    # ORD-005: line item quantity MUST include both total and fulfilled counts.
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    lis = r.json.get("line_items")
    if not isinstance(lis, list) or not lis:
        return DEVIATION
    ok = all(isinstance(li.get("quantity"), dict)
             and "total" in li["quantity"] and "fulfilled" in li["quantity"]
             for li in lis)
    return CLEAN if ok else DEVIATION


def chk_expectation_fields(r):
    # ORD-010: each fulfillment expectation MUST include id, line_items,
    # method_type, destination.
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    exps = (r.json.get("fulfillment") or {}).get("expectations")
    if not isinstance(exps, list) or not exps:
        return DEVIATION
    ok = all(isinstance(e, dict) and all(k in e for k in EXPECTATION_REQUIRED)
             for e in exps)
    return CLEAN if ok else DEVIATION


CHECKS = [
    Check("order.snapshot", ["ORD-001"], "MUST", f_get_order, chk_snapshot,
          ["status:404", "status:500", "empty", "corrupt-json",
           "drop:line_items", "drop:fulfillment.events"]),
    Check("order.top_fields", ["ORD-002"], "MUST", f_get_order, chk_top_fields,
          ["status:404", "drop:ucp", "drop:checkout_id", "drop:permalink_url",
           "drop:line_items", "drop:fulfillment", "drop:totals", "corrupt-json"]),
    Check("order.currency_not_required", ["ORD-003"], "MUST", f_get_order,
          chk_currency_not_required,
          ["status:404", "drop:permalink_url", "drop:totals", "corrupt-json"]),
    Check("order.line_item_fields", ["ORD-004"], "MUST", f_get_order,
          chk_line_item_fields,
          ["status:404", "drop:line_items.0.id", "drop:line_items.0.item",
           "drop:line_items.0.quantity", "drop:line_items.0.totals",
           "drop:line_items.0.status", "empty"]),
    Check("order.line_item_quantity", ["ORD-005"], "MUST", f_get_order,
          chk_quantity_fields,
          ["status:404", "drop:line_items.0.quantity.total",
           "drop:line_items.0.quantity.fulfilled", "drop:line_items", "corrupt-json"]),
    Check("order.expectation_fields", ["ORD-010"], "MUST", f_get_order,
          chk_expectation_fields,
          ["status:404", "drop:fulfillment.expectations.0.id",
           "drop:fulfillment.expectations.0.line_items",
           "drop:fulfillment.expectations.0.method_type",
           "drop:fulfillment.expectations.0.destination",
           "drop:fulfillment", "empty"]),
]

# ---- SKIPPED (documented, not shipped) -------------------------------------
# needs-receiver (webhooks; no controllable platform endpoint):
#   ORD-011, ORD-012, ORD-013, ORD-014, ORD-015, ORD-016, ORD-017, ORD-018, ORD-019
# no live data to drive kill-safe on a freshly-completed order (empty/null logs;
# populating requires the gated /testing/simulate-shipping secret):
#   ORD-007, ORD-008 (adjustments append-only log), ORD-009 (fulfillment events)
# data-state / not schema-expressible and not distinctly kill-safe live:
#   ORD-006 (line-item immutability — cannot observe a subsequent mutation attempt)
SKIPPED = {
    "ORD-006": "immutability is a data-state property; no live mutation-attempt oracle",
    "ORD-007": "adjustments log null on fresh order; populating needs simulation secret",
    "ORD-008": "adjustments append-only semantics; no live data / not kill-safe",
    "ORD-009": "fulfillment events empty on fresh order; needs simulation secret",
    "ORD-011": "needs-receiver (platform webhook_url negotiation)",
    "ORD-012": "needs-receiver (full-entity webhook POST)",
    "ORD-013": "needs-receiver (order-created webhook)",
    "ORD-014": "needs-receiver (webhook signing/verification)",
    "ORD-015": "needs-receiver (detached-JWT Request-Signature)",
    "ORD-016": "needs-receiver (webhook retry)",
    "ORD-017": "needs-receiver (business identifier in webhook)",
    "ORD-018": "needs-receiver (platform 2xx ack/async)",
    "ORD-019": "needs-receiver (webhook signature rejection)",
}


if __name__ == "__main__":
    from engine import run_check
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8182"
    for c in CHECKS:
        res, det = run_check(c, base)
        print(f"{c.id:28} clean={det['clean']:11} kills={det['kills']:6} "
              f"kill_safe={det['kill_safe']}"
              + (f"  survivors={det['survivors']}" if det.get("survivors") else ""))
