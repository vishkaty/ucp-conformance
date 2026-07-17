#!/usr/bin/env python3
"""
mint.py — mint an AP2 checkout-mandate delegate chain using OUR OWN frozen-layer
primitives only (RFC 9901 disclosures/_sd/sd_hash + ES256 compact JWS). No
reference SDK needed, so the enforce-side checks can act as the "platform" role
anywhere. The wire shape replicates the reference byte-conventions exactly
(observed in the committed goldens): array-element disclosures under
`delegate_payload`, `~` within a hop, `~~` between hops with the non-final hop's
trailing tilde stripped.

Cross-proof: validate_ap2_e2e's semantic tier feeds a chain minted here to the
REFERENCE verifier — our issuer interoperating with their verifier is the
two-way interop evidence (their issuer -> our verifier is already covered by the
goldens).

Fixture roles (deterministic seeds, the testbed contract):
  user/platform key  b"ap2-platform-fixture"  — signs the open mandate (consent)
  agent key          b"ap2-agent-fixture"     — closes it over the checkout
  merchant key       b"ap2-merchant-fixture"  — signs checkout_jwt + mAuth
"""
import json
import pathlib
import secrets
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))
import crypto  # noqa: E402
import sdjwt  # noqa: E402

PLATFORM_SEED = b"ap2-platform-fixture"
AGENT_SEED = b"ap2-agent-fixture"
MERCHANT_SEED = b"ap2-merchant-fixture"


def _salt():
    return secrets.token_urlsafe(16)


def _payload_json(obj):
    # match the reference/sd-jwt lib: default separators, unsorted insertion order
    return json.dumps(obj).encode("utf-8")


def _sign_hop(header, payload_obj, d):
    return crypto.jws_compact_sign(header, _payload_json(payload_obj), d)


def mint_checkout_jwt(checkout_obj, kid="merchant_2026"):
    """The merchant-signed compact JWS wrapping the full UCP checkout."""
    d, _ = crypto.keypair(MERCHANT_SEED)
    payload = json.dumps(checkout_obj, separators=(",", ":"), sort_keys=True).encode()
    return crypto.jws_compact_sign({"alg": "ES256"}, payload, d, kid=kid)


def mint_chain(checkout_obj, aud="merchant", nonce="merchant-nonce",
               strip_embedded_mauth=False, exp=None, iat=None, nbf=None,
               hop1_typ="kb+sd-jwt", hop1_unsigned=False, constraints=None):
    """Mint a 2-hop open->closed checkout-mandate chain over `checkout_obj`.

    Returns the `~~` wire. Negative-case knobs: `strip_embedded_mauth=True` mints
    the PAY-042 violation (embedded checkout without merchant_authorization);
    `exp`/`iat`/`nbf` (epoch seconds) set freshness claims on the closed hop;
    `hop1_typ` overrides the KB hop's typ; `hop1_unsigned=True` emits the closed
    hop as alg:none with a junk signature (the mandatory-negative alg:none case).
    """
    d_plat, _ = crypto.keypair(PLATFORM_SEED)
    d_agent, q_agent = crypto.keypair(AGENT_SEED)
    # cnf JWK kept BARE (kty/crv/x/y/kid): the reference re-validates cnf.jwk
    # through its pydantic model, and extra members like `use` round-trip as
    # enums that its key reconstruction chokes on.
    agent_jwk = {k: v for k, v in
                 crypto.jwk_from_pub("ap2-agent-fixture", q_agent).items()
                 if k not in ("use", "alg")}

    embedded = ({k: v for k, v in checkout_obj.items() if k != "ap2"}
                if strip_embedded_mauth else checkout_obj)
    checkout_jwt = mint_checkout_jwt(embedded)
    checkout_hash = sdjwt.hash_ascii(checkout_jwt, "sha-256")

    # hop0 — the user/platform-signed OPEN mandate binding the agent's key (consent).
    open_value = {"vct": "mandate.checkout.open.1",
                  "constraints": constraints if constraints is not None else [],
                  "cnf": {"jwk": agent_jwk}}
    d0 = sdjwt.encode_array_disclosure(_salt(), open_value)
    hop0_payload = {"delegate_payload": [{"...": sdjwt.disclosure_digest(d0, "sha-256")}],
                    "_sd_alg": "sha-256"}
    hop0_jwt = _sign_hop({"alg": "ES256", "typ": "example+sd-jwt",
                          "kid": "ap2-platform-fixture"}, hop0_payload, d_plat)
    hop0 = hop0_jwt + "~" + d0 + "~"

    # hop1 — the agent-signed CLOSED mandate, sd_hash-bound to hop0.
    closed_value = {"vct": "mandate.checkout.1", "checkout_hash": checkout_hash}
    if exp is not None:
        closed_value["exp"] = int(exp)
    d2 = sdjwt.encode_disclosure(_salt(), "checkout_jwt", checkout_jwt)
    closed_value["_sd"] = [sdjwt.disclosure_digest(d2, "sha-256")]
    d1 = sdjwt.encode_array_disclosure(_salt(), closed_value)
    hop1_payload = {
        "delegate_payload": [{"...": sdjwt.disclosure_digest(d1, "sha-256")}],
        "iat": int(time.time()) if iat is None else int(iat),
        "aud": aud, "nonce": nonce,
        "sd_hash": sdjwt.parse_hop(hop0).sd_hash(), "_sd_alg": "sha-256",
    }
    if nbf is not None:
        hop1_payload["nbf"] = int(nbf)
    if hop1_unsigned:
        hb = crypto.b64url(json.dumps(
            {"alg": "none", "typ": hop1_typ, "kid": "ap2-agent-fixture"},
            separators=(",", ":"), sort_keys=True).encode())
        hop1_jwt = hb + "." + crypto.b64url(_payload_json(hop1_payload)) + "." + \
            crypto.b64url(b"junk-signature")
    else:
        hop1_jwt = _sign_hop({"alg": "ES256", "typ": hop1_typ,
                              "kid": "ap2-agent-fixture"}, hop1_payload, d_agent)
    hop1 = hop1_jwt + "~" + d1 + "~" + d2 + "~"

    # join: the non-final hop's trailing tilde is stripped (reference convention).
    return hop0[:-1] + "~~" + hop1


def platform_public_jwk():
    """The fixture platform's public JWK (stands in for profile signing_keys)."""
    _, q = crypto.keypair(PLATFORM_SEED)
    return crypto.jwk_from_pub("ap2-platform-fixture", q)
