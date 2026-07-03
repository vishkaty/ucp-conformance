#!/usr/bin/env python3
"""
crypto_interop.py — prove common/crypto agrees bit-for-bit with the merchant fixture's
(openssl-cross-anchored) crypto, so the shared agent-side crypto can NEVER diverge from
the validated implementation.

Checks, over several deterministic messages:
  - keypair(seed) identical
  - content_digest identical
  - ES256 cross-verify BOTH ways (common signs -> fixture verifies; fixture signs ->
    common verifies)
  - common.verify_response ACCEPTS a real fixture-signed response (fixture.sign_response +
    fixture.signing_jwk) and REJECTS a tampered one

If any drift, the build fails — this is the gate that lets us reuse the crypto safely
without merging the two modules yet.
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "conformance"))
sys.path.insert(0, os.path.join(ROOT, "conformance", "fixtures", "merchant"))
from common import crypto as C   # noqa: E402
import server as F               # the merchant fixture (import-safe; no server start)  # noqa: E402


def run():
    fails = []
    msgs = [b"", b"hello", b'{"ucp":{"version":"2026-04-08"}}', b"\x00\x01\x02" * 40]
    seed = b"interop-seed-2026"

    if C.keypair(seed) != F._keypair(seed):
        fails.append("keypair(seed) differs between common and fixture")
    d, Q = C.keypair(seed)

    for m in msgs:
        if C.content_digest(m) != F.content_digest(m):
            fails.append(f"content_digest differs for {m!r}")
        # common signs -> fixture verifies
        if not F.ecdsa_p256_verify(m, C.ecdsa_p256_sign(m, d), Q):
            fails.append(f"fixture rejects common's signature for {m!r}")
        # fixture signs -> common verifies
        if not C.ecdsa_p256_verify(m, F.ecdsa_p256_sign(m, d), Q):
            fails.append(f"common rejects fixture's signature for {m!r}")

    # end-to-end: common.verify_response must ACCEPT a genuine fixture-signed response...
    body = b'{"ucp":{"version":"2026-04-08"},"id":"chk_x","status":"incomplete"}'
    hdrs = F.sign_response(200, body)
    jwks = [F.signing_jwk()]
    ok, reason = C.verify_response(200, body, hdrs, jwks)
    if not ok:
        fails.append(f"common.verify_response rejected a valid fixture-signed response: {reason}")
    # ...and REJECT a tampered body
    ok2, _ = C.verify_response(200, body + b" ", hdrs, jwks)
    if ok2:
        fails.append("common.verify_response accepted a tampered body (digest mismatch missed)")

    return fails


def main():
    fails = run()
    if fails:
        print("crypto-interop gate: FAIL — shared crypto drifted from the fixture:")
        for f in fails:
            print(f"  x {f}")
        return 1
    print("crypto-interop gate: PASS — common/crypto agrees bit-for-bit with the fixture's "
          "openssl-anchored ES256/RFC-9421 (verify accepts real signatures, rejects tampering).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
