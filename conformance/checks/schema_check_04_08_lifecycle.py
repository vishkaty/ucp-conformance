#!/usr/bin/env python3
"""
schema_check_04_08_lifecycle.py — 2026-04-08 schema-enforced checks for the
checkout-lifecycle + totals areas, validated by the official `ucp-schema` oracle.
Auto-discovered by run_schema_04_08.py (the `schema-04-08` gate).

Pattern copied from schema_check_04_08.py (same Check/RCheck contract; run() and
run_resolve_checks() return (results, oracle_available)). ONE documented extension:
checkout.json is a ROOT schema (no named $defs — the oracle validates it without
--def), so the def_name=None branch honors `strict` via a local root-strict helper
(the base file's strict path only existed for $def subtrees). Everything else is
verbatim.

Register rows covered (conformance/requirements/2026-04-08/):
  checkout-lifecycle.json  CHK-035, CHK-039
  totals.json              TOT-004, TOT-019, TOT-020, TOT-021
ID-DRIFT: CHK-035/CHK-039 name UNRELATED requirements in the 2026-01-23 register
(confirmation email / escalation message) and the TOT family does not exist there —
this module is 04-08-only (filename carries 04_08 for matrix attribution and
VERSION pins every oracle call).

Design notes per row:
  CHK-035  "id": ucp_request:omit — the official resolver REMOVES `id` from the
           request resolution of every op and --strict (additionalProperties:false
           on the resolved schema) then genuinely REJECTS an id-bearing request
           body. checkout.json's root is a SIMPLE object (no allOf), where strict
           is reliable. The response-direction control proves the rule is
           request-scoped (id stays legal — and required — on responses).
           A resolver-level RCheck additionally pins removal at ALL request ops.
  CHK-039  payment ucp_request {create/update: optional, complete: required} —
           the PAY-036@01-23 pattern (op/direction/controls): op=complete request
           without payment must be REJECTED; create/update without payment stay
           valid (controls prove optionality — the rule is lifecycle-scoped).
  TOT-004  totals.json if/then: a non-well-known `type` requires display_text;
           control proves well-known types stay display_text-optional.
  TOT-019  totals.json lines[] items: required [display_text, amount].
  TOT-020  total.json: required [type, amount] on every totals entry.
  TOT-021  price.json: required [amount, currency]; amount $ref amount.json
           (integer, minimum 0); currency pattern ^[A-Z]{3}$. Control: amount 0
           is legal ("Use 0 for free items").
"""
import sys, json, os, tempfile, pathlib
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))

VERSION = "2026-04-08"

# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, strict, controls)
# — same shape as schema_check_04_08.Check; def_name=None -> root-schema validation.
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction strict controls")
Check.__new__.__defaults__ = ("read", None, False, ())

_CHK = "schemas/shopping/checkout.json"
_LI = [{"item": {"id": "teapot_ceramic"}, "quantity": 1}]

# Minimal strict-valid 04-08 checkout RESPONSE (base schema only): proves `id` is
# legal (and required) in the response direction — CHK-035's omit is request-scoped.
_RESP_WITH_ID = {
    "ucp": {"version": VERSION,
            "capabilities": {"dev.ucp.shopping.checkout": [
                {"version": VERSION,
                 "schema": "https://ucp.dev/schemas/shopping/checkout.json"}]},
            "payment_handlers": {}},
    "id": "chk_123",
    "line_items": [{"id": "li_1",
                    "item": {"id": "teapot_ceramic", "title": "Teapot", "price": 1000},
                    "quantity": 1,
                    "totals": [{"type": "subtotal", "amount": 1000}]}],
    "status": "incomplete",
    "currency": "USD",
    "totals": [{"type": "subtotal", "amount": 1000}, {"type": "total", "amount": 1000}],
    "links": [{"type": "terms_of_service", "url": "https://spck.dev/fixture/tos"}],
}

_TOTALS = "schemas/shopping/types/totals.json"
_SUB = {"type": "subtotal", "amount": 2000}
_TOT = {"type": "total", "amount": 2100}

CHECKS = [
    # CHK-035 — top-level checkout id is OMITTED on request bodies (ucp_request:omit).
    # Strict-mode root validation: the resolver removes `id` from the request
    # resolution, so --strict rejects any request body that carries it.
    Check("checkout.request_id_omitted", ["CHK-035"],
          _CHK, None,
          {"line_items": _LI},
          [{"line_items": _LI, "id": "chk_abc123"},       # id echoed on update body
           {"line_items": _LI, "id": ""}],                # present-but-empty still forbidden
          op="update", direction="request", strict=True,
          controls=(({"line_items": _LI}, "create"),      # id-less create request valid
                    (_RESP_WITH_ID, "read", _CHK, None, "response"))),  # id legal on response

    # CHK-039 — payment is OPTIONAL on create/update and REQUIRED on complete
    # (checkout.json payment ucp_request lifecycle map). Oracle-resolved request
    # profile: op=complete requires payment; controls prove create/update do not.
    Check("checkout.payment_required_on_complete", ["CHK-039"],
          _CHK, None,
          {"payment": {"instruments": []}},
          [{},                                            # payment missing entirely
           {"payment": "tok_123"},                        # non-object payment
           {"payment": None}],                            # null payment
          op="complete", direction="request",
          controls=(({"line_items": _LI}, "create"),      # optional on create
                    ({"line_items": _LI}, "update"))),    # optional on update

    # TOT-004 — unknown (non-well-known) totals types MUST include display_text
    # (totals.json if/then). Control: a well-known type stays display_text-optional.
    Check("totals.unknown_type_requires_display_text", ["TOT-004"],
          _TOTALS, None,
          [_SUB, {"type": "gift_wrap", "display_text": "Gift wrap", "amount": 100}, _TOT],
          [[_SUB, {"type": "gift_wrap", "amount": 100}, _TOT]],  # display_text dropped
          op="read", direction="response",
          controls=(([_SUB, {"type": "tax", "amount": 100}, _TOT], "read"),)),

    # TOT-019 — each sub-line entry requires BOTH display_text and amount
    # (totals.json lines[] items required[]).
    Check("totals.subline_required_fields", ["TOT-019"],
          _TOTALS, None,
          [dict(_SUB, lines=[{"display_text": "Teapot", "amount": 1500},
                             {"display_text": "Mug", "amount": 500}]), _TOT],
          [[dict(_SUB, lines=[{"amount": 2000}]), _TOT],             # display_text missing
           [dict(_SUB, lines=[{"display_text": "Teapot"}]), _TOT],   # amount missing
           [dict(_SUB, lines=["not an object"]), _TOT]],             # non-object sub-line
          op="read", direction="response"),

    # TOT-020 — each top-level totals entry requires BOTH type and amount
    # (total.json required[]). Control: display_text stays optional.
    Check("totals.entry_required_fields", ["TOT-020"],
          "schemas/shopping/types/total.json", None,
          {"type": "tax", "amount": 100},
          [{"amount": 100},                               # type missing
           {"type": "tax"}],                              # amount missing
          op="read", direction="response",
          controls=(({"type": "fee", "amount": 50}, "read"),)),

    # TOT-021 — price requires a non-negative INTEGER amount (amount.json) and an
    # ISO 4217 currency (^[A-Z]{3}$). Control: amount 0 is legal (free items).
    Check("price.amount_and_currency", ["TOT-021"],
          "schemas/shopping/types/price.json", None,
          {"amount": 1000, "currency": "USD"},
          [{"currency": "USD"},                           # amount missing
           {"amount": 1000},                              # currency missing
           {"amount": -1, "currency": "USD"},             # negative amount
           {"amount": 10.5, "currency": "USD"},           # non-integer amount
           {"amount": 1000, "currency": "usd"},           # lowercase currency
           {"amount": 1000, "currency": "USDA"}],         # not a 3-letter code
          op="read", direction="response",
          controls=(({"amount": 0, "currency": "USD"}, "read"),)),
]

# Resolver-level check for CHK-035 on the ROOT checkout schema. The verbatim RCheck
# pattern (schema_check_04_08.py) proves per-op omit annotations by a kept-op
# retention; `id` is omitted at EVERY request op, so the kill-proof analog here is
# RESPONSE-direction retention: the official resolver must REMOVE the property from
# the request resolution of every op and RETAIN it in the response resolution (this
# fails if the resolver stopped resolving, or dropped the property globally).
#   RootRCheck(id, req_ids, schema_rel, prop, request_ops, response_op)
RootRCheck = namedtuple("RootRCheck", "id req_ids schema_rel prop request_ops response_op")

RESOLVE_CHECKS = [
    RootRCheck("checkout.id_omitted_all_request_ops", ["CHK-035"],
               _CHK, "id", ("create", "update", "complete"), "read"),
]


def _resolve_root(schema_rel, op, direction):
    """Resolve a ROOT schema (no --def) for a direction+op via the official resolver."""
    import subprocess
    import schema_oracle as so
    base = so.SCHEMA_BASE[VERSION]
    schema = base / schema_rel
    if not so.BIN.exists() or not schema.exists():
        raise so.OracleUnavailable(f"oracle or schema missing ({schema_rel})")
    args = [str(so.BIN), "resolve", str(schema), "--op", op,
            "--schema-local-base", str(base),
            "--request" if direction == "request" else "--response"]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise so.OracleUnavailable(f"resolve failed: {(r.stdout + r.stderr)[:200]}")
    return json.loads(r.stdout)


def _validate_root_strict(payload, schema_rel, op, direction):
    """Strict-mode ROOT-schema validation (--schema without --def, --strict true):
    the resolver removes ucp_request-omitted properties for the op, and strict then
    rejects them if present in the payload. Local because schema_oracle.validate_root
    has no strict parameter (kept additive-only for parallel-area safety)."""
    import subprocess
    import schema_oracle as so
    base = so.SCHEMA_BASE[VERSION]
    schema = base / schema_rel
    if not so.BIN.exists() or not schema.exists():
        raise so.OracleUnavailable(f"oracle or schema missing ({schema_rel})")
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        pathlib.Path(path).write_text(json.dumps(payload))
        args = [str(so.BIN), "validate", path, "--schema", str(schema), "--op", op,
                "--schema-local-base", str(base), "--strict", "true",
                "--request" if direction == "request" else "--response"]
        r = subprocess.run(args, capture_output=True, text=True)
        return (r.returncode == 0, (r.stdout + r.stderr).strip())
    finally:
        os.unlink(path)


def run_resolve_checks():
    """Run every resolver-level check; returns (results, oracle_available)."""
    from schema_oracle import OracleUnavailable
    results = []
    for c in RESOLVE_CHECKS:
        try:
            removed = [c.prop not in _resolve_root(c.schema_rel, op, "request")
                       .get("properties", {}) for op in c.request_ops]
            kept = c.prop in _resolve_root(c.schema_rel, c.response_op, "response") \
                .get("properties", {})
        except OracleUnavailable:
            return [], False
        ok = all(removed) and kept
        detail = (f"resolver removes on {'/'.join(c.request_ops)} requests, "
                  f"retains on response" if ok
                  else f"removed@request={removed}, kept@response={kept}")
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
                if c.strict:   # root schemas: strict via the local helper (see docstring)
                    return _validate_root_strict(payload, schema_rel, op,
                                                 direction or "response")
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
