#!/usr/bin/env python3
"""
validate_ap2_crypto.py — proves the AP2 mandate crypto (JCS + JWS-detached) is
correct against INDEPENDENT oracles, not just self-consistent.

AP2 merchant_authorization is a JWS Detached Content signature (RFC 7515 App. F)
whose signing input is base64url(header)+"."+base64url(JCS(payload)) — so the
signature covers the header and the JCS-canonicalized payload (ap2-mandates.md).
A conformance tool that mis-canonicalizes would raise false signature failures, so
this gate anchors both layers to their standards:

  1. JCS vs the OFFICIAL RFC 8785 test vectors (cyberphone reference impl) — the
     exact input→output pairs the RFC's reference is tested with.
  2. JWS detached round-trip + every tampering rejected (payload edit, wrong key,
     alg substitution).
  3. The ES256 primitive underneath is already openssl-anchored by crypto-interop.

Run:  python3 conformance/selfcheck/validate_ap2_crypto.py
Exit 0 = sound; 1 = a property failed (blocks any AP2 check built on it).
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))
import crypto  # noqa: E402

VEC = HERE / "fixtures" / "rfc8785"


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    return bool(cond)


def main():
    ok = True

    # 1. JCS vs official RFC 8785 vectors (independent correctness).
    for inp in sorted((VEC / "input").glob("*.json")):
        exp = (VEC / "output" / inp.name).read_bytes().rstrip(b"\n")
        got = crypto.jcs_canonicalize(json.loads(inp.read_text()))
        ok &= check(f"RFC 8785 vector {inp.stem}", got == exp)

    # 2. JWS detached: round-trip + tamper resistance.
    d, Q = crypto.keypair(b"ap2-merchant-test")
    payload = {"id": "chk_1", "status": "ready_for_complete", "total": 3500}
    detached = crypto.jws_detached_sign({"alg": "ES256"}, payload, d, kid="k1")
    ok &= check("detached JWS shape header..sig",
                detached.count(".") == 2 and ".." in detached)
    ok &= check("verify accepts a correct signature",
                crypto.jws_detached_verify(detached, payload, Q) is True)
    ok &= check("verify REJECTS an edited payload",
                crypto.jws_detached_verify(detached, {**payload, "total": 1}, Q) is False)
    ok &= check("verify accepts a JCS-equal key reordering",
                crypto.jws_detached_verify(
                    detached, {"total": 3500, "status": "ready_for_complete", "id": "chk_1"}, Q) is True)
    ok &= check("verify REJECTS a wrong key",
                crypto.jws_detached_verify(detached, payload, crypto.keypair(b"other")[1]) is False)
    hb, _, sb = detached.split(".")
    bad = crypto.b64url(json.dumps({"alg": "HS256", "kid": "k1"}, separators=(",", ":")).encode())
    ok &= check("verify REJECTS an alg-swapped header (alg-substitution defense)",
                crypto.jws_detached_verify(f"{bad}..{sb}", payload, Q) is False)

    print("\nap2-crypto: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
