#!/usr/bin/env python3
"""
schema_oracle.py — thin wrapper over the OFFICIAL ucp-schema validator.

Per the methodology (and the red-team's blocker #2) we do NOT hand-roll JSON-Schema
validation — divergence on $ref/$defs/allOf composition is a false-PASS source.
Instead we shell out to the vendored, pinned `ucp-schema` Rust binary and treat its
verdict as the schema-validation oracle.

Build (once):  cd conformance/.vendor/ucp-schema && cargo build --release
Schema base:   the pinned ucp repo's source/schemas for the target spec version.

`schema_parity()` proves our invocation is faithful by running the validator over
ucp-schema's own fixtures/valid (must pass) and fixtures/invalid (must fail). If any
valid fixture fails or any invalid fixture passes, the oracle wiring is wrong and
callers MUST treat schema verdicts as `inconclusive`, never `pass`.
"""
import json, subprocess, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
VENDOR = ROOT / "conformance" / ".vendor"
# The PINNED build (SOURCES.lock schema_validator): the 2026-04-08-layout oracle and the
# module-level default. D4-04 (decision 7b): the oracle is PER VERSION — bin_for(version)
# resolves through conformance/ci/oracle_manifest.json (one merged SHA per layout; the
# 2026-08-25 layout runs the merged-main b52518f5 build that no longer aborts on the
# `$ref: "#"` self-root `--def` path). BIN stays the pinned path for callers that only
# ask "is an oracle built at all".
BIN = VENDOR / "ucp-schema" / "target" / "release" / "ucp-schema"
MANIFEST = ROOT / "conformance" / "ci" / "oracle_manifest.json"
DEFAULT_VERSION = "2026-04-08"


def load_manifest(path=MANIFEST):
    return json.loads(pathlib.Path(path).read_text())


def manifest_entry(version, manifest=None):
    """The manifest's per_version entry for `version`, aliases resolved (an `alias_of`
    entry points at the layout it shares). Raises OracleUnavailable for an unknown
    version or a dangling alias."""
    m = manifest or load_manifest()
    pv = m.get("per_version") or {}
    seen = set()
    while True:
        e = pv.get(version)
        if e is None:
            raise OracleUnavailable(f"no oracle manifest entry for {version} in {MANIFEST}")
        if "alias_of" not in e:
            return e
        if version in seen:
            raise OracleUnavailable(f"alias cycle at {version} in {MANIFEST}")
        seen.add(version)
        version = e["alias_of"]


def bin_for(version=None, manifest=None):
    """Path of the ucp-schema binary that serves `version`'s layout (per the manifest)."""
    if version is None:
        return BIN
    return VENDOR / manifest_entry(version, manifest)["bin"]
# spec version -> local "site root" dir that maps https://ucp.dev/ (so the
# validator resolves a capability schema URL https://ucp.dev/schemas/<x> to
# <base>/schemas/<x>). Each dir therefore CONTAINS a schemas/ subdir.
SCHEMA_BASE = {
    "2026-08-25": VENDOR / "ucp-2026-08-25" / "source",
    "2026-04-08": VENDOR / "ucp" / "source",
    "2026-01-23": VENDOR / "ucp-schemas" / "2026-01-23",   # git-extracted source/ tree
    "2026-01-11": VENDOR / "ucp-schemas" / "2026-01-11",
}
FIXTURES = ROOT / "conformance" / "selfcheck" / "fixtures"

class OracleUnavailable(RuntimeError):
    pass

# return code of the most recent oracle invocation: 0 valid, 1 invalid, anything else
# (rc 2 usage/resolution error, 134 / -6 abort on the `$ref: "#"` self-root blind spot,
# ucp-schema#45) is a CRASH, not a verdict — validate_dual_oracle.rust_verdict reads it.
LAST_RC = None

def _run(args, version=None):
    """Invoke the oracle that serves `version` (None -> the pinned default build)."""
    global LAST_RC
    b = bin_for(version)
    if not b.exists():
        # parents[2] may not exist for an unusual BIN path; don't let the message crash.
        hint = str(b.parents[2]) if len(b.parents) > 2 else str(b.parent)
        raise OracleUnavailable(
            f"ucp-schema binary not built at {b}. Run: cd {hint} && cargo build --release")
    r = subprocess.run([str(b), *args], capture_output=True, text=True)
    LAST_RC = r.returncode
    return r

def validate(payload_path, op, *, request=False, response=False,
             version="2026-04-08", schema_base=None, strict=False):
    """Validate a payload file. Returns (ok: bool, detail: str).
    Direction: pass request=True for a request body, response=True for a response
    body. The validator only auto-infers direction when the payload carries
    meta.profile (request) or ucp.capabilities (response); otherwise it errors, so
    callers should be explicit.
    ok=True  -> schema-valid (exit 0); ok=False -> schema-invalid (detail = reason).
    Raises OracleUnavailable if the binary isn't built (caller -> 'inconclusive')."""
    base = pathlib.Path(schema_base) if schema_base else SCHEMA_BASE.get(version)
    args = ["validate", str(payload_path), "--op", op,
            "--schema-local-base", str(base)]
    if request:
        args.append("--request")
    elif response:
        args.append("--response")
    if strict:
        args.append("--strict")
    r = _run(args, version=version)
    return (r.returncode == 0, (r.stdout + r.stderr).strip())

def _ucp_schema_path(base):
    """Locate the schema that validates a served `{"ucp": {...}}` profile
    document under a schema base dir. Through 2026-04-08/01-23/01-11, ucp.json
    carried the `{ucp: ...}` wrapper itself (its business_schema/platform_schema
    $defs require top-level `ucp`). The 2026-08-25 hierarchy reorg (#723) split
    that wrapper out into a dedicated profile.json (whose business_schema/
    platform_schema $defs require `ucp` and $ref ucp.json for the unwrapped
    metadata) — ucp.json's own $defs no longer require the wrapper. So prefer
    profile.json when present and fall back to ucp.json for older generations
    that never had the split, rather than hard-coding the choice per version."""
    for cand in (
        base / "schemas" / "profile.json", base / "source" / "schemas" / "profile.json",
        base / "schemas" / "ucp.json", base / "source" / "schemas" / "ucp.json",
    ):
        if cand.exists():
            return cand
    return None

def validate_profile(profile, version="2026-01-23", role="business", def_name=None):
    """Validate a discovered /.well-known/ucp document against the official profile
    schema (ucp.json, $def {role}_schema) using the ucp-schema validator.
      role="business" for a merchant profile, "platform" for an agent profile.
      def_name overrides the $def selection for schema generations whose profile def
      is not named {role}_schema — 2026-01-11's is `discovery_profile` (additive
      parameter for the 01-11 fixture mode; existing callers are unaffected).
    Returns (ok: bool, detail: str). Raises OracleUnavailable if the binary or the
    version's schema base isn't present (caller -> inconclusive / not-tested)."""
    import tempfile, os
    base = SCHEMA_BASE.get(version)
    schema = _ucp_schema_path(base) if base else None
    if not base or not schema:
        raise OracleUnavailable(f"no ucp.json profile schema for {version} under {base}")
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        pathlib.Path(path).write_text(json.dumps(profile))
        r = _run(["validate", path, "--schema", str(schema),
                  "--def", def_name or f"{role}_schema",
                  "--op", "read", "--schema-local-base", str(base)], version=version)
        return (r.returncode == 0, (r.stdout + r.stderr).strip())
    finally:
        os.unlink(path)

def resolve_def(schema_rel, def_name, op, version="2026-04-08", direction="request"):
    """Resolve a schema $def for a direction+op via the official resolver and return
    the parsed JSON (the authority on ucp_request/ucp_response annotation semantics —
    e.g. a property annotated complete:"omit" is REMOVED from the op=complete request
    resolution). Raises OracleUnavailable if the binary/base is absent."""
    base = SCHEMA_BASE.get(version)
    schema = (base / schema_rel) if base else None
    if not base or not schema or not schema.exists():
        raise OracleUnavailable(f"schema {schema_rel} for {version} not found under {base}")
    args = ["resolve", str(schema), "--def", def_name, "--op", op,
            "--schema-local-base", str(base)]
    if direction == "request":
        args.append("--request")
    elif direction == "response":
        args.append("--response")
    r = _run(args, version=version)
    if r.returncode != 0:
        raise OracleUnavailable(f"resolve failed: {(r.stdout + r.stderr)[:200]}")
    return json.loads(r.stdout)


def resolve_root(schema_rel, op, version="2026-01-23", direction="request"):
    """Resolve a ROOT schema (no --def; e.g. shopping/checkout.json) for a
    direction+op via the official resolver and return the parsed JSON. This is the
    authority on ucp_request lifecycle annotations at the checkout ROOT (the 01-era
    CHK-017/019/020 omit family). Raises OracleUnavailable if the binary/base is
    absent. Additive companion to resolve_def (which requires a named $def)."""
    base = SCHEMA_BASE.get(version)
    schema = (base / schema_rel) if base else None
    if not base or not schema or not schema.exists():
        raise OracleUnavailable(f"schema {schema_rel} for {version} not found under {base}")
    args = ["resolve", str(schema), "--op", op, "--schema-local-base", str(base)]
    if direction == "request":
        args.append("--request")
    elif direction == "response":
        args.append("--response")
    r = _run(args, version=version)
    if r.returncode != 0:
        raise OracleUnavailable(f"resolve failed: {(r.stdout + r.stderr)[:200]}")
    return json.loads(r.stdout)


def validate_against(payload, schema_rel, def_name, op="read", version="2026-04-08",
                     direction=None, strict=False):
    """Validate a payload object against an explicit schema file + $def under a version's
    schema base. schema_rel is relative to <base> (e.g. 'schemas/shopping/catalog_search.json').
    direction="request"/"response" applies the ucp_request lifecycle filtering for the
    given op (e.g. ap2 required-on-complete); None leaves the validator's default.
    Returns (ok, detail); raises OracleUnavailable if the binary/base is absent."""
    import tempfile, os
    base = SCHEMA_BASE.get(version)
    schema = (base / schema_rel) if base else None
    if not base or not schema or not schema.exists():
        raise OracleUnavailable(f"schema {schema_rel} for {version} not found under {base}")
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        pathlib.Path(path).write_text(json.dumps(payload))
        args = ["validate", path, "--schema", str(schema), "--def", def_name,
                "--op", op, "--schema-local-base", str(base)]
        if strict:
            args += ["--strict", "true"]
        if direction == "request":
            args.append("--request")
        elif direction == "response":
            args.append("--response")
        r = _run(args, version=version)
        return (r.returncode == 0, (r.stdout + r.stderr).strip())
    finally:
        os.unlink(path)

def validate_nested_def(payload, schema_rel, def_path, op="read", version="2026-04-08"):
    """Validate a payload against a NESTED $defs entry (e.g. a capability schema's
    role branch: $defs['dev.ucp.common.identity_linking'].business_schema).

    The CLI's --def only selects TOP-LEVEL $defs names, and selecting the capability
    container def validates vacuously (its platform_schema/business_schema keys are
    not JSON-Schema keywords) — a false-PASS trap. So we point the validator at a
    tiny wrapper schema whose $ref targets the nested def INSIDE the official pinned
    schema by its https://ucp.dev/ URL + JSON pointer; --schema-remote-base maps the
    URL back to the pinned local tree, so the OFFICIAL schema content remains the
    only validation authority (the wrapper carries no keywords of its own).

    def_path is the JSON-pointer tail under $defs, '/'-separated
    (e.g. 'dev.ucp.common.identity_linking/business_schema').
    Returns (ok, detail); raises OracleUnavailable if the binary/base is absent."""
    import tempfile, os
    base = SCHEMA_BASE.get(version)
    if not base or not (base / schema_rel).exists():
        raise OracleUnavailable(f"schema {schema_rel} for {version} not found under {base}")
    wrapper = {"$schema": "https://json-schema.org/draft/2020-12/schema",
               "$ref": f"https://ucp.dev/{schema_rel}#/$defs/{def_path}"}
    fd, wpath = tempfile.mkstemp(suffix=".json"); os.close(fd)
    fd, ppath = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        pathlib.Path(wpath).write_text(json.dumps(wrapper))
        pathlib.Path(ppath).write_text(json.dumps(payload))
        r = _run(["validate", ppath, "--schema", wpath, "--op", op,
                  "--schema-local-base", str(base),
                  "--schema-remote-base", "https://ucp.dev"], version=version)
        return (r.returncode == 0, (r.stdout + r.stderr).strip())
    finally:
        os.unlink(wpath); os.unlink(ppath)

def validate_root(payload, schema_rel, op="read", version="2026-04-08", direction="response"):
    """Validate a payload against a ROOT schema (one with no named $defs — e.g.
    types/message_error.json): pass --schema WITHOUT --def and the validator uses the
    schema as-is. This is what unblocked ERR-002/003/004 (the old note that the oracle
    'can't validate a root' was true only of --def mode).
    Returns (ok, detail); raises OracleUnavailable if the binary/base is absent."""
    import tempfile, os
    base = SCHEMA_BASE.get(version)
    schema = (base / schema_rel) if base else None
    if not base or not schema or not schema.exists():
        raise OracleUnavailable(f"schema {schema_rel} for {version} not found under {base}")
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        pathlib.Path(path).write_text(json.dumps(payload))
        args = ["validate", path, "--schema", str(schema), "--op", op,
                "--schema-local-base", str(base)]
        if direction == "request":
            args.append("--request")
        elif direction == "response":
            args.append("--response")
        r = _run(args, version=version)
        return (r.returncode == 0, (r.stdout + r.stderr).strip())
    finally:
        os.unlink(path)

def schema_parity(version="2026-04-08"):
    """Prove our oracle wiring is faithful: run the validator over our OWN controlled,
    version-matched corpus (fixtures/<version>/manifest.json). Each entry declares
    {file, op, request|response, expect: valid|invalid}. A *.valid.json that the
    validator rejects, or a *.invalid.json it accepts, means the wiring is wrong and
    callers MUST treat schema verdicts as `inconclusive`, never `pass`.
    Returns (passed: bool, report: list[str])."""
    manifest = FIXTURES / version / "manifest.json"
    if not manifest.exists():
        return False, [f"  no parity manifest at {manifest}"]
    entries = json.loads(manifest.read_text())
    report, ok = [], True
    for e in entries:
        f = FIXTURES / version / e["file"]
        got_valid, detail = validate(
            f, e["op"], request=(e.get("direction") == "request"),
            response=(e.get("direction") == "response"), version=version)
        expect_valid = (e["expect"] == "valid")
        good = (got_valid == expect_valid)
        ok &= good
        report.append(f"  {'OK ' if good else 'XX '} {e['file']:36} "
                      f"-> valid={got_valid} (expected {expect_valid})"
                      + ("" if good else f"  | {detail.splitlines()[0] if detail else ''}"))
    return ok, report

if __name__ == "__main__":
    try:
        passed, report = schema_parity()
    except OracleUnavailable as e:
        print(f"oracle unavailable: {e}", file=sys.stderr); sys.exit(2)
    print("\n".join(report))
    print(f"\nschema-oracle parity: {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if passed else 1)
