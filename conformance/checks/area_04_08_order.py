#!/usr/bin/env python3
"""
area_04_08_order.py — 2026-04-08 fixture-based conformance checks for the ORDER
capability (dev.ucp.shopping.order), validated through the official ucp-schema oracle.

Each check loads the VALID order fixture (op "read", response direction) and declares
mutations the schema MUST reject. The 04-08 delta is that order.json required[] now
INCLUDES `currency` — the drop:currency mutation proves that delta is enforced.

Requirements cited from conformance/requirements/2026-04-08/order.json (ORD-*).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from schema_check import fixture_check   # noqa: E402

_V = "2026-04-08"
_FX = "order_response.valid.json"

CHECKS = [
    # Order entity top-level required[] (ucp, id, checkout_id, permalink_url,
    # line_items, fulfillment, currency, totals). Cites the required[] block rows
    # ORD-003 (currency) and ORD-004 (line_items).
    fixture_check("order.entity_required_fields", ["ORD-003", "ORD-004"], "MUST", _V,
                  _FX, "read", "response",
                  ["drop:ucp", "drop:id", "drop:checkout_id", "drop:permalink_url",
                   "drop:line_items", "drop:fulfillment", "drop:currency", "drop:totals",
                   "corrupt-json", "empty"]),

    # 04-08 delta: `currency` is newly REQUIRED on the order entity. Isolates the
    # drop:currency kill so the delta is asserted on its own row (ORD-003).
    fixture_check("order.currency_required_delta", ["ORD-003"], "MUST", _V,
                  _FX, "read", "response",
                  ["drop:currency", "corrupt-json", "empty"]),

    # permalink_url is REQUIRED on the order entity (part of order.json required[]).
    fixture_check("order.permalink_url_required", ["ORD-004"], "MUST", _V,
                  _FX, "read", "response",
                  ["drop:permalink_url", "corrupt-json", "empty"]),

    # Each order line item requires id, item, quantity, totals, status (order_line_item.json).
    fixture_check("order.line_item_required_fields", ["ORD-005"], "MUST", _V,
                  _FX, "read", "response",
                  ["drop:line_items.0.id", "drop:line_items.0.item",
                   "drop:line_items.0.quantity", "drop:line_items.0.totals",
                   "drop:line_items.0.status", "corrupt-json", "empty"]),
]
