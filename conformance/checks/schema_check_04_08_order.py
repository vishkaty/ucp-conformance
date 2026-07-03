#!/usr/bin/env python3
"""
schema_check_04_08_order.py — 2026-04-08 ORDER-area schema-enforced checks, validated
by the official `ucp-schema` oracle (schema_oracle.py), in the schema_check_04_08.py
pattern: each check pins a normative MUST the schema itself enforces — the VALID
fixture must pass the oracle AND every NEGATIVE (one per defect the MUST forbids)
must be rejected. The negative set IS the kill-rate proof. `controls` are positive
guards proving a rule is no stricter than the spec (optional fields stay optional).

These rows anchor to the order TYPE schemas (types/order_line_item.json,
types/adjustment.json, types/expectation.json, types/fulfillment_event.json,
types/order_confirmation.json) as ROOT schemas (def_name=None -> the oracle's
validate_root path, the ERR-002/003/004 precedent), RESPONSE direction — orders are
business-rendered response entities.

ID-DRIFT: every ORD id below means a DIFFERENT requirement in the 2026-01-23
register (the 04-08 registers renumbered the ORD family — e.g. ORD-006@01-23 is
line-items-immutable-source-of-truth, ORD-018@01-23 is webhook-2xx-ack), so this
file is *_04_08-named and VERSION-pinned: matrix.py attributes its ids to
2026-04-08 only, and the run_schema_04_08.py gate runs it at that version.

Run + gated by the `schema-04-08` run_suite gate (auto-discovered sibling);
skips honestly (exit 2) if the Rust oracle isn't built.
"""
import sys, pathlib
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))

VERSION = "2026-04-08"
# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, strict, controls)
#   def_name None -> the schema is validated as a ROOT schema (validate_root).
#   controls: (payload, op) validated against the check's own schema/def+direction, OR
#   (payload, op, schema_rel, def_name, direction) to prove a rule against a DIFFERENT
#   anchor/direction.
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction strict controls")
Check.__new__.__defaults__ = ("read", None, False, ())

# ---- shared valid payloads (mirror the pinned order.md example shapes) ---------
_LI_VALID = {
    "id": "li_1",
    "item": {"id": "sku_widget", "title": "Widget", "price": 1000},
    "quantity": {"original": 2, "total": 2, "fulfilled": 1},
    "totals": [{"type": "subtotal", "amount": 2000}, {"type": "total", "amount": 2000}],
    "status": "partial",
}
def _li(**quantity):
    out = dict(_LI_VALID)
    out["quantity"] = quantity
    return out

_ADJ_VALID = {
    "id": "adj_1", "type": "refund", "occurred_at": "2026-04-08T12:00:00Z",
    "status": "completed",
    "line_items": [{"id": "li_1", "quantity": -1}],
    "totals": [{"type": "total", "amount": -1000}],
    "description": "Defective item",
}
def _drop(base, key):
    out = dict(base)
    out.pop(key, None)
    return out

_EXP_VALID = {
    "id": "exp_1",
    "line_items": [{"id": "li_1", "quantity": 1}],
    "method_type": "shipping",
    "destination": {"street_address": "123 Main St", "address_locality": "Austin",
                    "address_region": "TX", "address_country": "US",
                    "postal_code": "78701"},
    "description": "Arrives in 2-3 business days",
    "fulfillable_on": "now",
}

_FE_VALID = {
    "id": "evt_1", "occurred_at": "2026-04-08T10:30:00Z", "type": "shipped",
    "line_items": [{"id": "li_1", "quantity": 1}],
    "tracking_number": "1Z999AA10123456784",
    "tracking_url": "https://ups.example/track/1Z999AA10123456784",
}

_OC_VALID = {"id": "order_1", "permalink_url": "https://merchant.example/orders/1",
             "label": "#1001"}

CHECKS = [
    # ORD-006 — order_line_item.quantity requires total AND fulfilled, each a
    # non-negative integer (order_line_item.json "required": ["total","fulfilled"],
    # each integer minimum:0). Control proves `original` stays OPTIONAL.
    Check("order.line_item_quantity_shape", ["ORD-006"],
          "schemas/shopping/types/order_line_item.json", None,
          _LI_VALID,
          [_li(original=2, fulfilled=1),          # total missing
           _li(original=2, total=2),              # fulfilled missing
           _li(original=2, total=-1, fulfilled=0),   # negative total
           _li(original=2, total=2, fulfilled=-1),   # negative fulfilled
           _li(original=2, total=1.5, fulfilled=0),  # non-integer
           _li(original=2, total="2", fulfilled=0)], # string, not integer
          op="read", direction="response",
          controls=((_li(total=1, fulfilled=0), "read"),)),   # original optional
    # ORD-008 — each adjustment requires id, type, occurred_at, status
    # (adjustment.json required[]). Control proves line_items/totals/description
    # stay OPTIONAL (order-level adjustments need no line references).
    Check("order.adjustment_required_fields", ["ORD-008"],
          "schemas/shopping/types/adjustment.json", None,
          _ADJ_VALID,
          [_drop(_ADJ_VALID, "id"), _drop(_ADJ_VALID, "type"),
           _drop(_ADJ_VALID, "occurred_at"), _drop(_ADJ_VALID, "status")],
          op="read", direction="response",
          controls=(({"id": "adj_2", "type": "credit",
                      "occurred_at": "2026-04-08T12:00:00Z", "status": "pending"},
                     "read"),)),
    # ORD-018 — each expectation requires id, line_items, method_type, destination
    # (expectation.json required[]). Control: description/fulfillable_on optional.
    Check("order.expectation_required_fields", ["ORD-018"],
          "schemas/shopping/types/expectation.json", None,
          _EXP_VALID,
          [_drop(_EXP_VALID, "id"), _drop(_EXP_VALID, "line_items"),
           _drop(_EXP_VALID, "method_type"), _drop(_EXP_VALID, "destination")],
          op="read", direction="response",
          controls=((_drop(_drop(_EXP_VALID, "description"), "fulfillable_on"),
                     "read"),)),
    # ORD-019 — each fulfillment event requires id, occurred_at, type, line_items
    # (fulfillment_event.json required[]). Control: tracking fields optional at the
    # schema level (the required-if-shipped prose is not schema-bound).
    Check("order.fulfillment_event_required_fields", ["ORD-019"],
          "schemas/shopping/types/fulfillment_event.json", None,
          _FE_VALID,
          [_drop(_FE_VALID, "id"), _drop(_FE_VALID, "occurred_at"),
           _drop(_FE_VALID, "type"), _drop(_FE_VALID, "line_items")],
          op="read", direction="response",
          controls=((_drop(_drop(_FE_VALID, "tracking_number"), "tracking_url"),
                     "read"),)),
    # ORD-020 — the order confirmation (checkout completion) requires id and
    # permalink_url (order_confirmation.json required[]). Control: label optional.
    Check("order.confirmation_required_fields", ["ORD-020"],
          "schemas/shopping/types/order_confirmation.json", None,
          _OC_VALID,
          [_drop(_OC_VALID, "id"), _drop(_OC_VALID, "permalink_url"),
           {"label": "#1001"}],                    # both required fields missing
          op="read", direction="response",
          controls=((_drop(_OC_VALID, "label"), "read"),)),
]


def run():
    """Run every schema check; return (results, oracle_available).
    results = [(check, passed_all, detail)]; passed_all = valid fixture validates AND
    every control validates AND every negative is rejected (kill-rate)."""
    from schema_oracle import validate_against, validate_root, OracleUnavailable
    results = []
    for c in CHECKS:
        def _va(payload, op, schema_rel=None, def_name="__own__", direction="__own__"):
            schema_rel = schema_rel or c.schema_rel
            def_name = c.def_name if def_name == "__own__" else def_name
            direction = c.direction if direction == "__own__" else direction
            if def_name is None:
                return validate_root(payload, schema_rel, op=op, version=VERSION,
                                     direction=direction or "response")
            return validate_against(payload, schema_rel, def_name,
                                    op=op, version=VERSION, direction=direction,
                                    strict=c.strict)
        try:
            ok_valid, dv = _va(c.valid, c.op)
            neg_ok = [_va(n, c.op)[0] for n in c.negatives]
            ctrl_ok = all(_va(ctrl[0], *ctrl[1:])[0] for ctrl in c.controls)
        except OracleUnavailable:
            return [], False
        killed_all = ok_valid and ctrl_ok and not any(neg_ok)
        surviving = sum(1 for x in neg_ok if x)
        detail = ("clean-pass + kill-safe" if killed_all
                  else f"valid_ok={ok_valid}, ctrl_ok={ctrl_ok}, "
                       f"{surviving}/{len(c.negatives)} mutants SURVIVED")
        results.append((c, killed_all, detail))
    return results, True


if __name__ == "__main__":
    res, avail = run()
    if not avail:
        print("oracle unavailable — skip"); sys.exit(2)
    allok = True
    for c, ok, detail in res:
        print(f"  {'✓' if ok else '✗'} {c.id} ({','.join(c.req_ids)}): {detail}")
        allok = allok and ok
    print("PASS" if allok else "FAIL"); sys.exit(0 if allok else 1)
