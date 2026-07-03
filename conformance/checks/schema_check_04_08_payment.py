#!/usr/bin/env python3
"""
schema_check_04_08_payment.py — 2026-04-08 schema-enforced PAYMENT checks, validated
by the official `ucp-schema` oracle (schema_oracle.py), in the schema_check_04_08.py
pattern: each check pins a normative MUST the schema itself enforces — the VALID
fixture must pass the oracle AND every NEGATIVE (one per defect the MUST forbids)
must be rejected. The negative set IS the kill-rate proof. `controls` are positive
guards proving a rule is no stricter than the spec.

VERSION-LOCKED to 2026-04-08 (file name token): the 04-08 registers RENUMBERED the
PAY family — e.g. PAY-004@01-23 is "document the participant field mappings" while
PAY-004@04-08 is the platform_schema spec+schema requirement below. Never cite these
ids at another version.

Register: conformance/requirements/2026-04-08/payment.json. Pinned sources:
source/schemas/ucp.json, source/schemas/payment_handler.json,
source/schemas/shopping/types/{payment_instrument,payment_credential,card_credential,
card_payment_instrument,token_credential,binding,payment_identity,
available_payment_instrument}.json.

Run + gated by the `schema-04-08` run_suite gate (run_schema_04_08.py auto-discovers
this sibling); skips honestly (exit 2) if the Rust oracle isn't built.
"""
import sys, pathlib
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))

VERSION = "2026-04-08"
# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, strict, controls)
#   def_name=None -> the schema is validated as a ROOT schema (validate_root), the
#   pattern proven by ERR-002/003/004 for $defs-less type schemas.
#   controls: (payload, op) validated against the check's own schema/def+direction, OR
#   (payload, op, schema_rel, def_name, direction) to prove a rule against a DIFFERENT
#   anchor/direction (e.g. business_schema does NOT require spec/schema).
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction strict controls")
Check.__new__.__defaults__ = ("read", None, False, ())

# A minimal, oracle-valid business profile (the fixture's own shape) — the anchor
# payload for the profile-level payment_handlers requirement.
_HANDLER = {"id": "spck_tokenpay", "version": VERSION,
            "spec": "https://spck.dev/fixture/handlers/tokenpay",
            "schema": "https://spck.dev/fixture/handlers/tokenpay/schema.json",
            "available_instruments": [
                {"type": "card", "constraints": {"brands": ["visa", "mastercard"]}}]}

def _profile(**over):
    p = {"version": VERSION,
         "services": {"dev.ucp.shopping": [
             {"version": VERSION, "transport": "rest",
              "endpoint": "https://merchant.example/ucp",
              "spec": f"https://ucp.dev/{VERSION}/specification/shopping",
              "schema": f"https://ucp.dev/{VERSION}/services/shopping/openapi.json"}]},
         "capabilities": {"dev.ucp.shopping.checkout": [{"version": VERSION}]},
         "payment_handlers": {"dev.spck.tokenpay": [dict(_HANDLER)]}}
    p.update(over)
    for k in [k for k, v in over.items() if v is None]:
        del p[k]
    return p

CHECKS = [
    # PAY-001 — a business profile MUST carry the payment_handlers registry, keyed by
    # reverse-domain name (ucp.json#L180 business_schema `"required": ["services",
    # "payment_handlers"]`; registry propertyNames -> reverse_domain_name.json).
    # Control: the platform profile carries the SAME requirement (ucp.json#L150).
    Check("payment.profile_requires_handlers_registry", ["PAY-001"],
          "schemas/ucp.json", "business_schema",
          _profile(),
          [_profile(payment_handlers=None),                # registry key missing
           _profile(payment_handlers=[dict(_HANDLER)]),    # array, not keyed object
           _profile(payment_handlers={"TokenPay": [dict(_HANDLER)]}),  # bad key form
           # an entry violating the handler base schema (id missing — PAY-002)
           # invalidates the WHOLE profile
           _profile(payment_handlers={"dev.spck.tokenpay": [{"version": VERSION}]})],
          op="read", direction=None,
          controls=((_profile(payment_handlers={}), "read"),  # empty registry is legal
                    # the platform profile carries the SAME requirement (ucp.json#L150;
                    # platform capability entries additionally need spec+schema)
                    (_profile(capabilities={"dev.ucp.shopping.checkout": [
                        {"version": VERSION,
                         "spec": f"https://ucp.dev/{VERSION}/specification/shopping",
                         "schema": "https://ucp.dev/schemas/shopping/checkout.json"}]}),
                     "read", "schemas/ucp.json", "platform_schema", None))),
    # PAY-002 — every payment handler declaration MUST include an id
    # (payment_handler.json#L13 base `"required": ["id"]`; inherited by all variants —
    # the business/response controls prove the allOf inheritance). The in-profile
    # enforcement (idless entry invalidates the profile) is a PAY-001 negative above.
    Check("payment.handler_declaration_requires_id", ["PAY-002"],
          "schemas/payment_handler.json", "base",
          {"version": VERSION, "id": "spck_tokenpay"},
          [{"version": VERSION}],                          # id missing
          op="read", direction=None,
          controls=(({"version": VERSION, "id": "spck_tokenpay"}, "read",
                     "schemas/payment_handler.json", "business_schema", None),
                    ({"version": VERSION, "id": "spck_tokenpay"}, "read",
                     "schemas/payment_handler.json", "response_schema", None))),
    # PAY-003 — checkout responses MUST echo the payment_handlers registry
    # (ucp.json#L219 response_checkout_schema `"required": ["payment_handlers"]`);
    # entries deref payment_handler.json response_schema (id required).
    Check("payment.response_envelope_requires_handlers", ["PAY-003"],
          "schemas/ucp.json", "response_checkout_schema",
          {"version": VERSION, "payment_handlers": {"dev.spck.tokenpay": [
              {"id": "spck_tokenpay", "version": VERSION,
               "available_instruments": [{"type": "card",
                                          "constraints": {"brands": ["visa"]}}]}]}},
          [{"version": VERSION},                           # payment_handlers missing
           {"version": VERSION,                            # entry violates response_schema
            "payment_handlers": {"dev.spck.tokenpay": [{"version": VERSION}]}}],
          op="complete", direction="response",
          controls=(({"version": VERSION, "payment_handlers": {}}, "complete"),)),
    # PAY-004 — a platform-schema handler declaration MUST additionally carry
    # spec + schema (payment_handler.json#L32). Control: business_schema does NOT
    # require them (the requirement is platform-variant-specific).
    Check("payment.platform_handler_requires_spec_schema", ["PAY-004"],
          "schemas/payment_handler.json", "platform_schema",
          {"version": VERSION, "id": "spck_tokenpay",
           "spec": "https://spck.dev/fixture/handlers/tokenpay",
           "schema": "https://spck.dev/fixture/handlers/tokenpay/schema.json"},
          [{"version": VERSION, "id": "spck_tokenpay",     # spec missing
            "schema": "https://spck.dev/fixture/handlers/tokenpay/schema.json"},
           {"version": VERSION, "id": "spck_tokenpay",     # schema missing
            "spec": "https://spck.dev/fixture/handlers/tokenpay"}],
          op="read", direction=None,
          controls=(({"version": VERSION, "id": "spck_tokenpay"}, "read",
                     "schemas/payment_handler.json", "business_schema", None),)),
    # PAY-005 — a declared available_instruments array MUST NOT be empty
    # (payment_handler.json#L22 `"minItems": 1`). Control: the field itself is
    # optional (guide L194: absent = no restrictions).
    Check("payment.available_instruments_min_one", ["PAY-005"],
          "schemas/payment_handler.json", "base",
          {"version": VERSION, "id": "spck_tokenpay",
           "available_instruments": [{"type": "card"}]},
          [{"version": VERSION, "id": "spck_tokenpay", "available_instruments": []}],
          op="read", direction=None,
          controls=(({"version": VERSION, "id": "spck_tokenpay"}, "read"),)),
    # PAY-007 — an available_payment_instrument MUST include type
    # (available_payment_instrument.json#L7); constraints, when present, MUST be
    # non-empty (minProperties 1, same file — row notes).
    Check("payment.available_instrument_requires_type", ["PAY-007"],
          "schemas/shopping/types/available_payment_instrument.json", None,
          {"type": "card", "constraints": {"brands": ["visa"]}},
          [{},                                             # type missing
           {"constraints": {"brands": ["visa"]}},          # type missing, constraints set
           {"type": 123},                                  # non-string type
           {"type": "card", "constraints": {}}],           # empty constraints object
          op="read", direction="response",
          controls=(({"type": "card"}, "read"),)),         # constraints stay optional
    # PAY-019 — a payment instrument MUST include id, handler_id, and type
    # (payment_instrument.json#L7).
    Check("payment.instrument_required_fields", ["PAY-019"],
          "schemas/shopping/types/payment_instrument.json", None,
          {"id": "instr_1", "handler_id": "spck_tokenpay", "type": "card"},
          [{"handler_id": "spck_tokenpay", "type": "card"},          # id missing
           {"id": "instr_1", "type": "card"},                        # handler_id missing
           {"id": "instr_1", "handler_id": "spck_tokenpay"}],        # type missing
          op="complete", direction="request",
          controls=(({"id": "instr_1", "handler_id": "spck_tokenpay", "type": "card",
                      "credential": {"type": "token"}}, "complete"),)),
    # PAY-020 — a payment credential MUST include a type discriminator
    # (payment_credential.json#L7). Control: additionalProperties stay open (handlers
    # extend the base credential).
    Check("payment.credential_requires_type", ["PAY-020"],
          "schemas/shopping/types/payment_credential.json", None,
          {"type": "token"},
          [{}],                                            # type missing
          op="complete", direction="request",
          controls=(({"type": "token", "handler_extra": True}, "complete"),)),
    # PAY-021 — a card credential MUST include type (const card) and card_number_type
    # (card_credential.json#L12; enum fpan|network_token|dpan, same file).
    Check("payment.card_credential_required_fields", ["PAY-021"],
          "schemas/shopping/types/card_credential.json", None,
          {"type": "card", "card_number_type": "network_token",
           "number": "4242424242424242",
           "cryptogram": "gXc5UCLnM6ckD7pjM1TdPA==", "eci_value": "07"},
          [{"type": "card"},                               # card_number_type missing
           {"card_number_type": "fpan"},                   # type missing
           {"type": "token", "card_number_type": "fpan"},  # type const violated
           {"type": "card", "card_number_type": "pan"}],   # enum violated
          op="complete", direction="request",
          controls=(({"type": "card", "card_number_type": "fpan"}, "complete"),)),
    # PAY-023 — a card payment instrument MUST have type const 'card'
    # (card_payment_instrument.json#L41-45); base payment_instrument fields are
    # inherited via allOf (a missing handler_id still invalidates it).
    Check("payment.card_instrument_type_const", ["PAY-023"],
          "schemas/shopping/types/card_payment_instrument.json", None,
          {"id": "instr_1", "handler_id": "spck_tokenpay", "type": "card",
           "display": {"brand": "visa", "last_digits": "4242"}},
          [{"id": "instr_1", "handler_id": "spck_tokenpay", "type": "bank_transfer"},
           {"id": "instr_1", "handler_id": "spck_tokenpay"},         # type missing
           {"id": "instr_1", "type": "card"}],             # inherited handler_id missing
          op="complete", direction="request"),
    # PAY-024 — a token credential MUST include type and token
    # (token_credential.json#L12). Request-direction: the token field exists on the
    # wire Platform -> Business; the response-direction control (payload legal WITHOUT
    # token) is the flip side of PAY-012's omit rule.
    Check("payment.token_credential_required_fields", ["PAY-024"],
          "schemas/shopping/types/token_credential.json", None,
          {"type": "stripe_token", "token": "tok_visa_4242"},
          [{"type": "stripe_token"},                       # token missing
           {"token": "tok_visa_4242"}],                    # type missing
          op="complete", direction="request",
          controls=(({"type": "stripe_token"}, "complete",
                     "schemas/shopping/types/token_credential.json", None, "response"),)),
    # PAY-026 — a token binding MUST include the checkout_id it is bound to
    # (binding.json#L7). Control: identity stays optional (required only when acting
    # on behalf of another participant — binding.json#L15).
    Check("payment.binding_requires_checkout_id", ["PAY-026"],
          "schemas/shopping/types/binding.json", None,
          {"checkout_id": "chk_123"},
          [{},                                             # checkout_id missing
           {"identity": {"access_token": "pt_1"}}],        # identity alone insufficient
          op="complete", direction="request",
          controls=(({"checkout_id": "chk_123",
                      "identity": {"access_token": "pt_1"}}, "complete"),)),
    # PAY-027 — a payment identity MUST include access_token (payment_identity.json#L7).
    Check("payment.identity_requires_access_token", ["PAY-027"],
          "schemas/shopping/types/payment_identity.json", None,
          {"access_token": "pt_participant_1"},
          [{},                                             # access_token missing
           {"token": "pt_participant_1"}],                 # wrong field name
          op="complete", direction="request"),
]

# Resolver-level check for PAY-012: token_credential.json#L21 annotates the token
# value `"ucp_response": "omit"` — the schema-level enforcement of the no-echo rule.
# The annotation is DIRECTION-scoped (not per-op like DSC-028), so the proof is: the
# official resolver REMOVES the property (and its required entry) from the RESPONSE
# resolution and RETAINS both in the REQUEST resolution. The retained-direction
# assertions are the kill-proof analog — they fail if the resolver stopped resolving
# or dropped the property globally rather than per-direction.
#   DirRCheck(id, req_ids, schema_rel, prop, removed_direction, kept_direction, op)
DirRCheck = namedtuple("DirRCheck",
                       "id req_ids schema_rel prop removed_direction kept_direction op")

RESOLVE_CHECKS = [
    DirRCheck("payment.token_omitted_on_response", ["PAY-012"],
              "schemas/shopping/types/token_credential.json",
              "token", "response", "request", "complete"),
]


def _resolve_schema(schema_rel, op, direction):
    """Resolve a ROOT schema (no named $defs) for a direction+op via the official
    resolver — the authority on ucp_request/ucp_response annotation semantics."""
    import json as _json
    from schema_oracle import SCHEMA_BASE, _run, OracleUnavailable
    base = SCHEMA_BASE.get(VERSION)
    schema = (base / schema_rel) if base else None
    if not base or not schema or not schema.exists():
        raise OracleUnavailable(f"schema {schema_rel} for {VERSION} not found under {base}")
    args = ["resolve", str(schema), "--op", op, "--schema-local-base", str(base)]
    args.append("--request" if direction == "request" else "--response")
    r = _run(args)
    if r.returncode != 0:
        raise OracleUnavailable(f"resolve failed: {(r.stdout + r.stderr)[:200]}")
    return _json.loads(r.stdout)


def _props_and_required(resolved):
    """Union of property names + required names across the schema's allOf branches
    (token_credential composes the base credential $ref with its own branch)."""
    props, req = set(resolved.get("properties", {})), set(resolved.get("required", []))
    for b in resolved.get("allOf", []):
        if isinstance(b, dict):
            props |= set(b.get("properties", {}))
            req |= set(b.get("required", []) or [])
    return props, req


def run_resolve_checks():
    """Run every resolver-level check; returns (results, oracle_available)."""
    from schema_oracle import OracleUnavailable
    results = []
    for c in RESOLVE_CHECKS:
        try:
            rem_props, rem_req = _props_and_required(
                _resolve_schema(c.schema_rel, c.op, c.removed_direction))
            kept_props, kept_req = _props_and_required(
                _resolve_schema(c.schema_rel, c.op, c.kept_direction))
        except OracleUnavailable:
            return [], False
        ok_removed = c.prop not in rem_props and c.prop not in rem_req
        ok_kept = c.prop in kept_props and c.prop in kept_req
        ok = ok_removed and ok_kept
        detail = (f"resolver removes on {c.removed_direction}, retains (required) on "
                  f"{c.kept_direction}" if ok
                  else f"removed@{c.removed_direction}={ok_removed}, "
                       f"kept@{c.kept_direction}={ok_kept}")
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
