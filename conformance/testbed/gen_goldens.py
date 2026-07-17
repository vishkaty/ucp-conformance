#!/usr/bin/env python3
"""
gen_goldens.py — drive the PINNED AP2 reference SDK (google-agentic-commerce/AP2
@ e1ea56d) to emit REAL golden mandate delegate chains for the three roles
(user -> agent -> merchant), used as authoritative fixtures for the E2E suite.

Hybrid-(C): the reference owns the moving delegate-chain machinery; we capture its
output as goldens and cross-check the FROZEN RFC-9901 mechanics against our own
codec (conformance/common/sdjwt.py) in validate_sdjwt_vs_reference.py.

The reference uses random keys + salts per run, so goldens are not byte-reproducible;
we commit one captured run. Re-run this (with `ap2` installed) to refresh them:

    pip install "git+https://github.com/google-agentic-commerce/AP2@e1ea56d"
    python3 conformance/testbed/gen_goldens.py conformance/selfcheck/fixtures/ap2/golden

Exit 2 if `ap2` is not installed (generation is a maintainer step, not a CI gate).
"""
import json
import pathlib
import sys

try:
    from ap2.sdk.mandate import MandateClient
    from ap2.sdk.generated.checkout_mandate import CheckoutMandate
    from ap2.sdk.generated.open_checkout_mandate import OpenCheckoutMandate
    from ap2.sdk.generated.payment_mandate import PaymentMandate
    from ap2.sdk.generated.open_payment_mandate import OpenPaymentMandate
    from ap2.sdk.generated.types.amount import Amount
    from ap2.sdk.generated.types.merchant import Merchant
    from ap2.sdk.generated.types.payment_instrument import PaymentInstrument
    from ap2.tests.conftest import make_checkout_jwt, make_cnf, make_line_item
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwcrypto.jwk import JWK
except ImportError:
    print("gen-goldens: SKIP (ap2 reference not installed)")
    sys.exit(2)

REF_SHA = "e1ea56db72a6385bce3e5c1112b3a56ce60acb43"


def _named_key(kid):
    k = ec.generate_private_key(ec.SECP256R1())
    d = json.loads(JWK.from_pyca(k).export())
    d["kid"] = kid
    return JWK.from_json(json.dumps(d))


def _roles():
    user = _named_key("user-key-1")
    agent = _named_key("agent-key-1")
    return user, agent, JWK.from_json(user.export_public())


def checkout_golden():
    user, agent, user_pub = _roles()
    holder = MandateClient()
    open_tok = holder.create(
        payloads=[OpenCheckoutMandate(constraints=[], cnf=make_cnf(agent))],
        issuer_key=user,
    )
    checkout_jwt = make_checkout_jwt(
        merchant=Merchant(id="m-1", name="Store"),
        line_items=[make_line_item("item-1", "Widget", quantity=1, unit_price=1000)],
    )
    chain = holder.present(
        holder_key=agent,
        mandate_token=open_tok,
        payloads=[CheckoutMandate(checkout_jwt=checkout_jwt, checkout_hash="hash")],
        aud="merchant",
        nonce="merchant-nonce",
    )
    payloads = holder.verify(token=chain, key_or_provider=lambda _t: user_pub)
    from ap2.sdk.checkout_mandate_chain import CheckoutMandateChain
    violations = CheckoutMandateChain.parse(payloads).verify(checkout_jwt=checkout_jwt)
    return {
        "scenario": "checkout_mandate_chain_human_not_present",
        "ref_sha": REF_SHA,
        "wire": chain,
        "checkout_jwt": checkout_jwt,
        "user_public_jwk": json.loads(user_pub.export_public()),
        "aud": "merchant",
        "nonce": "merchant-nonce",
        "expected_violations": violations,
    }


def payment_golden():
    user, agent, user_pub = _roles()
    holder = MandateClient()
    open_tok = holder.create(
        payloads=[OpenPaymentMandate(constraints=[], cnf=make_cnf(agent))],
        issuer_key=user,
    )
    pm = PaymentMandate(
        transaction_id="tx_1",
        payee=Merchant(name="Shop", id="s-1"),
        payment_amount=Amount(amount=1000, currency="USD"),
        payment_instrument=PaymentInstrument(id="pi-1", type="credit"),
    )
    chain = holder.present(
        holder_key=agent, mandate_token=open_tok, payloads=[pm],
        aud="merchant", nonce="merchant-nonce",
    )
    payloads = holder.verify(token=chain, key_or_provider=lambda _t: user_pub)
    from ap2.sdk.payment_mandate_chain import PaymentMandateChain
    violations = PaymentMandateChain.parse(payloads).verify()
    return {
        "scenario": "payment_mandate_chain_human_not_present",
        "ref_sha": REF_SHA,
        "wire": chain,
        "user_public_jwk": json.loads(user_pub.export_public()),
        "aud": "merchant",
        "nonce": "merchant-nonce",
        "expected_violations": violations,
    }


def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in (("checkout_chain", checkout_golden), ("payment_chain", payment_golden)):
        g = fn()
        (out / f"{name}.json").write_text(json.dumps(g, indent=2) + "\n")
        print(f"  wrote {name}.json  (violations={g['expected_violations']})")
    print(f"goldens written to {out}  (ref {REF_SHA[:10]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
