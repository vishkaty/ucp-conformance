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


def mint_checkout_jwt(checkout_obj, kid="merchant_2026", signer_seed=None):
    """The merchant-signed compact JWS wrapping the full UCP checkout.

    `signer_seed` overrides the signing key (matrix case 19: a checkout_jwt
    signed by a key that is NOT the merchant's must fail verification)."""
    d, _ = crypto.keypair(MERCHANT_SEED if signer_seed is None else signer_seed)
    payload = json.dumps(checkout_obj, separators=(",", ":"), sort_keys=True).encode()
    return crypto.jws_compact_sign({"alg": "ES256"}, payload, d, kid=kid)


def mint_chain(checkout_obj, aud="merchant", nonce="merchant-nonce",
               strip_embedded_mauth=False, exp=None, iat=None, nbf=None,
               hop1_typ="kb+sd-jwt", hop1_unsigned=False, constraints=None,
               open_cnf=True, open_vct="mandate.checkout.open.1",
               closed_vct="mandate.checkout.1", omit_checkout_hash=False,
               close_seed=None, hop0_unsigned=False, hop1_alg="ES256",
               closed_extra=None, checkout_signer_seed=None):
    """Mint a 2-hop open->closed checkout-mandate chain over `checkout_obj`.

    Returns the `~~` wire. Negative-case knobs (DATA, not per-case code — every
    knob defaults to the byte-identical valid chain): `strip_embedded_mauth=True`
    mints the PAY-042 violation (embedded checkout without merchant_authorization);
    `exp`/`iat`/`nbf` (epoch seconds) set freshness claims on the closed hop;
    `hop1_typ` overrides the KB hop's typ; `hop1_unsigned=True` emits the closed
    hop as alg:none with a junk signature (the mandatory-negative alg:none case).
    Matrix knobs: `open_cnf=False` omits the open mandate's cnf (case 6);
    `open_vct`/`closed_vct` override the vct literals (case 5); `omit_checkout_hash`
    drops the closed mandate's checkout_hash (case 4); `close_seed` closes the
    chain with a key OTHER than the one hop0's cnf binds (cases 25/38: consent
    forgery — cnf still names the real agent); `hop0_unsigned` emits the ROOT hop
    as alg:none (case 21); `hop1_alg` declares a different alg on the closing
    hop's header (case 22: declared/actual mismatch — the sig bytes stay ES256);
    `closed_extra` merges extra members into the closed mandate value (case 29:
    a terminal hop smuggling a cnf); `checkout_signer_seed` signs the embedded
    checkout_jwt with a non-merchant key (case 19).
    """
    d_plat, _ = crypto.keypair(PLATFORM_SEED)
    d_agent, q_agent = crypto.keypair(AGENT_SEED)
    d_close = d_agent if close_seed is None else crypto.keypair(close_seed)[0]
    # cnf JWK kept BARE (kty/crv/x/y/kid): the reference re-validates cnf.jwk
    # through its pydantic model, and extra members like `use` round-trip as
    # enums that its key reconstruction chokes on.
    agent_jwk = {k: v for k, v in
                 crypto.jwk_from_pub("ap2-agent-fixture", q_agent).items()
                 if k not in ("use", "alg")}

    embedded = ({k: v for k, v in checkout_obj.items() if k != "ap2"}
                if strip_embedded_mauth else checkout_obj)
    checkout_jwt = mint_checkout_jwt(embedded, signer_seed=checkout_signer_seed)
    checkout_hash = sdjwt.hash_ascii(checkout_jwt, "sha-256")

    # hop0 — the user/platform-signed OPEN mandate binding the agent's key (consent).
    open_value = {"vct": open_vct,
                  "constraints": constraints if constraints is not None else []}
    if open_cnf:
        open_value["cnf"] = {"jwk": agent_jwk}
    d0 = sdjwt.encode_array_disclosure(_salt(), open_value)
    hop0_payload = {"delegate_payload": [{"...": sdjwt.disclosure_digest(d0, "sha-256")}],
                    "_sd_alg": "sha-256"}
    if hop0_unsigned:
        hb = crypto.b64url(json.dumps(
            {"alg": "none", "typ": "example+sd-jwt", "kid": "ap2-platform-fixture"},
            separators=(",", ":"), sort_keys=True).encode())
        hop0_jwt = hb + "." + crypto.b64url(_payload_json(hop0_payload)) + "." + \
            crypto.b64url(b"junk-signature")
    else:
        hop0_jwt = _sign_hop({"alg": "ES256", "typ": "example+sd-jwt",
                              "kid": "ap2-platform-fixture"}, hop0_payload, d_plat)
    hop0 = hop0_jwt + "~" + d0 + "~"

    # hop1 — the agent-signed CLOSED mandate, sd_hash-bound to hop0.
    closed_value = {"vct": closed_vct, "checkout_hash": checkout_hash}
    if omit_checkout_hash:
        del closed_value["checkout_hash"]
    if closed_extra:
        closed_value.update(closed_extra)
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
        hop1_jwt = _sign_hop({"alg": hop1_alg, "typ": hop1_typ,
                              "kid": "ap2-agent-fixture"}, hop1_payload, d_close)
    hop1 = hop1_jwt + "~" + d1 + "~" + d2 + "~"

    # join: the non-final hop's trailing tilde is stripped (reference convention).
    return hop0[:-1] + "~~" + hop1


_DEFAULT = object()   # sentinel: "use the derived default" for mint knobs


def mint_payment_chain(checkout_jwt, aud="merchant", nonce="merchant-nonce",
                       payment_instrument=None, transaction_id=_DEFAULT,
                       payment_amount=None):
    """Mint a 2-hop open->closed PAYMENT-mandate chain bound to `checkout_jwt`
    (transaction_id = H(checkout_jwt), the payment<->checkout binding). Same role
    keys and wire conventions as mint_chain — the platform's SECOND distinct
    artifact (PAY-041).

    Negative-case knobs (data, not per-case code): `payment_instrument` replaces
    the default instrument dict verbatim — including TYPE-SPECIFIC extension
    fields such as x402's payee_address/facilitator (the AP2#329 surface);
    `transaction_id` overrides the checkout binding: a string (including "") is
    used as-is, None OMITS the claim (the AP2#330 fail-closed negatives);
    `payment_amount` replaces the amount object verbatim (matrix case 9: a
    non-ISO-4217 currency / non-integer minor-unit amount must be rejected)."""
    d_plat, _ = crypto.keypair(PLATFORM_SEED)
    d_agent, q_agent = crypto.keypair(AGENT_SEED)
    agent_jwk = {k: v for k, v in
                 crypto.jwk_from_pub("ap2-agent-fixture", q_agent).items()
                 if k not in ("use", "alg")}

    open_value = {"vct": "mandate.payment.open.1", "constraints": [],
                  "cnf": {"jwk": agent_jwk}}
    d0 = sdjwt.encode_array_disclosure(_salt(), open_value)
    hop0_payload = {"delegate_payload": [{"...": sdjwt.disclosure_digest(d0, "sha-256")}],
                    "_sd_alg": "sha-256"}
    hop0_jwt = _sign_hop({"alg": "ES256", "typ": "example+sd-jwt",
                          "kid": "ap2-platform-fixture"}, hop0_payload, d_plat)
    hop0 = hop0_jwt + "~" + d0 + "~"

    closed_value = {"vct": "mandate.payment.1",
                    "transaction_id": sdjwt.hash_ascii(checkout_jwt, "sha-256"),
                    "payee": {"id": "s-1", "name": "Shop"},
                    "payment_amount": payment_amount if payment_amount is not None
                    else {"amount": 1000, "currency": "USD"},
                    "payment_instrument": payment_instrument if payment_instrument
                    is not None else {"id": "pi-1", "type": "credit"}}
    if transaction_id is None:
        del closed_value["transaction_id"]
    elif transaction_id is not _DEFAULT:
        closed_value["transaction_id"] = transaction_id
    d1 = sdjwt.encode_array_disclosure(_salt(), closed_value)
    hop1_payload = {
        "delegate_payload": [{"...": sdjwt.disclosure_digest(d1, "sha-256")}],
        "iat": int(time.time()), "aud": aud, "nonce": nonce,
        "sd_hash": sdjwt.parse_hop(hop0).sd_hash(), "_sd_alg": "sha-256",
    }
    hop1_jwt = _sign_hop({"alg": "ES256", "typ": "kb+sd-jwt",
                          "kid": "ap2-agent-fixture"}, hop1_payload, d_agent)
    hop1 = hop1_jwt + "~" + d1 + "~"
    return hop0[:-1] + "~~" + hop1


def platform_public_jwk():
    """The fixture platform's public JWK (stands in for profile signing_keys)."""
    _, q = crypto.keypair(PLATFORM_SEED)
    return crypto.jwk_from_pub("ap2-platform-fixture", q)
