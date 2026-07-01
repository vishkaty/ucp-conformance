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
BIN = VENDOR / "ucp-schema" / "target" / "release" / "ucp-schema"
# spec version -> local "site root" dir that maps https://ucp.dev/ (so the
# validator resolves a capability schema URL https://ucp.dev/schemas/<x> to
# <base>/schemas/<x>). Each dir therefore CONTAINS a schemas/ subdir.
SCHEMA_BASE = {
    "2026-04-08": VENDOR / "ucp" / "source",
    "2026-01-23": VENDOR / "ucp-schemas" / "2026-01-23",   # git-extracted source/ tree
    "2026-01-11": VENDOR / "ucp-schemas" / "2026-01-11",
}
FIXTURES = ROOT / "conformance" / "selfcheck" / "fixtures"

class OracleUnavailable(RuntimeError):
    pass

def _run(args):
    if not BIN.exists():
        # parents[2] may not exist for an unusual BIN path; don't let the message crash.
        hint = str(BIN.parents[2]) if len(BIN.parents) > 2 else str(BIN.parent)
        raise OracleUnavailable(
            f"ucp-schema binary not built at {BIN}. Run: cd {hint} && cargo build --release")
    return subprocess.run([str(BIN), *args], capture_output=True, text=True)

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
    r = _run(args)
    return (r.returncode == 0, (r.stdout + r.stderr).strip())

def _ucp_schema_path(base):
    """Locate ucp.json (the profile schema) under a schema base dir."""
    for cand in (base / "schemas" / "ucp.json", base / "source" / "schemas" / "ucp.json"):
        if cand.exists():
            return cand
    return None

def validate_profile(profile, version="2026-01-23", role="business"):
    """Validate a discovered /.well-known/ucp document against the official profile
    schema (ucp.json, $def {role}_schema) using the ucp-schema validator.
      role="business" for a merchant profile, "platform" for an agent profile.
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
        r = _run(["validate", path, "--schema", str(schema), "--def", f"{role}_schema",
                  "--op", "read", "--schema-local-base", str(base)])
        return (r.returncode == 0, (r.stdout + r.stderr).strip())
    finally:
        os.unlink(path)

def validate_against(payload, schema_rel, def_name, op="read", version="2026-04-08"):
    """Validate a payload object against an explicit schema file + $def under a version's
    schema base. schema_rel is relative to <base> (e.g. 'schemas/shopping/catalog_search.json').
    Returns (ok, detail); raises OracleUnavailable if the binary/base is absent."""
    import tempfile, os
    base = SCHEMA_BASE.get(version)
    schema = (base / schema_rel) if base else None
    if not base or not schema or not schema.exists():
        raise OracleUnavailable(f"schema {schema_rel} for {version} not found under {base}")
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        pathlib.Path(path).write_text(json.dumps(payload))
        r = _run(["validate", path, "--schema", str(schema), "--def", def_name,
                  "--op", op, "--schema-local-base", str(base)])
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
