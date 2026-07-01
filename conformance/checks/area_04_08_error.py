#!/usr/bin/env python3
"""
area_04_08_error.py — 2026-04-08 ERROR ENVELOPE fixture checks (schema oracle).

Fixture: error_response.valid.json — a status=error UCP envelope with a non-empty
messages[] carrying one well-formed error message. Its ucp.capabilities schema URL
points at error_response.json (a non-container leaf schema), so the oracle's
{op}_{direction} resolution returns that schema as-is and validates the payload
against it directly (op="read", response). See requirements/2026-04-08/error-envelope.json.

Each declared mutation MUST make the fixture schema-invalid (engine kill-rate):
- drop:messages / drop:ucp -> error_response.required (ucp, messages)
- set:messages=[]          -> messages.minItems 1
- drop:messages.0.<field>  -> message_error.required (type, code, content, severity)
- corrupt-json / empty     -> non-well-formed body is a precondition failure

NOTE: setting an out-of-enum severity in place is NOT used as a mutation — the
engine's `set:` operates only at the top level (`set:messages.0.severity=...` adds a
literal "messages.0.severity" key rather than editing the nested value, so it would
survive). The severity enum (ERR-003) is instead proven structurally by
error_response.invalid.json (severity "catastrophic") in the parity manifest; the
in-check kill for the severity field is drop:messages.0.severity.
"""
from schema_check import fixture_check   # noqa: E402

_FIX, _OP, _DIR = "error_response.valid.json", "read", "response"

CHECKS = [
    # ERR-028 ucp+messages required; ERR-029 ucp.status=error (dropping ucp removes it)
    fixture_check("error.response_envelope_schema", ["ERR-028", "ERR-029"], "MUST", "2026-04-08",
                  _FIX, _OP, _DIR,
                  ["drop:messages", "drop:ucp", "corrupt-json", "empty"]),
    # ERR-030 non-empty messages[]
    fixture_check("error.messages_non_empty", ["ERR-030"], "MUST", "2026-04-08",
                  _FIX, _OP, _DIR,
                  ["set:messages=[]", "drop:messages"]),
    # ERR-001 error message required fields; ERR-003 severity presence
    fixture_check("error.message_required_fields", ["ERR-001", "ERR-003"], "MUST", "2026-04-08",
                  _FIX, _OP, _DIR,
                  ["drop:messages.0.type", "drop:messages.0.code",
                   "drop:messages.0.content", "drop:messages.0.severity"]),
]
