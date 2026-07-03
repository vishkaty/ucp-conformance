#!/usr/bin/env python3
"""
merchant_checks_04_08_signatures.py — 2026-04-08-scoped RFC 9421 signature checks
(signatures area; docs/specification/signatures.md is NEW at 2026-04-08 — no SIG ids
exist in the 2026-01-11/01-23 registers, so every check is version-locked).

Two merchant-observable surfaces:

  * RESPONSE signing (SIG-001/006/008/010/012/013/019/020): signatures.md makes
    response signatures RECOMMENDED/OPTIONAL, so these checks are config-gated on
    `signature.responses` (the operator asserts the merchant signs its responses;
    the controlled fixture does). Given a signed response, the format rules ARE
    MUSTs: RFC 9421 header shapes, @status component, RFC 9530 sha-256 body
    digest, no `alg` Signature-Input parameter, raw r||s encoding, and the keyid
    resolving to a published signing_keys JWK. The verifying receiver for a
    merchant RESPONSE is this suite itself, so no external harness is needed —
    including a full cryptographic ES256 verification against the JWK the
    merchant publishes at /.well-known/ucp.

  * REQUEST verification (SIG-002): "All implementations MUST support verifying
    P-256 (ES256) signatures." Config-gated on `signature.request_private_jwk`
    (a P-256 private JWK whose PUBLIC part the merchant under test trusts — the
    controlled fixture bakes in the matching test public key). The check signs a
    create request per the spec's signed-component table, first proving the
    merchant actually verifies (a tampered signature must be rejected 4xx), then
    that a valid ES256 signature is accepted.

The P-256 ECDSA implementation is pure stdlib (the pip bundle must stay
dependency-free); it is cross-anchored against openssl — both directions — in
conformance/fixtures/merchant/selfcheck.py, so sign/verify correctness does not
rest on this file alone.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr/_create_payload from there).
"""
import sys, pathlib, json, uuid, base64, hashlib, hmac
from urllib.parse import urlsplit
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, fetch, CLEAN, DEVIATION, INCONCLUSIVE       # noqa: E402
from merchant_checks import MCheck, _hdr, _create_payload, STATUS_ENUM  # noqa: E402

V0408 = ("2026-04-08",)

# ---- P-256 ECDSA (pure stdlib; openssl-anchored in fixtures/merchant/selfcheck.py) --
_EC_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_EC_A = _EC_P - 3
_EC_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
_EC_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_EC_G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
         0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)

def _ec_add(p1, p2):
    if p1 is None: return p2
    if p2 is None: return p1
    x1, y1 = p1; x2, y2 = p2
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

def _ec_on_curve(pt):
    if pt is None: return False
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
    """ECDSA P-256/SHA-256 -> 64-byte fixed-width raw r||s (RFC 9421, not DER)."""
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
    if not isinstance(sig, (bytes, bytearray)) or len(sig) != 64 or not _ec_on_curve(Q):
        return False
    r = int.from_bytes(sig[:32], "big"); s = int.from_bytes(sig[32:], "big")
    if not (1 <= r < _EC_N and 1 <= s < _EC_N):
        return False
    z = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, -1, _EC_N)
    pt = _ec_add(_ec_mul(z * w % _EC_N, _EC_G), _ec_mul(r * w % _EC_N, Q))
    return pt is not None and pt[0] % _EC_N == r

# ---- RFC 8941/9421/9530 header helpers ---------------------------------------
def _b64u_dec(s):
    """Strict base64url decode (raises on non-alphabet input — a permissive decode
    would silently accept corrupt JWK coordinates)."""
    if not isinstance(s, str) or not s:
        raise ValueError("empty")
    return base64.b64decode(s.replace("-", "+").replace("_", "/")
                            + "=" * (-len(s) % 4), validate=True)

def _sf_split(s, seps):
    """Split a structured-field string on top-level separators, respecting quoted
    strings and inner lists (enough RFC 8941 for the signature headers)."""
    out, cur, depth, quote, i = [], [], 0, False, 0
    while i < len(s):
        c = s[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1]); i += 1
            elif c == '"':
                quote = False
        elif c == '"':
            quote = True; cur.append(c)
        elif c == "(":
            depth += 1; cur.append(c)
        elif c == ")":
            depth -= 1; cur.append(c)
        elif c in seps and depth == 0:
            out.append("".join(cur).strip()); cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out

def parse_signature_input(value):
    """Signature-Input -> {label: {raw, components, params}} (raw = the member value
    verbatim, which "@signature-params" must echo). None on malformed input."""
    if not isinstance(value, str) or not value.strip():
        return None
    out = {}
    for member in _sf_split(value, ","):
        label, eq, val = member.partition("=")
        label, val = label.strip(), val.strip()
        if not eq or not label or not val.startswith("("):
            return None
        depth, j = 0, 0
        for j, c in enumerate(val):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
        inner, rest = val[1:j], val[j + 1:]
        comps = []
        for tok in _sf_split(inner, " "):
            if not (tok.startswith('"') and tok.endswith('"')) or ";" in tok:
                return None                 # component parameters: unsupported here
            comps.append(tok[1:-1])
        params = {}
        for p in _sf_split(rest, ";"):
            if not p:
                continue
            k, _, v = p.partition("=")
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            params[k.strip()] = v
        out[label] = {"raw": val, "components": comps, "params": params}
    return out or None

def parse_signature(value):
    """Signature -> {label: raw signature bytes}; None on malformed input."""
    if not isinstance(value, str) or not value.strip():
        return None
    out = {}
    for member in _sf_split(value, ","):
        label, eq, val = member.partition("=")
        label, val = label.strip(), val.strip()
        if not eq or not val.startswith(":") or not val.endswith(":"):
            return None
        try:
            out[label] = base64.b64decode(val[1:-1], validate=True)
        except Exception:
            return None
    return out or None

def _lh(r):
    return {k.lower(): v for k, v in (r.headers or {}).items()}

def _matched(r):
    """The response's (signature-input entry, signature bytes, lowercased headers)
    for the first label present in BOTH headers; None when absent/malformed."""
    h = _lh(r)
    si = parse_signature_input(h.get("signature-input", ""))
    sigs = parse_signature(h.get("signature", ""))
    if not si or not sigs:
        return None
    label = next((l for l in si if l in sigs), None)
    if label is None:
        return None
    return si[label], sigs[label], h

def _digest_value(header_value):
    """The sha-256 byte value from an RFC 9530 Content-Digest dictionary, or None
    (missing sha-256 member / malformed)."""
    for member in _sf_split(header_value or "", ","):
        k, _, v = member.partition("=")
        if k.strip().lower() == "sha-256" and v.startswith(":") and v.endswith(":"):
            try:
                return base64.b64decode(v[1:-1], validate=True)
            except Exception:
                return None
    return None

def _profile_keys(ctx):
    """signing_keys[] from the discovered profile document. Per the official
    discovery/profile_schema.json it lives at the top level of the served document
    (sibling of `ucp` in the enveloped shape; top level of the flat shape)."""
    keys = (ctx.profile or {}).get("signing_keys")
    return keys if isinstance(keys, list) else []

def _jwk_point(jwk):
    """(x, y) ints from an EC JWK; None when not a decodable P-256 point."""
    try:
        x, y = _b64u_dec(jwk.get("x")), _b64u_dec(jwk.get("y"))
    except Exception:
        return None
    if len(x) != 32 or len(y) != 32:
        return None
    return (int.from_bytes(x, "big"), int.from_bytes(y, "big"))

# ---- fetches ------------------------------------------------------------------
def signed_create_resp(ctx):
    """A checkout create — on a merchant that signs responses, the response carries
    the RFC 9421 headers the predicates examine."""
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _create_payload(ctx), _hdr())

def profile_doc_resp(ctx):
    """The WHOLE discovered profile document (not just the ucp envelope) — that is
    where signing_keys lives."""
    return Resp(200, {"Content-Type": "application/json"},
                json.dumps(ctx.profile).encode())

# ---- predicates -----------------------------------------------------------------
def p_sig_headers_present(r):
    """SIG-013: a signed response carries BOTH Signature-Input and Signature, with a
    common label whose value parses (RFC 8941 inner-list / byte-sequence shapes)."""
    if r.status not in (200, 201):
        return DEVIATION
    return CLEAN if _matched(r) else DEVIATION

def p_content_digest(r):
    """SIG-012 (Content-Digest required when a body is present) + SIG-010 (RFC 9530,
    sha-256 over the raw body bytes)."""
    if r.status not in (200, 201) or not r.body:
        return DEVIATION
    got = _digest_value(_lh(r).get("content-digest"))
    if got is None:
        return DEVIATION                    # header absent, malformed, or not sha-256
    return CLEAN if got == hashlib.sha256(r.body).digest() else DEVIATION

def p_status_component(r):
    """SIG-020: response signatures cover @status (and not the request-only @method)."""
    if r.status not in (200, 201):
        return DEVIATION
    m = _matched(r)
    if not m:
        return DEVIATION
    comps = m[0]["components"]
    return CLEAN if "@status" in comps and "@method" not in comps else DEVIATION

def p_no_alg_param(r):
    """SIG-006: `alg` MUST NOT appear in the Signature-Input parameters (the
    algorithm is derived from the JWK's crv)."""
    if r.status not in (200, 201):
        return DEVIATION
    m = _matched(r)
    if not m:
        return DEVIATION
    return DEVIATION if "alg" in m[0]["params"] else CLEAN

def p_signing_keys_jwk(r):
    """SIG-007: every published signing key is a well-formed RFC 7517 JWK per the
    spec's EC key table — kid + kty required; EC keys additionally carry crv and
    base64url x/y of the curve's coordinate size."""
    keys = (r.json or {}).get("signing_keys") if isinstance(r.json, dict) else None
    if not isinstance(keys, list) or not keys:
        return DEVIATION
    want_len = {"P-256": 32, "P-384": 48}
    for k in keys:
        if not isinstance(k, dict):
            return DEVIATION
        if not (isinstance(k.get("kid"), str) and k["kid"]
                and isinstance(k.get("kty"), str) and k["kty"]):
            return DEVIATION
        if k["kty"] == "EC":
            if not (isinstance(k.get("crv"), str) and k["crv"]):
                return DEVIATION
            try:
                x, y = _b64u_dec(k.get("x")), _b64u_dec(k.get("y"))
            except Exception:
                return DEVIATION
            n = want_len.get(k["crv"])
            if n and (len(x) != n or len(y) != n):
                return DEVIATION
    return CLEAN

def p_keyid_published(r, ctx):
    """SIG-008: the keyid a signed response references resolves to a key published
    in the profile's signing_keys[] at /.well-known/ucp."""
    if r.status not in (200, 201):
        return DEVIATION
    m = _matched(r)
    if not m:
        return DEVIATION
    kid = m[0]["params"].get("keyid")
    if not kid:
        return DEVIATION
    kids = {k.get("kid") for k in _profile_keys(ctx) if isinstance(k, dict)}
    return CLEAN if kid in kids else DEVIATION

def p_raw_rs_encoding(r, ctx):
    """SIG-019: the signature value is fixed-width raw r||s (64 bytes for P-256,
    96 for P-384), never ASN.1/DER."""
    if r.status not in (200, 201):
        return DEVIATION
    m = _matched(r)
    if not m:
        return DEVIATION
    entry, sig, _ = m
    jwk = next((k for k in _profile_keys(ctx) if isinstance(k, dict)
                and k.get("kid") == entry["params"].get("keyid")), None)
    want = {"P-256": (64,), "P-384": (96,)}.get((jwk or {}).get("crv"), (64, 96))
    return CLEAN if len(sig) in want else DEVIATION

def p_response_verifies(r, ctx):
    """SIG-001: the response signature is a REAL RFC 9421 signature — the body
    digest matches, the signature base reconstructs from the declared components,
    and ES256 verification succeeds against the JWK published in signing_keys."""
    if r.status not in (200, 201) or not r.body:
        return DEVIATION
    m = _matched(r)
    if not m:
        return DEVIATION
    entry, sig, h = m
    # spec verification step 3: content-digest (when signed) must match the body
    if "content-digest" in entry["components"]:
        got = _digest_value(h.get("content-digest"))
        if got is None or got != hashlib.sha256(r.body).digest():
            return DEVIATION
    jwk = next((k for k in _profile_keys(ctx) if isinstance(k, dict)
                and k.get("kid") == entry["params"].get("keyid")), None)
    if not jwk:
        return DEVIATION                    # key not discoverable -> unverifiable
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        return INCONCLUSIVE                 # P-384 verification not implemented here
    Q = _jwk_point(jwk)
    if Q is None:
        return DEVIATION
    # reconstruct the RFC 9421 signature base from the declared components
    lines = []
    for c in entry["components"]:
        if c.startswith("@"):
            if c != "@status":
                return INCONCLUSIVE         # exotic derived component: can't rebuild
            v = str(r.status)
        else:
            if c not in h:
                return DEVIATION            # signed header absent from the response
            v = h[c].strip()
        lines.append(f'"{c}": {v}')
    lines.append(f'"@signature-params": {entry["raw"]}')
    base = "\n".join(lines).encode()
    return CLEAN if ecdsa_p256_verify(base, sig, Q) else DEVIATION

# ---- SIG-002: the merchant VERIFIES ES256-signed requests ----------------------
def _signed_headers(ctx, method, path, raw_body, d, kid, tamper=False):
    """RFC 9421 request signature headers per the spec's signed-component table
    (@method/@authority/@path always; ucp-agent, idempotency-key, content-digest,
    content-type because this request carries them; no query -> no @query)."""
    u = urlsplit(ctx.shopping_endpoint)
    digest = ("sha-256=:" + base64.b64encode(hashlib.sha256(raw_body).digest()).decode()
              + ":")
    agent = 'profile="https://spck.dev/agent"'
    idem = str(uuid.uuid4())
    hdrs = {"UCP-Agent": agent, "idempotency-key": idem,
            "request-id": str(uuid.uuid4()), "Content-Type": "application/json",
            "Content-Digest": digest}
    comps = ["@method", "@authority", "@path", "ucp-agent", "idempotency-key",
             "content-digest", "content-type"]
    raw_params = ("(" + " ".join(f'"{c}"' for c in comps) + ")"
                  + f';keyid="{kid}"')
    values = {"@method": method.upper(), "@authority": u.netloc,
              "@path": (u.path.rstrip("/") or "") + path,
              "ucp-agent": agent, "idempotency-key": idem,
              "content-digest": digest, "content-type": "application/json"}
    base = "\n".join([f'"{c}": {values[c]}' for c in comps]
                     + [f'"@signature-params": {raw_params}']).encode()
    sig = ecdsa_p256_sign(base, d)
    if tamper:
        sig = sig[:-1] + bytes([sig[-1] ^ 0x01])
    hdrs["Signature-Input"] = f"sig1={raw_params}"
    hdrs["Signature"] = "sig1=:" + base64.b64encode(sig).decode() + ":"
    return hdrs

def verified_request_resp(ctx):
    """Prove the merchant VERIFIES (a tampered ES256 signature is rejected 4xx),
    then return its response to a correctly ES256-signed create request."""
    jwk = (ctx.config.get("signature") or {}).get("request_private_jwk") or {}
    try:
        d = int.from_bytes(_b64u_dec(jwk.get("d")), "big")
    except Exception:
        return Resp(0, {}, b'{"probe":"signature.request_private_jwk has no valid d"}')
    kid = jwk.get("kid") or ""
    payload = _create_payload(ctx)
    raw = json.dumps(payload).encode()       # engine.fetch serializes identically
    bad = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", payload,
                _signed_headers(ctx, "POST", "/checkout-sessions", raw, d, kid,
                                tamper=True))
    if not (400 <= bad.status < 500):
        return Resp(0, {}, json.dumps(
            {"probe": "a request with a TAMPERED ES256 signature was not rejected "
                      "(the merchant did not verify it)",
             "observed_status": bad.status}).encode())
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", payload,
                 _signed_headers(ctx, "POST", "/checkout-sessions", raw, d, kid))

def p_signed_accepted(r):
    """SIG-002: the correctly ES256-signed request is processed normally."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("id") and r.json.get("status") in STATUS_ENUM \
        else DEVIATION

# ---- mutation constants (deterministic defect injections) ----------------------
def _der_int(x):
    b = x.to_bytes(32, "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return b"\x02" + bytes([len(b)]) + b

_DER = _der_int((1 << 255) + 7) + _der_int((1 << 255) + 11)
_DER_SIG_B64 = base64.b64encode(b"\x30" + bytes([len(_DER)]) + _DER).decode()
_B63_B64 = base64.b64encode(b"\x02" * 63).decode()
_BAD64_B64 = base64.b64encode(b"\x03" * 64).decode()   # right length, wrong signature
_SHA512_HDR = "sha-512=:" + base64.b64encode(b"\x00" * 64).decode() + ":"
_WRONG_DIGEST_HDR = ("sha-256=:"
                     + base64.b64encode(hashlib.sha256(b"tampered").digest()).decode()
                     + ":")
_SI_ALG = 'sig1=("@status" "content-digest" "content-type");alg="ES256";keyid="k1"'
_SI_METHOD = 'sig1=("@method" "content-digest" "content-type");keyid="k1"'
_SI_UNKNOWN_KID = ('sig1=("@status" "content-digest" "content-type")'
                   ';keyid="ucp-not-a-published-key"')

CHECKS_04_08_SIGNATURES = [
    MCheck("signature.response_headers_present", ["SIG-013"], "MUST",
           signed_create_resp, p_sig_headers_present,
           ["status:500", "hdrop:Signature", "hdrop:Signature-Input",
            "hset:Signature=sig2=:AAECAw==:"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.responses",), transport="rest", versions=V0408),
    MCheck("signature.response_content_digest", ["SIG-010", "SIG-012"], "MUST",
           signed_create_resp, p_content_digest,
           ["status:500", "hdrop:Content-Digest",
            f"hset:Content-Digest={_SHA512_HDR}",
            f"hset:Content-Digest={_WRONG_DIGEST_HDR}",
            "set:currency=\"EUR\""],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.responses",), transport="rest", versions=V0408),
    MCheck("signature.response_status_component", ["SIG-020"], "MUST",
           signed_create_resp, p_status_component,
           ["status:500", "hdrop:Signature-Input",
            f"hset:Signature-Input={_SI_METHOD}"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.responses",), transport="rest", versions=V0408),
    MCheck("signature.no_alg_parameter", ["SIG-006"], "MUST NOT",
           signed_create_resp, p_no_alg_param,
           ["status:500", "hdrop:Signature-Input",
            f"hset:Signature-Input={_SI_ALG}"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.responses",), transport="rest", versions=V0408),
    MCheck("signature.signing_keys_jwk", ["SIG-007"], "MUST",
           profile_doc_resp, p_signing_keys_jwk,
           ["drop:signing_keys", "set:signing_keys=[]",
            "drop:signing_keys.0.kid", "set:signing_keys.0.x=\"%%%\"",
            "set:signing_keys.0.x=\"AAAA\"", "corrupt-json"],
           cfg_needs=("signature",), transport="rest", versions=V0408),
    MCheck("signature.keyid_published", ["SIG-008"], "MUST",
           signed_create_resp, p_keyid_published,
           ["status:500", "hdrop:Signature-Input",
            f"hset:Signature-Input={_SI_UNKNOWN_KID}"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.responses",), transport="rest", versions=V0408),
    MCheck("signature.raw_rs_encoding", ["SIG-019"], "MUST",
           signed_create_resp, p_raw_rs_encoding,
           ["status:500", "hdrop:Signature",
            f"hset:Signature=sig1=:{_DER_SIG_B64}:",
            f"hset:Signature=sig1=:{_B63_B64}:"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.responses",), transport="rest", versions=V0408),
    MCheck("signature.rfc9421_response_verifies", ["SIG-001"], "MUST",
           signed_create_resp, p_response_verifies,
           ["status:500", "hdrop:Signature",
            f"hset:Signature=sig1=:{_BAD64_B64}:",
            "hset:Content-Type=text/plain",
            "set:currency=\"EUR\""],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.responses",), transport="rest", versions=V0408),
    MCheck("signature.verifies_es256_requests", ["SIG-002"], "MUST",
           verified_request_resp, p_signed_accepted,
           ["status:401", "status:400", "status:500", "empty", "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("signature.request_private_jwk",), transport="rest",
           versions=V0408),
]
