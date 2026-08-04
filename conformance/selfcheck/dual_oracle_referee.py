#!/usr/bin/env python3
"""
dual_oracle_referee.py — an INDEPENDENT Python jsonschema referee for UCP schema
validation, built to cross-check the official Rust `ucp-schema` oracle
(schema_oracle.py). The two engines together are the dual-oracle gate
(validate_dual_oracle.py): every schema-validation check runs BOTH and ALARMS on
verdict divergence, so an oracle we otherwise trust blindly can no longer silently
pass a malformed payload.

WHY A SECOND ENGINE. The Rust oracle is the suite's schema-validation ORACLE
(SOURCES.lock.json schema_validator). This week it was proven to false-accept payment
instruments (ucp-schema#43, fix #44): its bundler leaves a `$ref: "#"` dangling when
inlining a fragment, so on the checkout payment path `payment.instruments[*]` is never
validated against the payment_instrument base schema — an instrument missing all of
id/handler_id/type validates clean. The bug was FOUND by cross-checking against an
independent referee; this module makes that cross-check permanent.

DESIGN.
  * Registry over ALL 78 vendored 04-08 schema files, keyed by their absolute `$id`
    (https://ucp.dev/schemas/...). The `referencing` library resolves every relative
    `$ref` (incl. bare `#`) against the CONTAINING resource's own `$id` — which is
    exactly the 2020-12 rule the Rust bundler gets wrong. So `#` inside
    payment_instrument.json resolves to payment_instrument.json's root here, and the
    base `required` is enforced. That is the whole point.
  * Lifecycle filtering (ucp_request / ucp_response annotations) is applied as an
    independent transform so the referee compares apples-to-apples with the Rust
    oracle's `--op`/`--request`/`--response` resolution. Semantics (derived from, and
    verified against, the official resolver — see referee_lifecycle_matches_resolver
    in validate_dual_oracle.py):
      - ucp_request applies ONLY in request direction; ucp_response ONLY in response.
      - value "omit"     -> drop the property from `properties` AND `required`.
        value "optional" -> drop the property from `required` (keep it allowed).
        value "required" -> add the property to `required`.
      - a per-op object {op: value} applies only when the current op is a key; an op
        ABSENT from the map is a NO-OP (base behaviour), matching the resolver.

This module is import-only (no side effects) and pip-installable-hermetic: it needs
`jsonschema>=4.26` + `referencing`, both already suite deps. It does NOT shell out.
"""
import json, pathlib, copy
from functools import lru_cache

try:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    _HAVE_JSONSCHEMA = True
except Exception:                                            # pragma: no cover
    _HAVE_JSONSCHEMA = False

ROOT = pathlib.Path(__file__).resolve().parents[2]
VENDOR = ROOT / "conformance" / ".vendor"
# Same schema-base map as the Rust oracle (schema_oracle.SCHEMA_BASE): each dir maps
# the https://ucp.dev/ site root, so a $id https://ucp.dev/schemas/<x> lives at
# <base>/schemas/<x>.
SCHEMA_BASE = {
    "2026-04-08": VENDOR / "ucp" / "source",
    "2026-01-23": VENDOR / "ucp-schemas" / "2026-01-23",
    "2026-01-11": VENDOR / "ucp-schemas" / "2026-01-11",
}

_LIFECYCLE_OPS = ("create", "update", "complete", "read")


class RefereeUnavailable(RuntimeError):
    """Raised when jsonschema/referencing or the schema base is absent -> caller SKIPs
    (never a false verdict)."""


def available():
    return _HAVE_JSONSCHEMA


def _resolve_annotation(value, op):
    """Return "omit"|"optional"|"required"|None for a ucp_request/ucp_response value
    under the current op. None => annotation does not apply (base behaviour)."""
    if isinstance(value, str):
        return value if value in ("omit", "optional", "required") else None
    if isinstance(value, dict):
        v = value.get(op)
        return v if v in ("omit", "optional", "required") else None
    return None


def _apply_lifecycle(node, op, direction):
    """Recursively transform a schema NODE in place for (op, direction): honour every
    ucp_request/ucp_response annotation on the properties of each object subschema.
    Mirrors the official resolver so the referee's verdict is comparable to the Rust
    oracle's --op/--request|--response resolution."""
    if isinstance(node, list):
        for x in node:
            _apply_lifecycle(x, op, direction)
        return
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        required = node.get("required", [])
        req = list(required) if isinstance(required, list) else []
        ann_key = "ucp_request" if direction == "request" else "ucp_response"
        for name in list(props.keys()):
            sub = props[name]
            if not isinstance(sub, dict):
                continue
            verdict = _resolve_annotation(sub.get(ann_key), op) if ann_key in sub else None
            if verdict == "omit":
                del props[name]
                req = [r for r in req if r != name]
            elif verdict == "optional":
                req = [r for r in req if r != name]
            elif verdict == "required":
                if name not in req:
                    req.append(name)
        if req or "required" in node:
            node["required"] = req
    # Recurse into every subschema-bearing keyword (properties already handled above,
    # but nested objects/defs/combinators still need it).
    for key, val in list(node.items()):
        if key in ("properties", "$defs", "definitions", "patternProperties"):
            if isinstance(val, dict):
                for v in val.values():
                    _apply_lifecycle(v, op, direction)
        elif key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            _apply_lifecycle(val, op, direction)
        elif key in ("items", "additionalProperties", "not", "if", "then", "else",
                     "contains", "unevaluatedItems", "unevaluatedProperties"):
            _apply_lifecycle(val, op, direction)


class Referee:
    """Independent Draft 2020-12 validator over the full pinned schema set for a spec
    version. Build once; validate many. Thread-unsafe (mutates cached registries by
    op/direction key, but only builds each once)."""

    def __init__(self, version="2026-04-08", schema_base=None):
        if not _HAVE_JSONSCHEMA:
            raise RefereeUnavailable("jsonschema/referencing not importable")
        base = pathlib.Path(schema_base) if schema_base else SCHEMA_BASE.get(version)
        if not base or not (base / "schemas").is_dir():
            raise RefereeUnavailable(f"no schema base for {version} at {base}")
        self.version = version
        self.base = base
        self._raw = {}                       # $id -> pristine schema dict
        for f in sorted((base / "schemas").rglob("*.json")):
            try:
                d = json.loads(f.read_text())
            except Exception:
                continue
            sid = d.get("$id")
            if sid:
                self._raw[sid] = d
        if not self._raw:
            raise RefereeUnavailable(f"no $id schemas found under {base}/schemas")
        self._registry_cache = {}            # (op,direction) -> Registry

    @property
    def schema_count(self):
        return len(self._raw)

    def _registry(self, op, direction):
        key = (op, direction)
        reg = self._registry_cache.get(key)
        if reg is not None:
            return reg
        resources = []
        for sid, schema in self._raw.items():
            filtered = copy.deepcopy(schema)
            if op is not None and direction is not None:
                _apply_lifecycle(filtered, op, direction)
            resources.append((sid, Resource(contents=filtered, specification=DRAFT202012)))
        reg = Registry().with_resources(resources)
        self._registry_cache[key] = reg
        return reg

    def validate(self, payload, schema_rel, def_name=None, op="read",
                 direction="response"):
        """Validate `payload` (a dict) against <schema_rel>[#/$defs/<def_name>] with
        lifecycle filtering for (op, direction). Returns (ok: bool, faults:
        list[(instance_path, keyword)]). instance_path is a '/'-joined pointer of the
        instance location at fault — the VERDICT-level detail the gate compares (never
        the human message text, which is cosmetic)."""
        sid = f"https://ucp.dev/{schema_rel.lstrip('/')}"
        if sid not in self._raw:
            # Accept an already-absolute $id too.
            if schema_rel in self._raw:
                sid = schema_rel
            else:
                raise RefereeUnavailable(f"schema {schema_rel} ($id {sid}) not in registry")
        ref = sid + (f"#/$defs/{def_name}" if def_name else "")
        reg = self._registry(op, direction)
        validator = jsonschema.Draft202012Validator({"$ref": ref}, registry=reg)
        faults = []
        for e in validator.iter_errors(payload):
            path = "/" + "/".join(str(p) for p in e.absolute_path)
            faults.append((path, e.validator))
        # De-dupe + sort so the fault set is a stable, comparable SEMANTIC signature.
        faults = sorted(set(faults))
        return (len(faults) == 0, faults)


@lru_cache(maxsize=None)
def get_referee(version="2026-04-08"):
    return Referee(version=version)
