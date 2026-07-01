#!/usr/bin/env python3
"""
area_fulfillment.py — 2026-01-23 fulfillment-extension conformance checks.

Covers the schema-shaped, testable MUSTs of the dev.ucp.shopping.fulfillment
register (requirements/2026-01-23/fulfillment.json). Each check evaluates the
`fulfillment` block of a real create-checkout response and declares the nested
`drop:`/`set:` mutations that MUST break that specific requirement. The engine
self-validates every check by kill-rate before it can contribute to a verdict.

Requirements covered:
  FUL-003  fulfillment_method MUST include id, type, line_item_ids
  FUL-004  fulfillment_method.type MUST be one of shipping|pickup
  FUL-007  fulfillment_group MUST include id, line_item_ids
  FUL-008  fulfillment_option MUST include id, title, totals
  FUL-030  shipping_destination MUST include id  (official_oracle=true)
"""
from engine import Check, fetch, CLEAN, DEVIATION  # noqa: F401
import v2026_01_23 as core

_TYPE_ENUM = {"shipping", "pickup"}


def _method0(r):
    """Return methods[0] of a create response, or None if unreachable."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return None
    try:
        m = r.json["fulfillment"]["methods"]
    except (KeyError, TypeError):
        return None
    return m[0] if isinstance(m, list) and m else None


def chk_method_shape(r):        # FUL-003
    m = _method0(r)
    if not isinstance(m, dict):
        return DEVIATION
    lii = m.get("line_item_ids")
    ok = bool(m.get("id")) and bool(m.get("type")) and isinstance(lii, list) and bool(lii)
    return CLEAN if ok else DEVIATION


def chk_method_type_enum(r):    # FUL-004
    m = _method0(r)
    if not isinstance(m, dict):
        return DEVIATION
    return CLEAN if m.get("type") in _TYPE_ENUM else DEVIATION


def chk_group_shape(r):         # FUL-007
    m = _method0(r)
    if not isinstance(m, dict):
        return DEVIATION
    groups = m.get("groups")
    if not isinstance(groups, list) or not groups:
        return DEVIATION
    for g in groups:
        if not isinstance(g, dict):
            return DEVIATION
        lii = g.get("line_item_ids")
        if not g.get("id") or not (isinstance(lii, list) and lii):
            return DEVIATION
    return CLEAN


def chk_option_shape(r):        # FUL-008
    m = _method0(r)
    if not isinstance(m, dict):
        return DEVIATION
    try:
        opts = m["groups"][0]["options"]
    except (KeyError, IndexError, TypeError):
        return DEVIATION
    if not isinstance(opts, list) or not opts:
        return DEVIATION
    ok = all(isinstance(o, dict) and o.get("id") and o.get("title") and ("totals" in o)
             for o in opts)
    return CLEAN if ok else DEVIATION


def chk_shipping_destination_id(r):   # FUL-030
    m = _method0(r)
    if not isinstance(m, dict):
        return DEVIATION
    dests = m.get("destinations")
    if not isinstance(dests, list) or not dests:
        return DEVIATION
    for d in dests:
        if not isinstance(d, dict) or not d.get("id"):
            return DEVIATION
    return CLEAN


CHECKS = [
    Check("fulfillment.method_shape", ["FUL-003"], "MUST", core._create, chk_method_shape,
          ["status:500", "empty", "corrupt-json",
           "drop:fulfillment.methods.0.id",
           "drop:fulfillment.methods.0.type",
           "drop:fulfillment.methods.0.line_item_ids",
           "set:fulfillment={\"methods\":[]}"]),
    Check("fulfillment.method_type_enum", ["FUL-004"], "MUST", core._create, chk_method_type_enum,
          ["status:500", "corrupt-json",
           "drop:fulfillment.methods.0.type",
           "set:fulfillment={\"methods\":[{\"id\":\"m\",\"type\":\"teleport\",\"line_item_ids\":[\"x\"]}]}"]),
    Check("fulfillment.group_shape", ["FUL-007"], "MUST", core._create, chk_group_shape,
          ["status:500", "empty", "corrupt-json",
           "drop:fulfillment.methods.0.groups.0.id",
           "drop:fulfillment.methods.0.groups.0.line_item_ids",
           "drop:fulfillment.methods.0.groups"]),
    Check("fulfillment.option_required_fields", ["FUL-008"], "MUST", core._create, chk_option_shape,
          ["status:500", "empty", "corrupt-json",
           "drop:fulfillment.methods.0.groups.0.options.0.id",
           "drop:fulfillment.methods.0.groups.0.options.0.title",
           "drop:fulfillment.methods.0.groups.0.options.0.totals",
           "drop:fulfillment.methods.0.groups.0.options"]),
    Check("fulfillment.shipping_destination_id", ["FUL-030"], "MUST", core._create,
          chk_shipping_destination_id,
          ["status:500", "empty", "corrupt-json",
           "drop:fulfillment.methods.0.destinations.0.id",
           "drop:fulfillment.methods.0.destinations"]),
]


if __name__ == "__main__":
    import sys
    from engine import run_check
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8182"
    for c in CHECKS:
        _, d = run_check(c, base)
        print(f"{c.id:38} clean={d['clean']!s:11} kills={d['kills']:6} "
              f"kill_safe={d['kill_safe']}"
              + (f"  survivors={d['survivors']}" if d.get("survivors") else ""))
