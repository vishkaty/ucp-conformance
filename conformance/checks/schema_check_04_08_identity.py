#!/usr/bin/env python3
"""
schema_check_04_08_identity.py — 2026-04-08 schema-enforced checks for the
identity-linking and signals-attribution-eligibility areas, validated by the
official `ucp-schema` oracle in the schema_check_04_08.py pattern: the VALID
fixture must pass the oracle AND every NEGATIVE (one per defect the MUST forbids)
must be rejected. The negative set IS the kill-rate proof; `controls` are positive
guards proving a rule is no stricter than the spec.

Version scoping: every id cited here (SAE-*, IDL-*) exists ONLY in the 2026-04-08
registers (the 01-11/01-23 registers stop at IDL-013 and have no SAE family), and
the file name carries 04_08 so coverage/matrix.py attributes to 2026-04-08 only.

Subject binding note: signals/attribution/context are PLATFORM-emitted request
fields, and the identity_linking capability declaration is a BUSINESS profile
artifact. Both kinds are schema-enforced (the register rows carry
schema_enforced:true), so per the area method they ship as oracle-anchored schema
checks — the check proves the pinned schema genuinely enforces the MUST (and gives
/tool + our own probe traffic an executable oracle for those payload shapes).

NESTED-DEF EXTENSION (documented deviation from the base file, kill-gated):
IDL-037/059/060 are enforced by identity_linking.json's
$defs['dev.ucp.common.identity_linking'].business_schema — a NESTED role branch
the CLI's --def cannot select (--def on the container def validates vacuously:
its platform_schema/business_schema keys are not JSON-Schema keywords, a
false-PASS trap; and validate_profile does NOT recurse into capability config
schemas — verified empirically: a profile whose identity_linking entry lacks
config entirely still validates). So a Check whose def_name contains '/' routes
to schema_oracle.validate_nested_def, which wraps the official nested def in a
keyword-free $ref wrapper — the OFFICIAL schema stays the only validation
authority, and a mis-wired pointer is caught by the gates (an erroring wrapper
fails clean-pass; a vacuous one fails the negatives).

Run + gated by the `schema-04-08` run_suite gate (run_schema_04_08.py discovers
this sibling); skips honestly (exit 2) if the Rust oracle isn't built.
"""
import sys, pathlib
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))

VERSION = "2026-04-08"
# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, strict, controls)
#   def_name None       -> validate_root (root schemas with no $defs, e.g. the
#                          signals/attribution/context type schemas)
#   def_name with '/'   -> validate_nested_def (nested role branch; see docstring)
#   controls: (payload, op) validated against the check's own schema/def+direction.
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction strict controls")
Check.__new__.__defaults__ = ("read", None, False, ())

# The identity_linking business declaration as it appears in a merchant profile's
# capabilities["dev.ucp.common.identity_linking"][i] entry.
def _decl(scopes):
    return {"version": "2026-04-08",
            "schema": "https://ucp.dev/schemas/common/identity_linking.json",
            "config": {"scopes": scopes}}

IDL_SCHEMA = "schemas/common/identity_linking.json"
IDL_BUSINESS = "dev.ucp.common.identity_linking/business_schema"

CHECKS = [
    # SAE-002/003 — every signals key MUST be a reverse-domain identifier
    # (signals.json propertyNames.pattern ^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$).
    # Controls: the empty object, extension namespaces, and structured attestation
    # VALUES all stay legal (the rule constrains keys, not values).
    Check("signals.reverse_domain_keys", ["SAE-002", "SAE-003"],
          "schemas/shopping/types/signals.json", None,
          {"dev.ucp.buyer_ip": "203.0.113.42",
           "dev.ucp.user_agent": "Mozilla/5.0 (X11; Linux x86_64)"},
          [{"buyer_ip": "203.0.113.42"},               # bare key, no namespace
           {"Dev.Ucp.buyer_ip": "203.0.113.42"},       # uppercase
           {"7dev.ucp.probe": "x"},                    # leading digit
           {"dev.ucp.": "x"}],                         # trailing empty segment
          op="create", direction="request",
          controls=(({}, "create"),                    # signals object may be empty
                    ({"com.example.device_id": "abc-123"}, "create"),
                    ({"com.example.attestation":         # values are unconstrained
                      {"kid": "k1", "payload": {"pass": True}, "sig": "b64"}},
                     "create"))),
    # SAE-007 — attribution values MUST be string-encoded (attribution.json
    # additionalProperties.type: string rejects raw numbers/booleans/objects/arrays).
    Check("attribution.values_string_encoded", ["SAE-007"],
          "schemas/shopping/types/attribution.json", None,
          {"utm_source": "newsletter", "utm_campaign": "spring_sale",
           "click_id": "abc123", "is_retargeting": "true", "position": "3"},
          [{"position": 3},                            # raw number
           {"is_retargeting": True},                   # raw boolean
           {"meta": {"a": "b"}},                       # object value
           {"ids": ["a", "b"]}],                       # array value
          op="create", direction="request",
          controls=(({}, "create"),)),                 # attribution may be empty
    # SAE-010 — context.eligibility values MUST use reverse-domain naming
    # (items $ref reverse_domain_name.json). The "MUST be non-identifying" half is
    # semantic (no schema/message can prove it) — recorded as not schema-checkable.
    Check("context.eligibility_reverse_domain", ["SAE-010"],
          "schemas/shopping/types/context.json", None,
          {"eligibility": ["com.example.loyalty_gold", "org.school.student"]},
          [{"eligibility": ["loyalty_gold"]},          # bare value, no namespace
           {"eligibility": ["Com.Example.Gold"]},      # uppercase
           {"eligibility": ["com.example.loyalty gold"]},  # whitespace
           {"eligibility": [42]}],                     # non-string
          op="create", direction="request",
          controls=(({"address_country": "US"}, "create"),   # eligibility optional
                    ({"eligibility": []}, "create"))),        # empty list legal
    # IDL-060 — the business identity_linking declaration requires `config`, and
    # config requires `scopes`. Controls prove config stays OPEN (the reserved
    # `providers` extension point) and the scopes map itself may be empty.
    Check("identity.business_config_scopes_required", ["IDL-060"],
          IDL_SCHEMA, IDL_BUSINESS,
          _decl({"dev.ucp.shopping.order:read": {},
                 "dev.ucp.shopping.checkout:manage": {}}),
          [{"version": "2026-04-08"},                  # config missing entirely
           {"version": "2026-04-08", "config": {}}],   # scopes missing
          op="read",
          controls=((_decl({}), "read"),               # empty scopes map is legal
                    ({"version": "2026-04-08",
                      "config": {"scopes": {"dev.ucp.shopping.order:read": {}},
                                 "providers": {"com.google": {"type": "oauth2"}}}},
                     "read"))),
    # IDL-059 — config.scopes property names MUST match the scope_token pattern
    # '{capability}:{scope}' (propertyNames $ref #/$defs/scope_token). Controls
    # prove third-party capabilities and open per-scope policy objects stay legal.
    Check("identity.config_scopes_scope_token", ["IDL-059"],
          IDL_SCHEMA, IDL_BUSINESS,
          _decl({"dev.ucp.shopping.order:read": {},
                 "dev.ucp.shopping.order:manage": {},
                 "com.example.loyalty:points": {}}),
          [_decl({"order-read": {}}),                  # no ':' separator at all
           _decl({"checkout:manage": {}}),             # capability not reverse-DNS
           _decl({"Dev.UCP.shopping.order:read": {}})],  # uppercase capability
          op="read",
          controls=((_decl({"dev.ucp.shopping.checkout:manage":
                            {"min_acr": "urn:mace:incommon:iap:silver",
                             "require_mfa": True}}), "read"),
                    (_decl({"dev.ucp.shopping.checkout:manage":
                            {"description": {"text": "Manage checkout sessions"}}}),
                     "read"))),
    # IDL-037 — the scope NAME (after the colon) MUST match ^[a-z][a-z0-9_]*$
    # (the trailing group of the scope_token pattern). Control: underscores legal.
    Check("identity.scope_name_pattern", ["IDL-037"],
          IDL_SCHEMA, IDL_BUSINESS,
          _decl({"dev.ucp.shopping.order:read": {},
                 "com.example.loyalty:points": {}}),
          [_decl({"dev.ucp.shopping.order:Read": {}}),       # uppercase scope name
           _decl({"dev.ucp.shopping.order:9read": {}}),      # leading digit
           _decl({"dev.ucp.shopping.order:read-only": {}})], # hyphen
          op="read",
          controls=((_decl({"dev.ucp.shopping.checkout:manage_all": {}}), "read"),)),
]


def run():
    """Run every schema check; return (results, oracle_available).
    results = [(check, passed_all, detail)]; passed_all = valid fixture validates AND
    every control validates AND every negative is rejected (kill-rate).
    Same contract as schema_check_04_08.run(), plus the nested-def routing."""
    from schema_oracle import (validate_against, validate_root, validate_nested_def,
                               OracleUnavailable)
    results = []
    for c in CHECKS:
        def _va(payload, op, schema_rel=None, def_name="__own__", direction="__own__"):
            schema_rel = schema_rel or c.schema_rel
            def_name = c.def_name if def_name == "__own__" else def_name
            direction = c.direction if direction == "__own__" else direction
            if def_name is None:
                return validate_root(payload, schema_rel, op=op, version=VERSION,
                                     direction=direction or "response")
            if "/" in def_name:              # nested role branch (see module docstring)
                return validate_nested_def(payload, schema_rel, def_name,
                                           op=op, version=VERSION)
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
