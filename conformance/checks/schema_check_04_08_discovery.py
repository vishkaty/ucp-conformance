#!/usr/bin/env python3
"""
schema_check_04_08_discovery.py — 2026-04-08 schema-enforced checks for the
ERROR-ENVELOPE message-type rows (discovery/negotiation/error-envelope area).

The 04-08 message schemas (source/schemas/shopping/types/message*.json) are LEAF
schemas with no lifecycle annotations: the oracle validates payloads against them
as-is via `schema_oracle.validate_root` (--schema without --def), the same route
that unblocked ERR-002/003/004 at 01-23. Each check pins one normative MUST from
conformance/requirements/2026-04-08/error-envelope.json; the negative set is the
kill-rate proof and `controls` prove the rule is no stricter than the spec.

VERSION-LOCK: every ERR id here means something DIFFERENT (or nothing) in the
2026-01-11/2026-01-23 registers (e.g. ERR-005 there = requires_* severity ->
requires_escalation; ERR-010/025/026/031 do not exist). This module carries the
04_08 filename token and VERSION = "2026-04-08", so coverage/matrix.py attributes
its ids to 2026-04-08 only.

Run + gated by the `schema-04-08` gate (checks/run_schema_04_08.py auto-discovers
this module); skips honestly (exit 2) if the Rust oracle isn't built.
"""
import sys, pathlib
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))

VERSION = "2026-04-08"
# Check(id, req_ids, schema_rel, def_name, valid, negatives, op, direction, strict, controls)
# def_name=None -> the oracle validates the payload against the ROOT schema
# (validate_root). See schema_check_04_08.py for the field semantics.
Check = namedtuple("Check", "id req_ids schema_rel def_name valid negatives op direction strict controls")
Check.__new__.__defaults__ = ("read", None, False, ())

_ERR = "schemas/shopping/types/message_error.json"
_WARN = "schemas/shopping/types/message_warning.json"
_INFO = "schemas/shopping/types/message_info.json"
_MSG = "schemas/shopping/types/message.json"

# Canonical well-formed messages (codes are freeform strings per *_code.json —
# the enums are `examples`, not restrictions — so any string code is schema-legal).
ERROR_MSG = {"type": "error", "code": "payment_failed",
             "content": "The payment was declined.", "severity": "recoverable"}
WARNING_MSG = {"type": "warning", "code": "final_sale",
               "content": "This item is a final sale and cannot be returned."}
INFO_MSG = {"type": "info", "content": "Free shipping was applied to this order."}


def _mut(base, **kw):
    """Copy of a message with fields replaced (value None -> field removed)."""
    out = dict(base)
    for k, v in kw.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


CHECKS = [
    # ERR-002 — error message `type` MUST be the constant "error"
    # (message_error.json#L13: "const": "error").
    Check("error.message_type_const", ["ERR-002"],
          _ERR, None, ERROR_MSG,
          [_mut(ERROR_MSG, type="warning"),          # another discriminator value
           _mut(ERROR_MSG, type="Error"),            # const is case-exact
           _mut(ERROR_MSG, type=1),                  # non-string
           _mut(ERROR_MSG, type=None)],              # missing (type is required)
          direction="response"),
    # ERR-005 — error `content_type`, when present, MUST be one of plain|markdown
    # (message_error.json#L25 enum; default plain). Controls prove the field stays
    # OPTIONAL and that both enum members are accepted.
    Check("error.content_type_enum", ["ERR-005"],
          _ERR, None, _mut(ERROR_MSG, content_type="plain"),
          [_mut(ERROR_MSG, content_type="html"),
           _mut(ERROR_MSG, content_type="PLAIN"),    # enum is case-exact
           _mut(ERROR_MSG, content_type="text/plain"),
           _mut(ERROR_MSG, content_type=5)],         # non-string
          direction="response",
          controls=((_mut(ERROR_MSG, content_type="markdown"), "read"),
                    (ERROR_MSG, "read"))),           # omitted -> default plain
    # ERR-009 — warning messages MUST include type, code, content
    # (message_warning.json#L6 required). Control: severity is NOT required on
    # warnings (register note), and the optional annotation fields stay optional.
    Check("warning.required_fields", ["ERR-009"],
          _WARN, None, WARNING_MSG,
          [_mut(WARNING_MSG, type=None),
           _mut(WARNING_MSG, code=None),
           _mut(WARNING_MSG, content=None)],
          direction="response",
          controls=((_mut(WARNING_MSG, path="$.line_items[0]",
                          presentation="disclosure",
                          url="https://example.com/policy"), "read"),)),
    # ERR-010 — warning `type` MUST be the constant "warning"
    # (message_warning.json#L12).
    Check("warning.message_type_const", ["ERR-010"],
          _WARN, None, WARNING_MSG,
          [_mut(WARNING_MSG, type="error"),
           _mut(WARNING_MSG, type="info"),
           _mut(WARNING_MSG, type="Warning")],
          direction="response"),
    # ERR-025 — info messages MUST include type and content (message_info.json#L6
    # required). The VALID payload deliberately omits `code`: code is OPTIONAL for
    # info (unlike error/warning — register note); the control proves a code-bearing
    # info message is also legal.
    Check("info.required_fields", ["ERR-025"],
          _INFO, None, INFO_MSG,
          [_mut(INFO_MSG, type=None),
           _mut(INFO_MSG, content=None),
           {}],
          direction="response",
          controls=((_mut(INFO_MSG, code="free_shipping"), "read"),)),
    # ERR-026 — info `type` MUST be the constant "info" (message_info.json#L11).
    Check("info.message_type_const", ["ERR-026"],
          _INFO, None, INFO_MSG,
          [_mut(INFO_MSG, type="error"),
           _mut(INFO_MSG, type="warning"),
           _mut(INFO_MSG, type="Info")],
          direction="response"),
    # ERR-031 — a Message MUST be exactly one of error|warning|info (message.json#L7
    # oneOf over the three leaf schemas). Negatives match NO branch: an unknown
    # discriminator, an empty object, and an error-typed object missing the error
    # branch's required fields (which cannot fall back to warning/info because their
    # type consts differ). Controls prove all three branches are accepted.
    Check("message.oneof_discriminated", ["ERR-031"],
          _MSG, None, ERROR_MSG,
          [{"type": "notice", "content": "not a defined message type"},
           {},
           {"type": "error", "content": "missing code and severity"}],
          direction="response",
          controls=((WARNING_MSG, "read"),
                    (INFO_MSG, "read"))),
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
