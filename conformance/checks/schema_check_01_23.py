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
# Check(id, req_ids, schema_rel, def_name, valid, negatives)
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives")

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
]


def run():
    """Run every schema check; return (results, oracle_available).
    results = [(check, passed_all, detail)] where passed_all requires the valid fixture to
    validate AND every negative to be rejected (kill-rate)."""
    from schema_oracle import validate_against, OracleUnavailable
    results = []
    for c in CHECKS:
        try:
            ok_valid, dv = validate_against(c.valid, c.schema_rel, c.def_name, op="read", version=VERSION)
            neg_ok = []
            for n in c.negatives:
                ok_n, _ = validate_against(n, c.schema_rel, c.def_name, op="read", version=VERSION)
                neg_ok.append(ok_n)
        except OracleUnavailable as e:
            return [], False
        killed_all = ok_valid and not any(neg_ok)
        surviving = sum(1 for x in neg_ok if x)
        detail = ("clean-pass + kill-safe" if killed_all
                  else f"valid_ok={ok_valid}, {surviving}/{len(c.negatives)} mutants SURVIVED")
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
