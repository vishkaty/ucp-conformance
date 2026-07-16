#!/usr/bin/env python3
"""
area_04_08_ap2.py — AP2 mandate merchant-authorization conformance (2026-04-08).

Covers the AP2 requirements that a merchant's OWN emitted signature must satisfy —
the surface no one else tests (the official suite's ap2_test uses a placeholder
mandate string, so real AP2 mandate crypto is unverified anywhere). Each check runs
against a fixture whose ap2.merchant_authorization is a genuine detached JWS over the
JCS-canonicalized checkout body; the engine's mutations then break it and each mutant
MUST be caught (kill-safe), so the check can't false-pass.

  payment.ap2_authorization_present  PAY-034, PAY-039 — merchant_authorization is present
                                      and shaped as a detached JWS (header..sig).
  payment.ap2_authorization_authentic PAY-031, PAY-040, PAY-043 — the signature verifies
                                      over the JCS payload with an ES256 header, so any
                                      payload edit or alg swap is rejected.

The enforce-side AP2 MUSTs (reject complete_checkout without a valid mandate: PAY-035/
038/045/047) are behavioral and need an AP2-capable reference server, which does not yet
exist — they remain gaps (see ops/scope-ap2-mandate-cluster). ap2-crypto gate proves the
JCS/JWS primitives independently.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "common"))
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))
import json                                                    # noqa: E402
from engine import Check                                       # noqa: E402
from verdict_gate import CLEAN, DEVIATION, INCONCLUSIVE        # noqa: E402
from schema_check import fixture_resp                          # noqa: E402
import crypto                                                  # noqa: E402

# The spec (ap2_mandate.json) allows alg ES256/ES384/ES512. Our verifier is the
# ES256 baseline; a spec-valid ES384/ES512 authorization is reported INCONCLUSIVE
# (honest "not verifiable here"), never a false DEVIATION.
_SPEC_ALGS = {"ES256", "ES384", "ES512"}

# Same fixed key the fixture generator signs with (stands in for the merchant's
# discovery-resolvable signing key).
_MERCHANT_SEED = b"ap2-merchant-fixture"
_, _Q = crypto.keypair(_MERCHANT_SEED)

_FIXTURE = "ap2/checkout_ap2.valid.json"


def _authorization(resp):
    if resp.json is None:
        return None
    ap2 = resp.json.get("ap2")
    if not isinstance(ap2, dict):
        return None
    auth = ap2.get("merchant_authorization")
    return auth if isinstance(auth, str) else None


def ap2_present(resp):
    """merchant_authorization present and shaped as a detached JWS (header..sig)."""
    auth = _authorization(resp)
    if not auth:
        return DEVIATION
    parts = auth.split(".")
    return CLEAN if (len(parts) == 3 and parts[1] == "") else DEVIATION


def _header_alg(auth):
    try:
        return json.loads(crypto.b64url_decode(auth.split(".")[0])).get("alg")
    except Exception:
        return None


def ap2_authentic(resp):
    """The detached JWS verifies over JCS(checkout minus ap2) — binds header + payload.

    ES256 (the baseline the fixture and most merchants use) is verified. A spec-valid
    ES384/ES512 authorization we cannot verify with the ES256 primitive is INCONCLUSIVE,
    not a false deviation; an alg outside the spec set is a DEVIATION.
    """
    auth = _authorization(resp)
    if not auth:
        return DEVIATION
    alg = _header_alg(auth)
    if alg not in _SPEC_ALGS:
        return DEVIATION
    if alg != "ES256":
        return INCONCLUSIVE
    payload = {k: v for k, v in resp.json.items() if k != "ap2"}
    return CLEAN if crypto.jws_detached_verify(auth, payload, _Q) else DEVIATION


def _fetch(base):
    return fixture_resp("2026-04-08", _FIXTURE)


CHECKS = [
    Check("payment.ap2_authorization_present", ["PAY-034", "PAY-039"], "MUST",
          _fetch, ap2_present,
          ["drop:ap2.merchant_authorization",
           'set:ap2.merchant_authorization="not-a-jws"',
           'set:ap2={}']),
    Check("payment.ap2_authorization_authentic", ["PAY-031", "PAY-040", "PAY-043"], "MUST",
          _fetch, ap2_authentic,
          ['set:currency="EUR"',
           "set:line_items.0.quantity=99",
           "set:totals.1.amount=1"]),
]
