#!/usr/bin/env python3
"""
schema_check_01_23.py — 2026-01-23 schema-enforced checks, validated by the official
`ucp-schema` oracle (schema_oracle.py). Each check pins a normative MUST that the schema
itself enforces: a VALID fixture must pass the oracle, and every NEGATIVE fixture (one
per defect the MUST forbids) must be rejected. That negative set IS the kill-rate proof —
if a defect still validates, the check can false-pass and the gate fails.

These are static-fixture checks (no live server needed): the shapes they assert are
sub-objects of the pinned 2026-01-23 shopping schemas. Run + gated by
selfcheck/validate_schema_01_23.py (a run_suite gate); skips honestly if the Rust oracle
isn't built.

The `Check("name", ["ID"], ...)` shape is intentional so coverage/matrix.py counts the ids.
"""
import sys, pathlib
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))

VERSION = "2026-01-23"
# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, controls)
#   op/direction: how the valid fixture + negatives are validated (direction=None keeps
#     the validator default; "request" applies ucp_request lifecycle filtering for op).
#   controls: [(payload, op)] that must ALSO validate — positive guards proving a rule
#     is lifecycle-scoped (e.g. ap2 required on complete but NOT on create).
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction controls")
Check.__new__.__defaults__ = ("read", None, ())

_PAY = {"instruments": [{"id": "instr_1", "handler_id": "h1", "type": "card",
                         "display": {"brand": "Visa", "last_digits": "1234"},
                         "credential": {"type": "token", "token": "tok"}}]}
# SD-JWT+kb shaped string (matches the checkout_mandate pattern incl. ~disclosures)
_MANDATE = "eyJhbGciOiJFUzI1NiJ9.eyJjaGVja291dCI6MX0.c2ln~ZGlzY2xvc3VyZQ"
_CREATE_BODY = {"line_items": [{"id": "li_1", "quantity": 1,
                                "item": {"id": "p1", "price": 1000}, "totals": []}]}

CHECKS = [
    Check("discount.allocation_shape", ["DSC-014"],
          "schemas/shopping/discount.json", "allocation",
          {"path": "$.line_items[0]", "amount": 100},
          [{"path": "$.line_items[0]"},                 # missing amount
           {"amount": 100},                              # missing path
           {"path": "$.x", "amount": -100},              # negative (minimum 0)
           {"path": "$.x", "amount": 10.5}]),            # non-integer
    Check("buyer_consent.boolean_states", ["DSC-020"],
          "schemas/shopping/buyer_consent.json", "consent",
          {"analytics": True, "marketing": False, "preferences": True, "sale_of_data": False},
          [{"marketing": "true"},                        # string, not boolean
           {"analytics": 1},                             # integer, not boolean
           {"sale_of_data": None},                       # null, not boolean
           {"preferences": "yes"}]),                     # string, not boolean
    Check("discount.applied_method_enum", ["DSC-013"],
          "schemas/shopping/discount.json", "applied_discount",
          {"title": "Spring sale", "amount": 500, "method": "each"},   # method optional; each|across
          [{"title": "s", "amount": 500, "method": "proportional"},    # outside enum
           {"title": "s", "amount": 500, "method": "EACH"},            # wrong case
           {"title": "s", "amount": 500, "method": 123}]),             # wrong type
    Check("payment.ap2_mandate_on_complete", ["PAY-036"],
          "schemas/shopping/ap2_mandate.json", "checkout",
          {"payment": _PAY, "ap2": {"checkout_mandate": _MANDATE}},    # complete + mandate
          [{"payment": _PAY},                                          # ap2 dropped entirely
           {"payment": _PAY, "ap2": {}},                               # ap2 without checkout_mandate
           {"payment": _PAY, "ap2": {"checkout_mandate": "not a jwt!!"}}],  # pattern violated
          op="complete", direction="request",
          # control: a create request WITHOUT ap2 stays valid on op=create — the rule
          # is lifecycle-scoped to complete, not an unconditional "ap2 always required"
          controls=((_CREATE_BODY, "create"),)),
]


def run():
    """Run every schema check; return (results, oracle_available).
    results = [(check, passed_all, detail)] where passed_all requires the valid fixture to
    validate AND every negative to be rejected (kill-rate)."""
    from schema_oracle import validate_against, OracleUnavailable
    results = []
    for c in CHECKS:
        try:
            ok_valid, dv = validate_against(c.valid, c.schema_rel, c.def_name,
                                            op=c.op, version=VERSION, direction=c.direction)
            neg_ok = []
            for n in c.negatives:
                ok_n, _ = validate_against(n, c.schema_rel, c.def_name,
                                           op=c.op, version=VERSION, direction=c.direction)
                neg_ok.append(ok_n)
            ctrl_ok = all(validate_against(p, c.schema_rel, c.def_name,
                                           op=o, version=VERSION, direction=c.direction)[0]
                          for p, o in c.controls)
        except OracleUnavailable as e:
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
