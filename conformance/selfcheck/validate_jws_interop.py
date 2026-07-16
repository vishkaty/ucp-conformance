#!/usr/bin/env python3
"""
validate_jws_interop.py — cross-validate our AP2 crypto against a THIRD-PARTY
implementation (the `cryptography` library), so correctness is anchored to an
independent oracle on every run, not just to our own round-trips.

Proves both directions of interop for the detached-JWS / ES256 surface AP2
mandates use:
  * a signature WE produce verifies under the standard library, and
  * a signature the standard library produces verifies under OUR verifier.
Byte-for-byte equality is NOT required (ECDSA k differs); cross-verification is
the correct interop property. Combined with crypto-interop (openssl-anchored
ES256) and the official RFC 8785 vectors in ap2-crypto, the JCS+JWS layers are
independently pinned.

Exit 0 = interop holds; 1 = a mismatch (real divergence); 2 = `cryptography`
not installed (skip — CI installs it; local runs without it are not failures).
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))
import crypto  # noqa: E402

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature, encode_dss_signature)
except ImportError:
    print("jws-interop: SKIP (cryptography not installed)")
    sys.exit(2)


def main():
    ok = True
    d, Q = crypto.keypair(b"jws-interop-test")
    header = {"alg": "ES256", "kid": "k1"}
    payload = {"id": "chk_1", "currency": "USD", "total": 3500, "items": [1, 2, 3]}

    pub = ec.EllipticCurvePublicNumbers(Q[0], Q[1], ec.SECP256R1()).public_key()
    priv = ec.derive_private_key(d, ec.SECP256R1())

    # signing input as AP2/JWS define it (over JCS payload)
    det = crypto.jws_detached_sign(header, payload, d, kid="k1")
    hb, _, sb = det.split(".")
    signing_input = (hb + "." + crypto.b64url(crypto.jcs_canonicalize(payload))).encode()

    # 1. our signature -> standard verifier
    sig = crypto.b64url_decode(sb)
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    try:
        pub.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
        print("  ✓ our detached-JWS signature verifies under `cryptography`")
    except Exception as e:
        ok = False
        print(f"  ✗ `cryptography` rejected our signature: {e}")

    # 2. standard signature -> our verifier
    der = priv.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    rr, ss = decode_dss_signature(der)
    their = hb + ".." + crypto.b64url(rr.to_bytes(32, "big") + ss.to_bytes(32, "big"))
    if crypto.jws_detached_verify(their, payload, Q):
        print("  ✓ a `cryptography`-produced signature verifies under our verifier")
    else:
        ok = False
        print("  ✗ our verifier rejected a valid `cryptography` signature")

    # 3. JCS bytes are what the standard lib signs over (sanity: same signing input)
    if crypto.jcs_canonicalize(payload) == crypto.jcs_canonicalize(
            json.loads(json.dumps(payload))):
        print("  ✓ JCS is stable across a re-serialization round-trip")
    else:
        ok = False
        print("  ✗ JCS not stable across re-serialization")

    print("\njws-interop: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
