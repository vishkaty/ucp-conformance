#!/usr/bin/env python3
"""
area_04_08_fulfillment.py — Phase 2 fixture-based checks for FULFILLMENT (2026-04-08).

Fulfillment is the extension capability dev.ucp.shopping.fulfillment that extends
Checkout (schemas/shopping/fulfillment.json wires a `fulfillment` field into
dev.ucp.shopping.checkout via allOf). 2026-04-08 has no reference server, so each
check validates the hand-built VALID fixture (fulfillment_response.valid.json — a
checkout response whose ucp.capabilities registers BOTH dev.ucp.shopping.checkout
and the dev.ucp.shopping.fulfillment extension so the oracle composes the
fulfillment-extended checkout schema) through the official ucp-schema oracle, then
the engine's kill-rate mutates it to prove the check catches the specific defect.
Every mutation isolates one SCHEMA-ENFORCED fulfillment MUST so schema validation
FAILS (kill_safe) while leaving the rest of the response valid.

Only the schema-enforced (required/enum) fulfillment MUSTs are covered as fixture
checks; the prose semantic/rendering MUSTs (FUL-009/010/012/013/018, ordering, etc.)
are schema_enforced:false and not machine-checkable from the schema, so they are
omitted here. The live 01-23 suite already exercises FUL-003/004/007/008/030 against
the reference server; this module asserts them as schema-enforced fixture checks.

Requirements cited from the pinned 04-08 register:
  conformance/requirements/2026-04-08/fulfillment.json (FUL-*)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from schema_check import fixture_check   # noqa: E402

_V = "2026-04-08"
_FIX = "fulfillment_response.valid.json"

# Nested paths into the single method/group/option/destination of the valid fixture.
_M = "fulfillment.methods.0"
_G = _M + ".groups.0"
_O = _G + ".options.0"
_D = _M + ".destinations.0"

CHECKS = [
    # FUL-003: a fulfillment_method MUST include id, type, and line_item_ids
    # (fulfillment_method.json required:["id","type","line_item_ids"]).
    fixture_check("fulfillment.method_required_fields", ["FUL-003"], "MUST", _V,
                  _FIX, "read", "response",
                  [f"drop:{_M}.id", f"drop:{_M}.type", f"drop:{_M}.line_item_ids",
                   "corrupt-json", "empty"]),

    # FUL-004: fulfillment_method.type MUST be one of shipping|pickup
    # (fulfillment_method.json type.enum). A dropped type also violates the enum
    # gate's required[type] so it is a valid kill for this row too.
    fixture_check("fulfillment.method_type_enum", ["FUL-004"], "MUST", _V,
                  _FIX, "read", "response",
                  [f'set:{_M}.type="bogus"', f"drop:{_M}.type", "corrupt-json"]),

    # FUL-007: a fulfillment_group MUST include id and line_item_ids
    # (fulfillment_group.json required:["id","line_item_ids"]).
    fixture_check("fulfillment.group_required_fields", ["FUL-007"], "MUST", _V,
                  _FIX, "read", "response",
                  [f"drop:{_G}.id", f"drop:{_G}.line_item_ids",
                   "corrupt-json", "empty"]),

    # FUL-008: a fulfillment_option MUST include id, title, and totals
    # (fulfillment_option.json required:["id","title","totals"]).
    fixture_check("fulfillment.option_required_fields", ["FUL-008"], "MUST", _V,
                  _FIX, "read", "response",
                  [f"drop:{_O}.id", f"drop:{_O}.title", f"drop:{_O}.totals",
                   "corrupt-json", "empty"]),

    # FUL-030: a shipping_destination MUST include an id (shipping_destination.json =
    # allOf(postal_address, {required:["id"]}); fulfillment_destination is oneOf).
    fixture_check("fulfillment.destination_required_id", ["FUL-030"], "MUST", _V,
                  _FIX, "read", "response",
                  [f"drop:{_D}.id", "corrupt-json", "empty"]),
]
