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
# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, strict, controls)
#   strict: validate with --strict true (additionalProperties:false on the resolved
#   schema) so lifecycle-OMITTED fields are genuinely REJECTED — reliable only on
#   simple object defs (allOf composition breaks strict; see RESOLVE_CHECKS below).
#   controls: (payload, op) validated against the check's own schema/def+direction, OR
#   (payload, op, schema_rel, def_name, direction) to prove a rule against a DIFFERENT
#   anchor/direction (e.g. discounts_object accepts `applied` on responses).
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction strict controls")
Check.__new__.__defaults__ = ("read", None, False, ())

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
    # DSC-027 — discounts.applied is RESPONSE-only (ucp_request: omit). Validated on
    # the discounts_object SUBTREE in STRICT mode (a simple object schema, where the
    # resolver's omit-removal + additionalProperties:false genuinely REJECTS applied);
    # the response-direction control proves applied is legal outside request filtering.
    Check("discount.applied_response_only", ["DSC-027"],
          "schemas/shopping/discount.json", "discounts_object",
          {"codes": ["10OFF"]},
          [{"codes": ["10OFF"], "applied": [{"title": "x", "amount": 100}]},
           {"applied": [{"title": "x", "amount": 100}]}],
          op="create", direction="request", strict=True,
          controls=(({"codes": ["10OFF"], "applied": [{"title": "x", "amount": 100}]},
                     "read", "schemas/shopping/discount.json", "discounts_object",
                     "response"),)),
]

# Resolver-level checks for lifecycle-omit annotations on allOf-composed extension
# defs, where payload-level strict validation is unreliable (the resolver's strict
# additionalProperties interacts badly with allOf branches). The official RESOLVER is
# the oracle here: a property annotated {"complete": "omit"} MUST be REMOVED from the
# op=complete request resolution and RETAINED at op=create/update. The retained-op
# assertions are the kill-proof analog — they fail if the resolver stopped resolving,
# resolved the wrong def, or dropped the property globally rather than per-op.
#   RCheck(id, req_ids, schema_rel, def_name, prop, removed_op, kept_ops)
RCheck = namedtuple("RCheck", "id req_ids schema_rel def_name prop removed_op kept_ops")

RESOLVE_CHECKS = [
    # DSC-028 — the discounts object is omitted from checkout COMPLETE requests
    # (discount.json extension def: ucp_request {create/update: optional, complete: omit})
    RCheck("discount.omitted_on_complete", ["DSC-028"],
           "schemas/shopping/discount.json", "dev.ucp.shopping.checkout",
           "discounts", "complete", ("create", "update")),
    # DSC-034 — the buyer object (carrying consent) is omitted from COMPLETE requests
    # (buyer_consent.json extension def: same lifecycle map on `buyer`)
    RCheck("consent.buyer_omitted_on_complete", ["DSC-034"],
           "schemas/shopping/buyer_consent.json", "dev.ucp.shopping.checkout",
           "buyer", "complete", ("create", "update")),
]


def _ext_props(resolved, def_name):
    """Union of property names across the def's allOf branches (the extension
    branch carries the annotated property; the base branch is a $ref)."""
    node = resolved.get("$defs", {}).get(def_name, resolved)
    props = set(node.get("properties", {}))
    for b in node.get("allOf", []):
        if isinstance(b, dict):
            props |= set(b.get("properties", {}))
    return props


def run_resolve_checks():
    """Run every resolver-level check; returns (results, oracle_available)."""
    from schema_oracle import resolve_def, OracleUnavailable
    results = []
    for c in RESOLVE_CHECKS:
        try:
            removed = _ext_props(resolve_def(c.schema_rel, c.def_name, c.removed_op,
                                             version=VERSION), c.def_name)
            kept = [_ext_props(resolve_def(c.schema_rel, c.def_name, op,
                                           version=VERSION), c.def_name)
                    for op in c.kept_ops]
        except OracleUnavailable:
            return [], False
        ok_removed = c.prop not in removed
        ok_kept = all(c.prop in k for k in kept)
        ok = ok_removed and ok_kept
        detail = ("resolver removes on "
                  f"{c.removed_op}, retains on {'/'.join(c.kept_ops)}" if ok
                  else f"removed@{c.removed_op}={ok_removed}, kept@rest={ok_kept}")
        results.append((c, ok, detail))
    return results, True


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
    if avail:
        res2, avail = run_resolve_checks()
        res += res2
    if not avail:
        print("oracle unavailable — skip"); sys.exit(2)
    allok = True
    for c, ok, detail in res:
        print(f"  {'✓' if ok else '✗'} {c.id} ({','.join(c.req_ids)}): {detail}")
        allok = allok and ok
    print("PASS" if allok else "FAIL"); sys.exit(0 if allok else 1)
