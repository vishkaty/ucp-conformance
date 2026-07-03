#!/usr/bin/env python3
"""
schema_check_01_11_01_23.py — OLD-VERSIONS (2026-01-11 / 2026-01-23) schema-enforced
checks, validated by the official `ucp-schema` oracle (schema_oracle.py) in the
schema_check_01_23.py / schema_check_04_08.py pattern: each check pins a normative
MUST the pinned schema itself enforces — the VALID fixture must pass the oracle AND
every NEGATIVE (one per defect the MUST forbids) must be rejected. The negative set
IS the kill-rate proof; `controls` are positive guards proving a rule is no stricter
than the spec.

Version scoping: every check carries an explicit `versions` tuple (introspected by
coverage/matrix.py; the file name carries BOTH 01_11 and 01_23 tokens, which bounds
attribution to those versions). The 2026-01-11 and 2026-01-23 register rows cited
here were verified TEXTUALLY IDENTICAL at both versions (or the check is locked to
the single version where the row exists); ids like DSC-013/DSC-014/DSC-020 and
ERR-002/003/004 are 2026-01-11-locked here because schema_check_01_23.py already
covers them at 2026-01-23.

RESOLVE_CHECKS use the official RESOLVER as the oracle for ucp_request lifecycle
annotations (the 01-era CHK-017/019/020/021 + DSC-015/DSC-021 omit family): a
property annotated omit for an op MUST be REMOVED from that op's request resolution
and RETAINED where the annotation keeps it — the retained/response assertions are
the kill-proof analog (they fail if the resolver stopped resolving, resolved the
wrong schema, or dropped the property globally rather than per-op).

Run this file directly as a gate (skips honestly with exit 2 without the Rust
oracle). run_suite wiring: a `schema-01-11-01-23` gate line (merge coordinator).
"""
import sys, pathlib
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))

V_BOTH = ("2026-01-11", "2026-01-23")
V_11 = ("2026-01-11",)
V_23 = ("2026-01-23",)

# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, controls, versions)
#   def_name None -> ROOT schema (validate_root); else named $def (validate_against).
#   Each check is validated at EVERY version in `versions` (schema base per version).
Check = namedtuple("Check",
                   "id req_ids schema_rel def_name valid negatives op direction controls versions")
Check.__new__.__defaults__ = ("read", None, (), V_BOTH)


def _msg_err(**over):
    """A minimal valid 01-era Message Error (required: type/code/content/severity)."""
    m = {"type": "error", "code": "invalid", "content": "Quantity exceeds stock",
         "severity": "recoverable"}
    for k, v in over.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    return m


# SD-JWT+kb shaped string (matches the checkout_mandate pattern incl. ~disclosures)
_MANDATE = "eyJhbGciOiJFUzI1NiJ9.eyJjaGVja291dCI6MX0.c2ln~ZGlzY2xvc3VyZQ"
_PAY = {"instruments": [{"id": "instr_1", "handler_id": "h1", "type": "card",
                         "credential": {"type": "token", "token": "tok"}}]}
_INSTR = {"id": "instr_1", "handler_id": "h1", "type": "card"}
_FUL_OPT = {"id": "opt_std", "title": "Standard shipping",
            "totals": [{"type": "total", "amount": 500}]}

CHECKS = [
    # ---- errors (message_error.json is a ROOT schema at both 01-era versions) ----
    # ERR-001 — all four of type/code/content/severity are REQUIRED on Message Error.
    Check("error.required_fields", ["ERR-001"],
          "schemas/shopping/types/message_error.json", None,
          _msg_err(),
          [_msg_err(type=None),                          # each required field dropped
           _msg_err(code=None),
           _msg_err(content=None),
           _msg_err(severity=None)]),
    # ERR-002/003/004 @2026-01-11 ONLY (schema_check_01_23.py already covers them at
    # 2026-01-23; the 01-11 message_error.json is textually identical — same const,
    # open string code, same 3-value severity enum).
    Check("error.type_const_error_01_11", ["ERR-002"],
          "schemas/shopping/types/message_error.json", None,
          _msg_err(),
          [_msg_err(type="warning"), _msg_err(type="info"),
           _msg_err(type=""), _msg_err(type=None)],
          versions=V_11),
    Check("error.code_is_open_string_01_11", ["ERR-003"],
          "schemas/shopping/types/message_error.json", None,
          _msg_err(),
          [_msg_err(code=123), _msg_err(code=None),
           _msg_err(code=["invalid"]), _msg_err(code=True)],
          controls=((_msg_err(code="some_freeform_merchant_code"), "read"),),
          versions=V_11),
    Check("error.severity_enum_3_01_11", ["ERR-004"],
          "schemas/shopping/types/message_error.json", None,
          _msg_err(),
          [_msg_err(severity="unrecoverable"),           # NOT in the 01-era enum
           _msg_err(severity="critical"),
           _msg_err(severity="escalation"),              # checkout.md's bad name (ERR-008)
           _msg_err(severity=None)],
          controls=((_msg_err(severity="requires_buyer_input"), "read"),
                    (_msg_err(severity="requires_buyer_review"), "read")),
          versions=V_11),
    # ---- discounts (2026-01-11-locked twins of the shipped 01-23 checks) --------
    Check("discount.allocation_shape_01_11", ["DSC-014"],
          "schemas/shopping/discount.json", "allocation",
          {"path": "$.line_items[0]", "amount": 100},
          [{"path": "$.line_items[0]"},                  # missing amount
           {"amount": 100},                              # missing path
           {"path": "$.x", "amount": -100},              # negative (minimum 0)
           {"path": "$.x", "amount": 10.5}],             # non-integer
          versions=V_11),
    Check("discount.applied_method_enum_01_11", ["DSC-013"],
          "schemas/shopping/discount.json", "applied_discount",
          {"title": "Spring sale", "amount": 500, "method": "each"},
          [{"title": "s", "amount": 500, "method": "proportional"},
           {"title": "s", "amount": 500, "method": "EACH"},
           {"title": "s", "amount": 500, "method": 123}],
          versions=V_11),
    Check("buyer_consent.boolean_states_01_11", ["DSC-020"],
          "schemas/shopping/buyer_consent.json", "consent",
          {"analytics": True, "marketing": False, "preferences": True, "sale_of_data": False},
          [{"marketing": "true"}, {"analytics": 1},
           {"sale_of_data": None}, {"preferences": "yes"}],
          versions=V_11),
    # ---- payment credentials/instruments (identical types at both versions) -----
    # PAY-008/PAY-010 — a payment instrument requires id + handler_id + type
    # (handler_id is the key-routing anchor: PAY-010's Handler ID Routing rule is
    # enforced through this required field). The required[] set lives in
    # payment_instrument.json at 2026-01-23 and payment_instrument_base.json at
    # 2026-01-11 — hence one version-locked check per schema location.
    Check("payment.instrument_required_fields", ["PAY-008", "PAY-010"],
          "schemas/shopping/types/payment_instrument.json", None,
          dict(_INSTR),
          [{"handler_id": "h1", "type": "card"},         # id missing
           {"id": "i1", "type": "card"},                 # handler_id missing
           {"id": "i1", "handler_id": "h1"}],            # type missing
          versions=V_23),
    Check("payment.instrument_required_fields_01_11", ["PAY-008", "PAY-010"],
          "schemas/shopping/types/payment_instrument_base.json", None,
          dict(_INSTR),
          [{"handler_id": "h1", "type": "card"},
           {"id": "i1", "type": "card"},
           {"id": "i1", "handler_id": "h1"}],
          versions=V_11),
    # PAY-015 — card credential: type (const 'card') + card_number_type (enum).
    Check("payment.card_credential_shape", ["PAY-015"],
          "schemas/shopping/types/card_credential.json", None,
          {"type": "card", "card_number_type": "fpan", "number": "4111111111111111"},
          [{"card_number_type": "fpan", "number": "4111111111111111"},   # type missing
           {"type": "token", "card_number_type": "fpan",
            "number": "4111111111111111"},                              # const violated
           {"type": "card", "number": "4111111111111111"},              # card_number_type missing
           {"type": "card", "card_number_type": "visa",
            "number": "4111111111111111"}]),                            # outside enum
    # PAY-016 — token credential: type + token required. Validated in the REQUEST
    # direction: `token` is ucp_response:omit, so the response resolution drops the
    # requirement — the MUST binds the platform's complete-request credential.
    Check("payment.token_credential_shape", ["PAY-016"],
          "schemas/shopping/types/token_credential.json", None,
          {"type": "token", "token": "tok_123"},
          [{"token": "tok_123"},                         # type missing
           {"type": "token"},                            # token missing
           {"type": "token", "token": 123}],             # non-string token
          op="complete", direction="request"),
    # PAY-017 — a binding requires checkout_id (token bound to THIS checkout).
    Check("payment.binding_requires_checkout_id", ["PAY-017"],
          "schemas/shopping/types/binding.json", None,
          {"checkout_id": "chk_1"},
          [{},                                           # checkout_id missing
           {"checkout_id": 123}],                        # non-string
          op="complete", direction="request"),
    # PAY-029 — autonomous AP2 completion produces two mandate artifacts. The
    # checkout_mandate half is schema-REQUIRED on complete (composed def at 01-23;
    # ap2_complete_request at 01-11) with the SD-JWT+kb pattern enforced; the
    # payment_mandate half rides in payment.instruments[].credential.token, whose
    # SHAPE anchor is payment.token_credential_shape (PAY-016) — the 01-era schemas
    # do not make credential presence required, so no absent-credential negative
    # can honestly be claimed here.
    Check("payment.ap2_two_mandates", ["PAY-029"],
          "schemas/shopping/ap2_mandate.json", "checkout",
          {"payment": _PAY, "ap2": {"checkout_mandate": _MANDATE}},
          [{"payment": _PAY},                            # ap2 artifact absent
           {"payment": _PAY, "ap2": {}},                 # checkout_mandate absent
           {"payment": _PAY,
            "ap2": {"checkout_mandate": "not a jwt!!"}}],  # SD-JWT pattern violated
          op="complete", direction="request", versions=V_23),
    Check("payment.ap2_two_mandates_01_11", ["PAY-029"],
          "schemas/shopping/ap2_mandate.json", "ap2_complete_request",
          {"checkout_mandate": _MANDATE},
          [{},                                           # checkout_mandate absent
           {"checkout_mandate": "not a jwt!!"}],         # SD-JWT pattern violated
          op="complete", direction="request", versions=V_11),
    # PAY-003 (2026-01-23 only) — a PLATFORM-schema payment handler declaration
    # requires spec + schema (payment_handler.json $defs/platform_schema, which also
    # pulls base's required id). Client-authored profile content -> schema-enforced.
    Check("payment.handler_platform_spec_schema", ["PAY-003"],
          "schemas/payment_handler.json", "platform_schema",
          {"id": "h1", "version": "2026-01-23",
           "spec": "https://platform.example/handlers/pay",
           "schema": "https://platform.example/handlers/pay/schema.json"},
          [{"id": "h1", "version": "2026-01-23",
            "schema": "https://platform.example/handlers/pay/schema.json"},  # spec missing
           {"id": "h1", "version": "2026-01-23",
            "spec": "https://platform.example/handlers/pay"},                # schema missing
           {"version": "2026-01-23", "spec": "https://platform.example/handlers/pay",
            "schema": "https://platform.example/handlers/pay/schema.json"}],  # id missing (base)
          versions=V_23),
    # ---- fulfillment ------------------------------------------------------------
    # FUL-009 — options[].title is REQUIRED (fulfillment_option.json required[]).
    Check("fulfillment.option_title_required", ["FUL-009"],
          "schemas/shopping/types/fulfillment_option.json", None,
          dict(_FUL_OPT),
          [{k: v for k, v in _FUL_OPT.items() if k != "title"},   # title missing
           {**_FUL_OPT, "title": 123}]),                          # non-string title
]

# ---- resolver-level checks (the official resolver arbitrates ucp_request) -------
# RCheck: lifecycle-omit on an allOf-composed EXTENSION def (04-08 pattern).
RCheck = namedtuple("RCheck", "id req_ids schema_rel def_name prop removed_op kept_ops versions")
# RootCheck: lifecycle annotations on the checkout ROOT schema (no named $def).
# (Exported as RESOLVE_CHECKS_ROOT so coverage/matrix.py introspection collects it.)
#   absent_ops  -> prop MUST be REMOVED from those ops' request resolutions
#   present_ops -> prop retained in those ops' request resolutions
#   required_ops / optional_ops -> prop in / not in the resolved `required` list
#   response_present -> prop present in the response resolution (kill-proof analog)
RootCheck = namedtuple("RootCheck",
                       "id req_ids schema_rel prop absent_ops present_ops "
                       "required_ops optional_ops response_present versions")
RootCheck.__new__.__defaults__ = ((), (), (), (), True, V_BOTH)

RESOLVE_CHECKS = [
    # DSC-015 — discounts optional on create/update, OMITTED on complete requests.
    RCheck("discount.omitted_on_complete_01_23", ["DSC-015"],
           "schemas/shopping/discount.json", "checkout",
           "discounts", "complete", ("create", "update"), V_23),
    # DSC-021 — buyer (carrying consent) omitted on complete (consent never gates
    # completion); optional on create/update.
    RCheck("consent.buyer_omitted_on_complete_01_23", ["DSC-021"],
           "schemas/shopping/buyer_consent.json", "checkout",
           "buyer", "complete", ("create", "update"), V_23),
]

RESOLVE_CHECKS_ROOT = [
    # CHK-017 — the ucp envelope is response-only (ucp_request: omit on every op).
    RootCheck("checkout.ucp_omitted_on_requests", ["CHK-017"],
              "schemas/shopping/checkout.json", "ucp",
              absent_ops=("create", "update", "complete")),
    # CHK-019 (2026-01-23 only) — currency is merchant-determined, omitted on all
    # request ops. (At 2026-01-11 currency is REQUIRED on requests — different rule,
    # hence the version lock.)
    RootCheck("checkout.currency_response_only_01_23", ["CHK-019"],
              "schemas/shopping/checkout.json", "currency",
              absent_ops=("create", "update", "complete"), versions=V_23),
    # CHK-020 — status/totals/messages/links/expires_at/continue_url/order are all
    # response-only (one RootCheck per property, single citation).
    *[RootCheck(f"checkout.{p}_response_only", ["CHK-020"],
                "schemas/shopping/checkout.json", p,
                absent_ops=("create", "update", "complete"))
      for p in ("status", "totals", "messages", "links",
                "expires_at", "continue_url", "order")],
    # CHK-021 (2026-01-23 only) — payment optional on create/update, REQUIRED on
    # complete requests (ucp_request per-op requiredness).
    RootCheck("checkout.payment_required_on_complete_01_23", ["CHK-021"],
              "schemas/shopping/checkout.json", "payment",
              present_ops=("create", "update", "complete"),
              required_ops=("complete",), optional_ops=("create", "update"),
              versions=V_23),
]


def _va(c, payload, op, version):
    from schema_oracle import validate_against, validate_root
    if c.def_name is None:
        return validate_root(payload, c.schema_rel, op=op, version=version,
                             direction=c.direction or "response")
    return validate_against(payload, c.schema_rel, c.def_name,
                            op=op, version=version, direction=c.direction)


def run():
    """Run every payload check at every version it is scoped to.
    Returns (results, oracle_available); results = [(check, version, ok, detail)]."""
    from schema_oracle import OracleUnavailable
    results = []
    for c in CHECKS:
        for v in c.versions:
            try:
                ok_valid, dv = _va(c, c.valid, c.op, v)
                neg_ok = [_va(c, n, c.op, v)[0] for n in c.negatives]
                ctrl_ok = all(_va(c, p, o, v)[0] for p, o in c.controls)
            except OracleUnavailable:
                return [], False
            killed_all = ok_valid and ctrl_ok and not any(neg_ok)
            surviving = sum(1 for x in neg_ok if x)
            detail = ("clean-pass + kill-safe" if killed_all
                      else f"valid_ok={ok_valid}, ctrl_ok={ctrl_ok}, "
                           f"{surviving}/{len(c.negatives)} mutants SURVIVED")
            results.append((c, v, killed_all, detail))
    return results, True


def _ext_props(resolved, def_name):
    node = resolved.get("$defs", {}).get(def_name, resolved)
    props = set(node.get("properties", {}))
    for b in node.get("allOf", []):
        if isinstance(b, dict):
            props |= set(b.get("properties", {}))
    return props


def run_resolve_checks():
    """Resolver-level checks (extension defs + the checkout root's lifecycle map)."""
    from schema_oracle import resolve_def, resolve_root, OracleUnavailable
    results = []
    for c in RESOLVE_CHECKS:
        for v in c.versions:
            try:
                removed = _ext_props(resolve_def(c.schema_rel, c.def_name, c.removed_op,
                                                 version=v, direction="request"),
                                     c.def_name)
                kept = [_ext_props(resolve_def(c.schema_rel, c.def_name, op,
                                               version=v, direction="request"),
                                   c.def_name)
                        for op in c.kept_ops]
            except OracleUnavailable:
                return [], False
            ok = (c.prop not in removed) and all(c.prop in k for k in kept)
            detail = (f"resolver removes on {c.removed_op}, retains on "
                      f"{'/'.join(c.kept_ops)}" if ok
                      else f"removed@{c.removed_op}={c.prop not in removed}, "
                           f"kept@rest={[c.prop in k for k in kept]}")
            results.append((c, v, ok, detail))
    for c in RESOLVE_CHECKS_ROOT:
        for v in c.versions:
            try:
                req = {op: resolve_root(c.schema_rel, op, version=v, direction="request")
                       for op in set(c.absent_ops) | set(c.present_ops)
                       | set(c.required_ops) | set(c.optional_ops)}
                resp = resolve_root(c.schema_rel, "read", version=v,
                                    direction="response")
            except OracleUnavailable:
                return [], False
            fails = []
            for op in c.absent_ops:
                if c.prop in req[op].get("properties", {}):
                    fails.append(f"present@{op}-request")
            for op in c.present_ops:
                if c.prop not in req[op].get("properties", {}):
                    fails.append(f"absent@{op}-request")
            for op in c.required_ops:
                if c.prop not in (req[op].get("required") or []):
                    fails.append(f"not-required@{op}")
            for op in c.optional_ops:
                if c.prop in (req[op].get("required") or []):
                    fails.append(f"required@{op}")
            if c.response_present and c.prop not in resp.get("properties", {}):
                fails.append("missing-from-response-resolution")
            ok = not fails
            detail = ("root resolver honors the lifecycle map "
                      "(+ response retention kill-proof)" if ok else "; ".join(fails))
            results.append((c, v, ok, detail))
    return results, True


if __name__ == "__main__":
    res, avail = run()
    if avail:
        res2, avail = run_resolve_checks()
        res += res2
    if not avail:
        print("oracle unavailable — skip"); sys.exit(2)
    allok = True
    for c, v, ok, detail in res:
        print(f"  {'✓' if ok else '✗'} {c.id} ({','.join(c.req_ids)}) [{v[5:]}]: {detail}")
        allok = allok and ok
    print("PASS" if allok else "FAIL"); sys.exit(0 if allok else 1)
