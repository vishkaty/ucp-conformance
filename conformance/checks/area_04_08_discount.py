#!/usr/bin/env python3
"""
area_04_08_discount.py — 2026-04-08 fixture-based conformance checks for the DISCOUNT
(dev.ucp.shopping.discount) and BUYER-CONSENT (dev.ucp.shopping.buyer_consent)
extension capabilities, validated through the official ucp-schema oracle.

Both are EXTENSIONS of checkout: they carry no standalone message body. The schema
oracle composes them by extracting `$defs["dev.ucp.shopping.checkout"]` from the
extension schema and allOf-merging it onto the base checkout.json. A fixture is
therefore a normal checkout `read` response that (a) declares BOTH the checkout root
capability AND the extension capability (with `extends: dev.ucp.shopping.checkout`),
and (b) carries the extension's field (`discounts` / `buyer.consent`). The oracle then
enforces the extension's schema-level MUSTs on that body.

Only schema_enforced=true DSC rows are covered here (real JSON-Schema constraints:
required arrays, enum, minimum, boolean type, totals sign). Behavioral/prose DSC rows
(replacement semantics, case-insensitivity, invariants, carry-forward, provisional
resolution, platform-side display) are not schema-enforceable and are intentionally
omitted; their mutations would survive and are not asserted here.

Requirements cited from conformance/requirements/2026-04-08/discounts-consent.json (DSC-*).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from schema_check import fixture_check   # noqa: E402

_V = "2026-04-08"
_DFX = "discount_response.valid.json"
_CFX = "buyer_consent_response.valid.json"

CHECKS = [
    # DSC-023: applied_discount MUST include title and amount (discount.json $defs
    # applied_discount required:[title, amount], schema_enforced).
    fixture_check("discount.applied_required_fields", ["DSC-023"], "MUST", _V,
                  _DFX, "read", "response",
                  ["drop:discounts.applied.0.title",
                   "drop:discounts.applied.0.amount",
                   "corrupt-json", "empty"]),

    # DSC-024: applied_discount.method, when present, MUST be one of 'each'|'across'
    # (discount.json enum, schema_enforced). Fixture uses 'across'; an out-of-enum
    # value must be rejected.
    fixture_check("discount.method_enum", ["DSC-024"], "MUST", _V,
                  _DFX, "read", "response",
                  ['set:discounts.applied.0.method="bogus"',
                   "corrupt-json", "empty"]),

    # DSC-025: applied_discount.priority MUST be an integer >= 1 (discount.json
    # minimum:1, schema_enforced). priority 0 violates the minimum.
    fixture_check("discount.priority_minimum", ["DSC-025"], "MUST", _V,
                  _DFX, "read", "response",
                  ["set:discounts.applied.0.priority=0",
                   "corrupt-json", "empty"]),

    # DSC-026: allocation MUST include path and amount (discount.json $defs
    # allocation required:[path, amount], schema_enforced).
    fixture_check("discount.allocation_required_fields", ["DSC-026"], "MUST", _V,
                  _DFX, "read", "response",
                  ["drop:discounts.applied.0.allocations.0.path",
                   "drop:discounts.applied.0.allocations.0.amount",
                   "corrupt-json", "empty"]),

    # DSC-021: discount/items_discount entries in totals[] MUST be strictly negative
    # (total.json if/then exclusiveMaximum:0 for type in {discount, items_discount};
    # schema_enforced). Flipping the items_discount amount positive must be rejected,
    # at both the checkout totals[] and the line_items[].totals[] level.
    fixture_check("discount.totals_sign_negative", ["DSC-021"], "MUST", _V,
                  _DFX, "read", "response",
                  ["set:totals.1.amount=300",
                   "set:line_items.0.totals.1.amount=300",
                   "corrupt-json", "empty"]),

    # DSC-032 / DSC-033: when the consent extension is active the buyer.consent
    # field carries boolean consent states, and each consent property
    # (analytics/preferences/marketing/sale_of_data) MUST be a boolean when present
    # (buyer_consent.json type:boolean, schema_enforced). Non-boolean values must be
    # rejected. (consent itself is optional, so dropping it is NOT a valid kill.)
    fixture_check("consent.fields_boolean", ["DSC-032", "DSC-033"], "MUST", _V,
                  _CFX, "read", "response",
                  ['set:buyer.consent.marketing="yes"',
                   "set:buyer.consent.analytics=1",
                   'set:buyer.consent.preferences="true"',
                   "set:buyer.consent.sale_of_data=null",
                   "corrupt-json", "empty"]),
]
