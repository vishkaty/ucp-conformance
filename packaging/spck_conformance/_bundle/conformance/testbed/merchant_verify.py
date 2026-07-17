#!/usr/bin/env python3
"""
merchant_verify.py — the MERCHANT-side verification of an incoming
`ap2.checkout_mandate` (ap2-mandates.md "Business Verification" + error-code
table), built entirely from our frozen-layer primitives so the fixture server can
enforce AP2 without the reference SDK.

verify_checkout_mandate(...) returns None on success, or the spec error code:

  mandate_invalid_signature      chain unparseable, integrity broken, hop signature
                                 invalid, or checkout_hash != H(checkout_jwt)
  mandate_expired                the closed mandate carries exp and it has passed
  merchant_authorization_invalid embedded checkout lacks ap2.merchant_authorization,
                                 or that nested signature does not verify
  mandate_scope_mismatch         the embedded checkout is bound to a different
                                 session (id) or different terms (totals)
  (mandate_required and agent_missing_key are the CALLER's codes: missing mandate,
   unresolvable platform key.)

Fixture key contract (mint.py): the platform root key stands in for the
profile-resolved `signing_keys` entry; the agent key is bound via hop0's cnf.
"""
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))
import crypto  # noqa: E402
import frozen  # noqa: E402
import nested  # noqa: E402
import sdjwt  # noqa: E402


def _cnf_pub(hop):
    """Extract cnf.jwk from a hop's disclosed values -> (x, y) point, or None."""
    for disc in hop.disclosures:
        try:
            arr = sdjwt.decode_disclosure(disc)
        except Exception:
            continue
        val = arr[-1]
        if isinstance(val, dict):
            jwk = (val.get("cnf") or {}).get("jwk")
            if isinstance(jwk, dict):
                try:
                    return crypto.pub_from_jwk(jwk)
                except Exception:
                    return None
    return None


def _closed_claim(hop, name):
    """A claim from the closed mandate's disclosed values or its payload."""
    for disc in hop.disclosures:
        try:
            arr = sdjwt.decode_disclosure(disc)
        except Exception:
            continue
        val = arr[-1]
        if isinstance(val, dict) and isinstance(val.get(name), int):
            return val[name]
    v = hop.payload.get(name)
    return v if isinstance(v, int) else None


_CLOCK_SKEW = 300   # seconds, the reference verifier's default


def verify_checkout_mandate(wire, platform_Q, merchant_Q,
                            expected_id=None, expected_totals=None, now=None):
    """Full merchant-side verification. Returns None (ok) or an AP2 error code."""
    # chain structure + RFC 9901 integrity + sd_hash binding (our frozen layer)
    ok, _reason = frozen.frozen_verify(wire)
    if not ok:
        return "mandate_invalid_signature"
    hops = sdjwt.parse_chain(wire)
    if len(hops) < 2:
        return "mandate_invalid_signature"   # no closed/consent hop

    # hop0 signed by the platform/user key (profile signing_keys stand-in)
    if crypto.jws_compact_verify(hops[0].issuer_jwt, platform_Q) is None:
        return "mandate_invalid_signature"
    # each later hop: dSD-JWT typ invariant + signed by the key bound in the
    # previous hop's cnf (alg:none and alg-swaps fail inside jws_compact_verify)
    for i in range(1, len(hops)):
        typ = hops[i].header.get("typ")
        if not (isinstance(typ, str) and typ.startswith("kb+sd-jwt")):
            return "mandate_invalid_signature"
        cnf_q = _cnf_pub(hops[i - 1])
        if cnf_q is None:
            return "mandate_invalid_signature"
        if crypto.jws_compact_verify(hops[i].issuer_jwt, cnf_q) is None:
            return "mandate_invalid_signature"

    # freshness on the closed mandate: exp passed -> expired; an iat from the
    # future (beyond skew) or an unreached nbf -> not (yet) valid.
    t = now or int(time.time())
    exp = _closed_claim(hops[-1], "exp")
    if exp is not None and t > exp:
        return "mandate_expired"
    iat = _closed_claim(hops[-1], "iat")
    if iat is not None and iat > t + _CLOCK_SKEW:
        return "mandate_invalid_signature"
    nbf = _closed_claim(hops[-1], "nbf")
    if nbf is not None and nbf > t + _CLOCK_SKEW:
        return "mandate_invalid_signature"

    # UCP nested binding: checkout_hash identity, merchant-signed checkout_jwt,
    # embedded merchant_authorization present and valid
    ok, reason = nested.verify_ucp_nested(wire, merchant_Q)
    if not ok:
        if "merchant_authorization" in reason:
            return "merchant_authorization_invalid"
        return "mandate_invalid_signature"

    # scope: the embedded checkout must be THIS session's checkout (terms match)
    if expected_id is not None or expected_totals is not None:
        checkout_jwt, _ = nested._find_mandate_fields(hops[-1])
        payload = crypto.jws_compact_verify(checkout_jwt, merchant_Q)
        embedded = json.loads(payload)
        if expected_id is not None and embedded.get("id") != expected_id:
            return "mandate_scope_mismatch"
        if expected_totals is not None and embedded.get("totals") != expected_totals:
            return "mandate_scope_mismatch"

    return None
