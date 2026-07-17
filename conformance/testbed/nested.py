#!/usr/bin/env python3
"""
nested.py — the UCP-specific NESTED CRYPTOGRAPHIC BINDING of an AP2 checkout
mandate (ap2-mandates.md L207-209 / PAY-042):

    "The checkout mandate MUST contain the full checkout response including the
     ap2.merchant_authorization field. This creates a nested cryptographic binding
     where the platform's signature covers the business's signature."

`verify_ucp_nested` checks the layers UCP adds ON TOP of the generic AP2 chain —
all of them frozen-standard crypto (compact/detached JWS + JCS + SHA-256), so this
runs with our own code only, no reference SDK:

  1. the mandate's `checkout_jwt` / `checkout_hash` fields are present,
  2. checkout_hash == H(ASCII(checkout_jwt))   (the freshness/identity binding),
  3. checkout_jwt is a valid merchant-signed compact JWS,
  4. the embedded checkout CONTAINS ap2.merchant_authorization  (PAY-042),
  5. that merchant_authorization is the merchant's valid detached JWS over
     JCS(embedded checkout minus ap2)          (spec L395-408, the business's
     own-signature re-verification inside the mandate).

The generic chain layer (hop signatures, sd_hash binding, disclosures) is checked
separately by frozen.py / the reference verifier; this module assumes a chain that
parses and asks only whether the UCP nesting holds.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))
import crypto  # noqa: E402
import sdjwt  # noqa: E402


def _find_mandate_fields(hop):
    """Pull checkout_jwt / checkout_hash from a hop's disclosures or payload."""
    checkout_jwt = checkout_hash = None

    def scan_obj(obj):
        nonlocal checkout_jwt, checkout_hash
        if isinstance(obj, dict):
            if isinstance(obj.get("checkout_jwt"), str):
                checkout_jwt = obj["checkout_jwt"]
            if isinstance(obj.get("checkout_hash"), str):
                checkout_hash = obj["checkout_hash"]
            for v in obj.values():
                scan_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                scan_obj(item)

    for disc in hop.disclosures:
        try:
            arr = sdjwt.decode_disclosure(disc)
        except Exception:
            continue
        if len(arr) == 3 and arr[1] == "checkout_jwt" and isinstance(arr[2], str):
            checkout_jwt = arr[2]
        scan_obj(arr[-1])
    scan_obj(hop.payload)
    return checkout_jwt, checkout_hash


def verify_ucp_nested(wire, merchant_Q):
    """Return (ok: bool, reason: str) for the UCP nested-binding layers."""
    try:
        hops = sdjwt.parse_chain(wire)
    except ValueError as exc:
        return False, f"structure: {exc}"

    # The closed checkout mandate is the terminal hop.
    checkout_jwt, checkout_hash = _find_mandate_fields(hops[-1])
    if not checkout_jwt:
        return False, "mandate carries no checkout_jwt"
    if not checkout_hash:
        return False, "mandate carries no checkout_hash"

    # 2. identity binding: the hash names exactly this checkout_jwt.
    if sdjwt.hash_ascii(checkout_jwt, hops[-1].sd_alg) != checkout_hash:
        return False, "checkout_hash != H(checkout_jwt)"

    # 3. the embedded checkout is merchant-signed.
    payload_bytes = crypto.jws_compact_verify(checkout_jwt, merchant_Q)
    if payload_bytes is None:
        return False, "checkout_jwt signature invalid for the merchant key"
    try:
        checkout = json.loads(payload_bytes)
    except Exception:
        return False, "checkout_jwt payload is not JSON"

    # 4. PAY-042: the FULL checkout, including ap2.merchant_authorization.
    ap2 = checkout.get("ap2")
    auth = ap2.get("merchant_authorization") if isinstance(ap2, dict) else None
    if not isinstance(auth, str) or not auth:
        return False, "embedded checkout lacks ap2.merchant_authorization (PAY-042)"

    # 5. the nested business signature verifies over JCS(checkout minus ap2).
    body = {k: v for k, v in checkout.items() if k != "ap2"}
    if not crypto.jws_detached_verify(auth, body, merchant_Q):
        return False, "embedded merchant_authorization does not verify"

    return True, "ok"
