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
import base64, hashlib, hmac, json, time

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


def b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ── JCS (RFC 8785) + JWS Detached Content (RFC 7515 App. F) for AP2 mandates ──
# AP2 merchant_authorization signs base64url(header)+"."+base64url(JCS(payload)); the
# header is inside the signing input, so an alg swap or any payload edit breaks the
# signature. Number handling is exact for the integer minor-unit amounts UCP payloads
# use; non-integer floats are out of scope here (ap2-mandates payloads carry none).

def _jcs_str(s):
    # RFC 8785 string serialization == JSON minimal escaping, non-ASCII kept literal.
    return json.dumps(s, ensure_ascii=False, separators=(",", ":"))


def _es6_number(value):
    """ECMAScript Number::toString (RFC 8785 §3.2.2.3). Ported from the RFC 8785
    reference NumberToJson.convert2Es6Format (cyberphone/json-canonicalization,
    Apache-2.0) — validated against the official RFC 8785 number vectors."""
    fvalue = float(value)
    if fvalue == 0:
        return "0"
    s = str(fvalue)
    if "n" in s:  # inf / nan
        raise ValueError(f"JCS: non-finite number {s}")
    sign = ""
    if s[0] == "-":
        sign, s = "-", s[1:]
    exp_str, exp_val = "", 0
    q = s.find("e")
    if q > 0:
        exp_str = s[q:]
        if exp_str[2:3] == "0":  # suppress leading zero on the exponent
            exp_str = exp_str[:2] + exp_str[3:]
        s = s[:q]
        exp_val = int(exp_str[1:])
    first, dot, last = s, "", ""
    q = s.find(".")
    if q > 0:
        dot, first, last = ".", s[:q], s[q + 1:]
    if last == "0":
        dot, last = "", ""
    if 0 < exp_val < 21:
        first += last
        last = dot = exp_str = ""
        q = exp_val - len(first)
        while q >= 0:
            q -= 1
            first += "0"
    elif -7 < exp_val < 0:
        last = first + last
        first, dot, exp_str = "0", ".", ""
        q = exp_val
        while q < -1:
            q += 1
            last = "0" + last
    return sign + first + dot + last + exp_str


def jcs_canonicalize(obj):
    """RFC 8785 canonical JSON bytes: keys sorted by UTF-16 code unit, compact,
    minimal string escaping, ECMAScript number serialization."""
    def enc(o):
        if o is None:
            return "null"
        if isinstance(o, bool):
            return "true" if o else "false"
        if isinstance(o, int):
            return str(o) if -(2 ** 53) < o < 2 ** 53 else _es6_number(o)
        if isinstance(o, float):
            return _es6_number(o)
        if isinstance(o, str):
            return _jcs_str(o)
        if isinstance(o, (list, tuple)):
            return "[" + ",".join(enc(x) for x in o) + "]"
        if isinstance(o, dict):
            return "{" + ",".join(
                _jcs_str(k) + ":" + enc(v)
                for k, v in sorted(o.items(), key=lambda kv: kv[0].encode("utf-16-be"))
            ) + "}"
        raise TypeError(f"JCS: unsupported type {type(o).__name__}")
    return enc(obj).encode("utf-8")


def jws_detached_sign(header, payload, d, kid=None):
    """AP2 detached JWS: base64url(header)+'..'+base64url(sig) over the JCS payload."""
    hdr = dict(header)
    hdr.setdefault("alg", "ES256")
    if kid:
        hdr.setdefault("kid", kid)
    hb = b64url(json.dumps(hdr, separators=(",", ":"), sort_keys=True).encode())
    pb = b64url(jcs_canonicalize(payload))
    sig = ecdsa_p256_sign((hb + "." + pb).encode("ascii"), d)
    return hb + ".." + b64url(sig)


def jws_detached_verify(detached, payload, Q):
    """True iff `detached` is a valid ES256 detached JWS over JCS(payload) for key Q."""
    parts = detached.split(".")
    if len(parts) != 3 or parts[1] != "":
        return False
    hb, _, sb = parts
    try:
        hdr = json.loads(b64url_decode(hb))
    except Exception:
        return False
    if hdr.get("alg") != "ES256":
        return False
    try:
        sig = b64url_decode(sb)
    except Exception:
        return False
    signing_input = (hb + "." + b64url(jcs_canonicalize(payload))).encode("ascii")
    return ecdsa_p256_verify(signing_input, sig, Q)


def jws_compact_sign(header, payload_bytes, d, kid=None):
    """Compact JWS (ES256) over raw payload bytes: b64(header).b64(payload).b64(sig).

    Used for the AP2 checkout_jwt (the merchant-signed JWT a checkout mandate wraps).
    """
    hdr = dict(header)
    hdr.setdefault("alg", "ES256")
    if kid:
        hdr.setdefault("kid", kid)
    hb = b64url(json.dumps(hdr, separators=(",", ":"), sort_keys=True).encode())
    pb = b64url(payload_bytes)
    sig = ecdsa_p256_sign((hb + "." + pb).encode("ascii"), d)
    return hb + "." + pb + "." + b64url(sig)


def jws_compact_verify(token, Q):
    """Verify a compact ES256 JWS for key Q; returns the payload bytes, or None."""
    try:
        hb, pb, sb = token.split(".")
        hdr = json.loads(b64url_decode(hb))
        if hdr.get("alg") != "ES256":
            return None
        if ecdsa_p256_verify((hb + "." + pb).encode("ascii"), b64url_decode(sb), Q):
            return b64url_decode(pb)
    except Exception:
        return None
    return None


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


def sign_request_headers(method, authority, path, body_bytes, d, kid,
                         ucp_agent=None, idem=None, omit=(),
                         content_type="application/json"):
    """RFC 9421 REQUEST-signing headers (signatures.md L193-204). Covered components:
    @method/@authority/@path always (SIG-014); content-digest+content-type when there is a
    body (SIG-015); idempotency-key on state-changing requests (SIG-016); `ucp-agent` when
    the UCP-Agent header is present (SIG-018). The Content-Digest / Idempotency-Key / UCP-Agent
    HEADERS are still emitted for integrity even when `omit` drops them from the COVERED set
    (that omission is how a defective signer is modelled). Built from the same interop-anchored
    ES256 primitives as sign_response_headers; mirrors the merchant webhook signer's scheme."""
    omit = set(omit)
    comps = [c for c in ("@method", "@authority", "@path") if c not in omit]
    hdrs = {}
    out = {}
    if body_bytes is not None:
        digest = content_digest(body_bytes)
        out["Content-Digest"] = digest
        hdrs["content-digest"] = digest
        # Sign the ACTUAL Content-Type the request carries (RFC 9421 signs the real
        # header value). Defaults to application/json, so every existing caller is
        # byte-identical; a non-JSON content_type (OVR-008 defect) still signs
        # consistently, keeping the signature valid and the violation isolated.
        hdrs["content-type"] = content_type
        comps += [c for c in ("content-digest", "content-type") if c not in omit]
    if ucp_agent:                               # SIG-018: bind the platform identity
        hdrs["ucp-agent"] = ucp_agent
        if "ucp-agent" not in omit:
            comps.append("ucp-agent")
    if idem:                                    # SIG-016: bind idempotency to the request
        hdrs["idempotency-key"] = idem
        if "idempotency-key" not in omit:
            comps.append("idempotency-key")
    raw_params = '(' + " ".join(f'"{c}"' for c in comps) + ')' + f';keyid="{kid}"'
    derived = {"@method": method.upper(), "@authority": authority, "@path": path}
    base = _sig_base(comps, raw_params, derived, hdrs)
    sig = ecdsa_p256_sign(base, d)
    out["Signature-Input"] = f"sig1={raw_params}"
    out["Signature"] = "sig1=:" + base64.b64encode(sig).decode() + ":"
    return out


def verify_request(method, authority, path, body_bytes, headers, jwks):
    """Verify a platform's RFC 9421 REQUEST signature (the business/receiver's obligation).
    -> (ok, reason). The sandbox uses this to actually check what the reference agent signs,
    so the agent's request signature is real (round-trip anchored), not merely well-formed."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    si, sg = h.get("signature-input"), h.get("signature")
    if not (si and sg):
        return False, "signature_missing"
    try:
        _, _, params = si.partition("=")
        inner = params[params.index("(") + 1:params.index(")")]
        comps = [c.strip().strip('"') for c in inner.split()]
        raw_params = params[params.index("("):]
        kid = params.split('keyid="')[1].split('"')[0]
    except Exception:
        return False, "malformed_signature_input"
    if body_bytes is not None and h.get("content-digest") != content_digest(body_bytes):
        return False, "digest_mismatch"
    jwk = next((j for j in (jwks or []) if j.get("kid") == kid), None)
    if not jwk:
        return False, "key_not_found"
    derived = {"@method": method.upper(), "@authority": authority, "@path": path}
    base = _sig_base(comps, raw_params, derived, h)
    if base is None:
        return False, "unresolved_component"
    try:
        sigb = base64.b64decode(sg.split(":", 1)[1].rsplit(":", 1)[0])
    except Exception:
        return False, "malformed_signature"
    ok = ecdsa_p256_verify(base, sigb, pub_from_jwk(jwk))
    return (ok, "ok" if ok else "signature_invalid")


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
