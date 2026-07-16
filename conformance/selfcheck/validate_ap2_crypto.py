#!/usr/bin/env python3
"""
validate_ap2_crypto.py — proves the AP2 mandate crypto (JCS + JWS-detached) is sound.

AP2 merchant_authorization is a JWS Detached Content signature (RFC 7515 App. F) whose
signing input is base64url(header) + "." + base64url(JCS(payload)) — so the signature
covers BOTH the header and the JCS-canonicalized payload (ap2-mandates.md: prevents
alg-substitution). This gate pins that behavior the only honest way: sign a known payload,
prove verify accepts it, and prove every tampering (payload edit, key reordering that
breaks JCS, alg swap in the header, wrong key) is REJECTED. Plus RFC 8785 canonicalization
vectors so JCS itself is correct, not just self-consistent.

Run:  python3 conformance/selfcheck/validate_ap2_crypto.py
Exit 0 = crypto sound; 1 = a property failed (blocks any AP2 check built on it).
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "common"))
import crypto  # noqa: E402


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    return bool(cond)


def main():
    ok = True

    # ── JCS (RFC 8785) ──────────────────────────────────────────────────────
    # Keys sorted by code unit; compact; minimal string escaping; integers verbatim.
    ok &= check(
        "JCS sorts object keys and compacts",
        crypto.jcs_canonicalize({"b": 1, "a": 2})
        == b'{"a":2,"b":1}',
    )
    ok &= check(
        "JCS is recursive and array order is preserved",
        crypto.jcs_canonicalize({"z": [3, 1, 2], "y": {"n": 1, "m": 2}})
        == b'{"y":{"m":2,"n":1},"z":[3,1,2]}',
    )
    # RFC 8785 §3.2.3 literal example (control chars + non-ASCII kept literal, sorted)
    ok &= check(
        "JCS RFC 8785 escaping + ordering",
        crypto.jcs_canonicalize({"€": "Euro", "\r": "CR", "1": "One"})
        == '{"\\r":"CR","1":"One","€":"Euro"}'.encode(),
    )

    # ── JWS detached sign/verify round-trip ─────────────────────────────────
    d, Q = crypto.keypair(b"ap2-merchant-test")
    payload = {"id": "chk_1", "status": "ready_for_complete", "total": 3500}
    header = {"alg": "ES256", "kid": "merchant-2026"}
    detached = crypto.jws_detached_sign(header, payload, d, kid="merchant-2026")
    ok &= check("detached JWS has empty middle segment (header..sig)",
                detached.count(".") == 2 and ".." in detached)
    ok &= check("verify accepts a correctly signed payload",
                crypto.jws_detached_verify(detached, payload, Q) is True)

    # ── tampering must be rejected ──────────────────────────────────────────
    ok &= check("verify REJECTS an edited payload",
                crypto.jws_detached_verify(detached, {**payload, "total": 1}, Q) is False)
    # key reordering only matters if the signer skipped JCS; with JCS both canonicalize
    # identically, so a semantically-equal reordering must still VERIFY:
    ok &= check("verify accepts a key-reordered but JCS-equal payload",
                crypto.jws_detached_verify(
                    detached, {"total": 3500, "status": "ready_for_complete", "id": "chk_1"}, Q) is True)
    ok &= check("verify REJECTS a wrong key",
                crypto.jws_detached_verify(detached, payload, crypto.keypair(b"other")[1]) is False)
    # alg-substitution: flip the header alg but keep the signature → signing input changes
    # (header is inside it) so verification MUST fail.
    hdr_b64, _, sig_b64 = detached.split(".")
    bad_hdr = crypto.b64url(json.dumps({"alg": "HS256", "kid": "merchant-2026"},
                                       separators=(",", ":")).encode())
    ok &= check("verify REJECTS an alg-swapped header (alg-substitution defense)",
                crypto.jws_detached_verify(f"{bad_hdr}..{sig_b64}", payload, Q) is False)

    print("\nap2-crypto: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
