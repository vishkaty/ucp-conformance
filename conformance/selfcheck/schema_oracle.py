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
        raise OracleUnavailable(
            f"ucp-schema binary not built at {BIN}. Run: "
            f"cd {BIN.parents[2]} && cargo build --release")
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
