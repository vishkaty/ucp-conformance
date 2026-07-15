#!/usr/bin/env python3
"""
openapi.py — read required *header* parameters per operation from an OpenAPI doc.

speclint's transport-parity rule compares, for one logical operation, the headers
a transport makes REQUIRED. On the REST side that is an OpenAPI operation's
`parameters` entries with `in: header` and `required: true`. Parameters are often
`$ref`s into `components.parameters`, and a path item may carry path-level
`parameters` shared by every verb; both are resolved here.

Pure stdlib, read-only. Returns a plain dict so callers stay trivially testable.
"""
from __future__ import annotations

_VERBS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


def _resolve_param(param, components):
    """Return the concrete parameter object, following a single components $ref."""
    ref = param.get("$ref", "")
    prefix = "#/components/parameters/"
    if ref.startswith(prefix):
        return components.get(ref[len(prefix):], {})
    return param


def required_headers_by_operation(openapi: dict) -> dict:
    """Map operationId -> set of required header names.

    A header counts as required for an operation when a resolved parameter has
    ``in == "header"`` and ``required is True``, whether it is declared on the
    operation itself or inherited from the path item's shared ``parameters``.
    Operations without an ``operationId`` are keyed by ``"{METHOD} {path}"`` so
    nothing is silently dropped.
    """
    components = openapi.get("components", {}).get("parameters", {})

    def collect(params):
        out = set()
        for p in params or []:
            rp = _resolve_param(p, components)
            if rp.get("in") == "header" and rp.get("required") is True:
                name = rp.get("name")
                if name:
                    out.add(name)
        return out

    result = {}
    for path, item in (openapi.get("paths") or {}).items():
        shared = collect(item.get("parameters"))
        for verb, op in item.items():
            if verb.lower() not in _VERBS or not isinstance(op, dict):
                continue
            key = op.get("operationId") or f"{verb.upper()} {path}"
            result[key] = shared | collect(op.get("parameters"))
    return result
