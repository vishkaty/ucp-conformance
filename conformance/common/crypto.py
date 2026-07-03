#!/usr/bin/env python3
"""
common/crypto.py — the shared RFC 9421 / ES256 crypto both conformance lanes use.

Stdlib-only P-256 ECDSA (RFC 6979 deterministic) + RFC 9421 response signing/verification
+ RFC 9530 Content-Digest + RFC 7517 JWK. The primitives are the exact,
openssl-cross-anchored implementation the merchant fixture already ships (and the
`sig-check` gate validates) — reproduced here as a standalone module so the AGENT lane
can sign (its sandbox) and verify (its reference agent) without importing merchant
internals. A `crypto-interop` gate proves this module and the fixture agree bit-for-bit,
so the two can never diverge. (Full dedup — fixture importing this — is a safe follow-up.)

TEST KEYS ONLY.
"""
import base64, hashlib, hmac, time

# NIST P-256
_EC_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_EC_A = _EC_P - 3
_EC_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
_EC_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_EC_G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
         0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _ec_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _EC_P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + _EC_A) * pow(2 * y1, -1, _EC_P) % _EC_P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, _EC_P) % _EC_P
    x3 = (lam * lam - x1 - x2) % _EC_P
    return (x3, (lam * (x1 - x3) - y1) % _EC_P)


def _ec_mul(k, pt):
    acc = None
    while k:
        if k & 1:
            acc = _ec_add(acc, pt)
        pt = _ec_add(pt, pt)
        k >>= 1
    return acc


def ec_on_curve(pt):
    if pt is None:
        return False
    x, y = pt
    return (y * y - (x * x * x + _EC_A * x + _EC_B)) % _EC_P == 0


def _rfc6979_k(d, h1):
    x = d.to_bytes(32, "big")
    V, K = b"\x01" * 32, b"\x00" * 32
    K = hmac.new(K, V + b"\x00" + x + h1, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    K = hmac.new(K, V + b"\x01" + x + h1, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    while True:
        V = hmac.new(K, V, hashlib.sha256).digest()
        k = int.from_bytes(V, "big")
        if 1 <= k < _EC_N:
            return k
        K = hmac.new(K, V + b"\x00", hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()


def ecdsa_p256_sign(msg, d):
    """ES256 over msg -> 64-byte raw r||s (RFC 9421 encoding)."""
    h1 = hashlib.sha256(msg).digest()
    z = int.from_bytes(h1, "big")
    while True:
        k = _rfc6979_k(d, h1)
        x1, _ = _ec_mul(k, _EC_G)
        r = x1 % _EC_N
        if r:
            s = pow(k, -1, _EC_N) * (z + r * d) % _EC_N
            if s:
                return r.to_bytes(32, "big") + s.to_bytes(32, "big")
        h1 = hashlib.sha256(h1).digest()


def ecdsa_p256_verify(msg, sig, Q):
    if not isinstance(sig, (bytes, bytearray)) or len(sig) != 64 or not ec_on_curve(Q):
        return False
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    if not (1 <= r < _EC_N and 1 <= s < _EC_N):
        return False
    z = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, -1, _EC_N)
    pt = _ec_add(_ec_mul(z * w % _EC_N, _EC_G), _ec_mul(r * w % _EC_N, Q))
    return pt is not None and pt[0] % _EC_N == r


def keypair(seed):
    d = (int.from_bytes(hashlib.sha256(seed).digest(), "big") % (_EC_N - 1)) + 1
    return d, _ec_mul(d, _EC_G)


def content_digest(body_bytes):
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body_bytes).digest()).decode() + ":"


def jwk_from_pub(kid, Q):
    return {"kid": kid, "kty": "EC", "crv": "P-256",
            "x": b64url(Q[0].to_bytes(32, "big")), "y": b64url(Q[1].to_bytes(32, "big")),
            "use": "sig", "alg": "ES256"}


def pub_from_jwk(jwk):
    def d(s):
        return int.from_bytes(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)), "big")
    return (d(jwk["x"]), d(jwk["y"]))


def _sig_base(components, raw_params, derived, headers_l):
    lines = []
    for c in components:
        if c.startswith("@"):
            if c not in derived:
                return None
            v = derived[c]
        else:
            if c not in headers_l:
                return None
            v = headers_l[c].strip()
        lines.append(f'"{c}": {v}')
    lines.append(f'"@signature-params": {raw_params}')
    return "\n".join(lines).encode()


_COMPS = ["@status", "content-digest", "content-type"]


def sign_response_headers(status, body_bytes, d, kid, created=None):
    """RFC 9421 response-signing headers (@status + content-digest + content-type)."""
    digest = content_digest(body_bytes)
    created = int(time.time()) if created is None else created
    raw_params = ('(' + " ".join(f'"{c}"' for c in _COMPS) + ')'
                  + f';created={created};keyid="{kid}"')
    base = _sig_base(_COMPS, raw_params, {"@status": str(status)},
                     {"content-digest": digest, "content-type": "application/json"})
    sig = ecdsa_p256_sign(base, d)
    return {"Content-Digest": digest, "Signature-Input": f"sig1={raw_params}",
            "Signature": "sig1=:" + base64.b64encode(sig).decode() + ":"}


def verify_response(status, body_bytes, headers, jwks):
    """Verify a business RFC 9421 response signature. -> (ok: bool, reason: str).
    This is the platform/agent's obligation: fetch the business signing key from its
    profile, verify Content-Digest + the ES256 signature over the reconstructed base."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    si, sg, cd = h.get("signature-input"), h.get("signature"), h.get("content-digest")
    if not (si and sg):
        return False, "signature_missing"
    if cd != content_digest(body_bytes):
        return False, "digest_mismatch"
    try:
        _, _, params = si.partition("=")
        inner = params[params.index("(") + 1:params.index(")")]
        comps = [c.strip().strip('"') for c in inner.split()]
        raw_params = params[params.index("("):]
        kid = params.split('keyid="')[1].split('"')[0]
    except Exception:
        return False, "malformed_signature_input"
    jwk = next((j for j in (jwks or []) if j.get("kid") == kid), None)
    if not jwk:
        return False, "key_not_found"
    base = _sig_base(comps, raw_params, {"@status": str(status)},
                     {"content-digest": cd, "content-type": h.get("content-type",
                                                                  "application/json")})
    if base is None:
        return False, "unresolved_component"
    try:
        b64 = sg.split(":", 1)[1].rsplit(":", 1)[0]
        sigb = base64.b64decode(b64)
    except Exception:
        return False, "malformed_signature"
    ok = ecdsa_p256_verify(base, sigb, pub_from_jwk(jwk))
    return (ok, "ok" if ok else "signature_invalid")
