#!/usr/bin/env python3
"""
schema_check_04_08.py — 2026-04-08 schema-enforced checks, validated by the official
`ucp-schema` oracle (schema_oracle.py), in the schema_check_01_23.py pattern: each
check pins a normative MUST the schema itself enforces — the VALID fixture must pass
the oracle AND every NEGATIVE (one per defect the MUST forbids) must be rejected.
The negative set IS the kill-rate proof. `controls` are positive guards proving a
rule is no stricter than the spec (e.g. optional fields stay optional).

These are REQUEST-side rows the response-fixture suite (run_04_08.py) cannot reach:
they validate what a conforming AGENT/CLIENT request looks like per the pinned
request schemas, which is what our own probe traffic and the /tool must emit.

Run + gated by the `schema-04-08` run_suite gate (this file's __main__); skips
honestly (exit 2) if the Rust oracle isn't built.

The `Check("name", ["ID"], ...)` shape is intentional so coverage/matrix.py counts
the ids (file name carries 04_08 -> attributes to 2026-04-08 only).
"""
import sys, pathlib
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))

VERSION = "2026-04-08"
# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, controls)
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction controls")
Check.__new__.__defaults__ = ("read", None, ())

CHECKS = [
    # CAT-028 — lookup_request: `ids` is required with minItems 1 (and string items).
    Check("catalog.lookup_request_ids_required", ["CAT-028"],
          "schemas/shopping/catalog_lookup.json", "lookup_request",
          {"ids": ["teapot_ceramic", "mug_enamel_v1"]},
          [{},                                            # ids missing entirely
           {"ids": []},                                   # minItems 1 violated
           {"ids": "teapot_ceramic"},                     # string, not array
           {"ids": [123]}],                               # non-string item
          op="lookup", direction="request"),
    # CAT-005 — pagination request: `limit` integer with minimum 1 (default 10 is a
    # documentation keyword the validator cannot exercise; the default-10 BEHAVIOR is
    # CAT-024's live-fixture check). Controls prove limit/cursor stay OPTIONAL.
    Check("catalog.pagination_limit_minimum", ["CAT-005"],
          "schemas/shopping/types/pagination.json", "request",
          {"limit": 1, "cursor": "b2Zmc2V0OjEw"},
          [{"limit": 0},                                  # minimum 1 violated
           {"limit": -1},                                 # negative
           {"limit": 1.5},                                # non-integer
           {"limit": "10"}],                              # string, not integer
          op="search", direction="request",
          controls=(({}, "search"),                       # both params optional
                    ({"limit": 10}, "search"))),          # the documented default value
]


def run():
    """Run every schema check; return (results, oracle_available).
    results = [(check, passed_all, detail)]; passed_all = valid fixture validates AND
    every control validates AND every negative is rejected (kill-rate)."""
    from schema_oracle import validate_against, validate_root, OracleUnavailable
    results = []
    for c in CHECKS:
        def _va(payload, op):
            if c.def_name is None:
                return validate_root(payload, c.schema_rel, op=op, version=VERSION,
                                     direction=c.direction or "response")
            return validate_against(payload, c.schema_rel, c.def_name,
                                    op=op, version=VERSION, direction=c.direction)
        try:
            ok_valid, dv = _va(c.valid, c.op)
            neg_ok = [_va(n, c.op)[0] for n in c.negatives]
            ctrl_ok = all(_va(p, o)[0] for p, o in c.controls)
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
