#!/usr/bin/env python3
"""
area_04_08_cart.py — 2026-04-08 fixture-based conformance checks for the CART
capability (dev.ucp.shopping.cart), validated through the official ucp-schema oracle.

Cart is NEW in spec 2026-04-08 (does not exist in 2026-01-23). There is no live
reference server and the vendored suite has no cart oracle, so each check loads the
VALID cart fixture (op "read", response direction) and declares mutations the schema
MUST reject.

Only the SCHEMA-ENFORCED (schema_enforced=true) MUST rows are testable here; the many
receiver-side / transport / non-standard-annotation rows (CART-001..028, CART-030) are
not expressible as cart.json JSON-Schema and are intentionally NOT built.

Requirements cited from conformance/requirements/2026-04-08/cart.json (CART-*):
  CART-029  top-level required[]: ucp, id, line_items, currency, totals  (cart.json)
  CART-031  each line item requires id, item, quantity, totals           (line_item.json)
  CART-032  line item quantity is integer minimum 1                      (line_item.json)
  CART-033  each item requires id, title, price                          (item.json)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from schema_check import fixture_check   # noqa: E402

_V = "2026-04-08"
_FX = "cart_response.valid.json"

CHECKS = [
    # CART-029: cart response top-level required[] (ucp, id, line_items, currency,
    # totals). Dropping any one MUST make schema-validation fail.
    fixture_check("cart.entity_required_fields", ["CART-029"], "MUST", _V,
                  _FX, "read", "response",
                  ["drop:ucp", "drop:id", "drop:line_items", "drop:currency",
                   "drop:totals", "corrupt-json", "empty"]),

    # CART-029 delta isolate: `currency` is REQUIRED on the cart entity (parallels the
    # order currency delta). Asserts the currency kill on its own row.
    fixture_check("cart.currency_required", ["CART-029"], "MUST", _V,
                  _FX, "read", "response",
                  ["drop:currency", "corrupt-json", "empty"]),

    # CART-031: each cart line item requires id, item, quantity, totals
    # (cart reuses the checkout line_item.json entity).
    fixture_check("cart.line_item_required_fields", ["CART-031"], "MUST", _V,
                  _FX, "read", "response",
                  ["drop:line_items.0.id", "drop:line_items.0.item",
                   "drop:line_items.0.quantity", "drop:line_items.0.totals",
                   "corrupt-json", "empty"]),

    # CART-032: line item quantity MUST be an integer >= 1. Setting it to 0 violates
    # minimum:1; setting a non-integer violates type:integer.
    fixture_check("cart.line_item_quantity_min", ["CART-032"], "MUST", _V,
                  _FX, "read", "response",
                  ["set:line_items.0.quantity=0", "set:line_items.0.quantity=1.5",
                   "corrupt-json", "empty"]),

    # CART-033: each line item's item requires id, title, price (item.json).
    fixture_check("cart.item_required_fields", ["CART-033"], "MUST", _V,
                  _FX, "read", "response",
                  ["drop:line_items.0.item.id", "drop:line_items.0.item.title",
                   "drop:line_items.0.item.price", "corrupt-json", "empty"]),
]
