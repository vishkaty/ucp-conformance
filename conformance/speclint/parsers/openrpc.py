#!/usr/bin/env python3
"""
openrpc.py — read the required `meta` fields per method from an OpenRPC doc.

speclint's transport-parity rule compares required headers across transports. On
the MCP side, the cross-transport headers (ucp-agent, idempotency-key) are modelled
as fields of the `meta` parameter object, and "required" means membership in that
object's `required` list. A method's `meta` schema may state `required` inline,
add to it via `allOf`, and/or `$ref` the shared base `#/components/schemas/meta`;
all three contribute, matching how a JSON-Schema validator resolves the object.

Pure stdlib, read-only.
"""
from __future__ import annotations

_META_REF = "#/components/schemas/meta"


def _base_meta_required(openrpc: dict) -> set:
    meta = openrpc.get("components", {}).get("schemas", {}).get("meta", {})
    return set(meta.get("required", []))


def required_meta_by_method(openrpc: dict) -> dict:
    """Map method name -> set of required `meta` field names (fully resolved).

    Resolution mirrors JSON-Schema object composition: inline ``required`` plus
    every ``allOf`` branch's ``required``, plus the base ``meta`` schema's
    ``required`` whenever the method's ``meta`` schema ``$ref``s it.
    """
    base = _base_meta_required(openrpc)
    result = {}
    for method in openrpc.get("methods", []) or []:
        name = method.get("name")
        if not name:
            continue
        req = set()
        for param in method.get("params", []) or []:
            if param.get("name") != "meta":
                continue
            schema = param.get("schema", {}) or {}
            req |= set(schema.get("required", []))
            for branch in schema.get("allOf", []) or []:
                req |= set(branch.get("required", []))
                if branch.get("$ref") == _META_REF:
                    req |= base
            if schema.get("$ref") == _META_REF:
                req |= base
        result[name] = req
    return result
