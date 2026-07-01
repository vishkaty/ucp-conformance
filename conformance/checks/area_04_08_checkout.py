#!/usr/bin/env python3
"""
area_04_08_checkout.py — Phase 2 fixture-based checks for CHECKOUT + TOTALS (2026-04-08).

2026-04-08 has no reference server, so each check validates the hand-built VALID
checkout response fixture (checkout_response.valid.json) through the official
ucp-schema oracle, then the engine's kill-rate mutates it to prove the check catches
the specific defect. Every mutation is chosen to isolate one requirement so that
schema validation FAILS (kill_safe) while leaving the rest of the response valid.

Requirements cited from the pinned 04-08 register:
  conformance/requirements/2026-04-08/checkout-lifecycle.json (CHK-*)
  conformance/requirements/2026-04-08/totals.json            (TOT-*)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from schema_check import fixture_check   # noqa: E402

_V = "2026-04-08"
_FIX = "checkout_response.valid.json"

# Totals-array replacements (idiomatic top-level `set:totals=` mutation) that each
# violate exactly one totals invariant while keeping the array otherwise valid.
_DISCOUNT_POSITIVE = ('set:totals=['
    '{"type":"subtotal","display_text":"Subtotal","amount":2000},'
    '{"type":"discount","display_text":"Promo","amount":300},'      # sign flipped: must be < 0
    '{"type":"total","display_text":"Total","amount":2300}]')
_SUBTOTAL_NEGATIVE = ('set:totals=['
    '{"type":"subtotal","display_text":"Subtotal","amount":-2000},'  # additive: must be >= 0
    '{"type":"total","display_text":"Total","amount":-2000}]')
_TWO_SUBTOTALS = ('set:totals=['
    '{"type":"subtotal","amount":2000},'
    '{"type":"subtotal","amount":2000},'                            # maxContains:1 violated
    '{"type":"total","amount":4000}]')
_TWO_TOTALS = ('set:totals=['
    '{"type":"subtotal","amount":2000},'
    '{"type":"total","amount":2000},'
    '{"type":"total","amount":2000}]')                              # maxContains:1 violated

CHECKS = [
    # CHK-034: response MUST carry the required top-level fields.
    fixture_check("checkout.response_required_fields", ["CHK-034"], "MUST", _V,
                  _FIX, "read", "response",
                  ["drop:ucp", "drop:id", "drop:line_items", "drop:status",
                   "drop:currency", "drop:totals", "drop:links",
                   "corrupt-json", "empty"]),

    # CHK-033: status field is constrained to the six lifecycle enum values.
    fixture_check("checkout.status_enum", ["CHK-033"], "MUST", _V,
                  _FIX, "read", "response",
                  ['set:status="bogus"', "drop:status", "corrupt-json"]),

    # TOT-005: exactly one totals entry of type subtotal (minContains/maxContains 1).
    fixture_check("totals.exactly_one_subtotal", ["TOT-005"], "MUST", _V,
                  _FIX, "read", "response",
                  ["drop:totals.0", _TWO_SUBTOTALS, "set:totals=[]",
                   "corrupt-json", "empty"]),

    # TOT-006: exactly one totals entry of type total (minContains/maxContains 1).
    fixture_check("totals.exactly_one_total", ["TOT-006"], "MUST", _V,
                  _FIX, "read", "response",
                  ["drop:totals.3", _TWO_TOTALS, "set:totals=[]",
                   "corrupt-json", "empty"]),

    # TOT-014: subtractive types (discount, items_discount) MUST have negative amounts.
    fixture_check("totals.discount_sign_negative", ["TOT-014"], "MUST", _V,
                  _FIX, "read", "response",
                  [_DISCOUNT_POSITIVE, "corrupt-json", "empty"]),

    # TOT-015: additive types (subtotal, fulfillment, tax, fee) MUST be non-negative.
    fixture_check("totals.additive_sign_nonnegative", ["TOT-015"], "MUST", _V,
                  _FIX, "read", "response",
                  [_SUBTOTAL_NEGATIVE, "corrupt-json", "empty"]),
]
