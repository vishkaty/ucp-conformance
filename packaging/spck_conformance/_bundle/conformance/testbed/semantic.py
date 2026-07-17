#!/usr/bin/env python3
"""
semantic.py — reference-backed AP2 E2E cases (the delegate-chain SEMANTICS that
track the moving draft: signatures, consent binding, replay, constraints, cross-
mandate integration). Each case mints a fresh chain via the pinned reference
(user -> agent -> merchant) and asserts the reference verifier ACCEPTS a valid
flow and REJECTS each violation.

These run only when the reference SDK is importable (CI installs the pinned commit;
the gate skips this tier otherwise, like validate_sdjwt_vs_reference tier 2). The
frozen tier (frozen.py) always runs.

Outcome convention: a case's run() returns "PASS" (reference accepted, no
violations) or "REJECT" (verify raised, OR chain.verify returned violations, OR a
testbed policy rejected it). The runner compares that to the case's `expect`.
"""
import json

try:
    from ap2.sdk.mandate import MandateClient
    from ap2.sdk.checkout_mandate_chain import CheckoutMandateChain
    from ap2.sdk.payment_mandate_chain import PaymentMandateChain
    from ap2.sdk.generated.checkout_mandate import CheckoutMandate
    from ap2.sdk.generated.open_checkout_mandate import (
        AllowedMerchants, OpenCheckoutMandate)
    from ap2.sdk.generated.payment_mandate import PaymentMandate
    from ap2.sdk.generated.open_payment_mandate import (
        AllowedPayees, AmountRange, OpenPaymentMandate)
    from ap2.sdk.generated.types.amount import Amount
    from ap2.sdk.generated.types.merchant import Merchant
    from ap2.sdk.generated.types.payment_instrument import PaymentInstrument
    from ap2.tests.conftest import make_checkout_jwt, make_cnf, make_line_item
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwcrypto.jwk import JWK
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


def _key(kid):
    k = ec.generate_private_key(ec.SECP256R1())
    d = json.loads(JWK.from_pyca(k).export())
    d["kid"] = kid
    return JWK.from_json(json.dumps(d))


def _pub(k):
    return JWK.from_json(k.export_public())


# ── checkout builders ─────────────────────────────────────────────────────

def _checkout_chain(open_cnf_key, close_holder_key, merchant_id="m-1",
                    constraints=None, aud="merchant", nonce="merchant-nonce"):
    """Mint a 2-hop checkout chain. Vary the keys/constraints to force rejects."""
    user = _key("user-key-1")
    holder = MandateClient()
    open_tok = holder.create(
        payloads=[OpenCheckoutMandate(constraints=constraints or [],
                                      cnf=make_cnf(open_cnf_key))],
        issuer_key=user,
    )
    checkout_jwt = make_checkout_jwt(
        merchant=Merchant(id=merchant_id, name="Store"),
        line_items=[make_line_item("item-1", "Widget", quantity=1, unit_price=1000)],
    )
    chain = holder.present(
        holder_key=close_holder_key, mandate_token=open_tok,
        payloads=[CheckoutMandate(checkout_jwt=checkout_jwt, checkout_hash="hash")],
        aud=aud, nonce=nonce,
    )
    return holder, chain, checkout_jwt, _pub(user)


def _checkout_outcome(chain, root_pub, checkout_jwt, expected_aud="merchant",
                      expected_nonce="merchant-nonce", expected_checkout_hash=None):
    holder = MandateClient()
    try:
        payloads = holder.verify(token=chain, key_or_provider=lambda _t: root_pub,
                                 expected_aud=expected_aud, expected_nonce=expected_nonce)
    except Exception:
        return "REJECT"
    kw = {"checkout_jwt": checkout_jwt}
    if expected_checkout_hash is not None:
        kw["expected_checkout_hash"] = expected_checkout_hash
    return "REJECT" if CheckoutMandateChain.parse(payloads).verify(**kw) else "PASS"


# ── payment builders ──────────────────────────────────────────────────────

def _payment_chain(open_cnf_key, close_holder_key, transaction_id="tx_1",
                   aud="merchant", nonce="merchant-nonce", constraints=None,
                   hash_mode="sd_hash"):
    user = _key("user-key-1")
    holder = MandateClient()
    open_tok = holder.create(
        payloads=[OpenPaymentMandate(constraints=constraints or [],
                                     cnf=make_cnf(open_cnf_key))],
        issuer_key=user,
    )
    pm = PaymentMandate(
        transaction_id=transaction_id, payee=Merchant(name="Shop", id="s-1"),
        payment_amount=Amount(amount=1000, currency="USD"),
        payment_instrument=PaymentInstrument(id="pi-1", type="credit"),
    )
    chain = holder.present(holder_key=close_holder_key, mandate_token=open_tok,
                           payloads=[pm], aud=aud, nonce=nonce, hash_mode=hash_mode)
    return chain, _pub(user)


def _payment_outcome(chain, root_pub, expected_transaction_id=None):
    holder = MandateClient()
    try:
        payloads = holder.verify(token=chain, key_or_provider=lambda _t: root_pub,
                                 expected_aud="merchant", expected_nonce="merchant-nonce")
    except Exception:
        return "REJECT"
    kw = {}
    if expected_transaction_id is not None:
        kw["expected_transaction_id"] = expected_transaction_id
    return "REJECT" if PaymentMandateChain.parse(payloads).verify(**kw) else "PASS"


# ── the semantic cases ────────────────────────────────────────────────────

def _happy_checkout():
    agent = _key("agent-key-1")
    h, chain, cj, up = _checkout_chain(agent, agent)
    return _checkout_outcome(chain, up, cj)


def _happy_payment():
    agent = _key("agent-key-1")
    chain, up = _payment_chain(agent, agent)
    return _payment_outcome(chain, up)


def _wrong_root_key():
    agent = _key("agent-key-1")
    h, chain, cj, up = _checkout_chain(agent, agent)
    return _checkout_outcome(chain, _pub(_key("attacker")), cj)  # wrong user pubkey


def _consent_forgery():
    # open binds agentA's key; agentB (attacker) closes it -> hop1 sig fails.
    agent_a, agent_b = _key("agent-a"), _key("attacker-b")
    h, chain, cj, up = _checkout_chain(agent_a, agent_b)
    return _checkout_outcome(chain, up, cj)


def _wrong_aud():
    agent = _key("agent-key-1")
    h, chain, cj, up = _checkout_chain(agent, agent, aud="merchant")
    return _checkout_outcome(chain, up, cj, expected_aud="attacker-aud")


def _wrong_nonce():
    agent = _key("agent-key-1")
    h, chain, cj, up = _checkout_chain(agent, agent, nonce="real-nonce")
    return _checkout_outcome(chain, up, cj, expected_nonce="replayed-nonce")


def _constraint_violation():
    # open restricts AllowedMerchants=[m-good]; checkout uses m-evil.
    agent = _key("agent-key-1")
    h, chain, cj, up = _checkout_chain(
        agent, agent, merchant_id="m-evil",
        constraints=[AllowedMerchants(allowed=[Merchant(id="m-good", name="Good")])])
    return _checkout_outcome(chain, up, cj)


def _checkout_hash_mismatch():
    agent = _key("agent-key-1")
    h, chain, cj, up = _checkout_chain(agent, agent)
    return _checkout_outcome(chain, up, cj, expected_checkout_hash="not-the-hash")


def _transaction_id_mismatch():
    agent = _key("agent-key-1")
    chain, up = _payment_chain(agent, agent, transaction_id="tx_actual")
    return _payment_outcome(chain, up, expected_transaction_id="tx_expected")


def _our_mint_interop():
    """TWO-WAY interop: a chain minted by OUR frozen-layer primitives (mint.py) is
    fully accepted by the REFERENCE verifier — including constraint evaluation and
    its Checkout schema validation. (Their issuer -> our verifier is covered by the
    committed goldens; this is the other direction.)
    """
    import pathlib
    import mint
    fx_path = (pathlib.Path(__file__).resolve().parents[1] / "selfcheck" / "fixtures"
               / "2026-04-08" / "ap2" / "checkout_ap2.valid.json")
    fx = json.loads(fx_path.read_text())
    wire = mint.mint_chain(fx)
    plat = JWK(**mint.platform_public_jwk())
    holder = MandateClient()
    try:
        payloads = holder.verify(token=wire, key_or_provider=lambda _t: plat,
                                 expected_aud="merchant", expected_nonce="merchant-nonce")
    except Exception:
        return "REJECT"
    violations = CheckoutMandateChain.parse(payloads).verify(
        checkout_jwt=mint.mint_checkout_jwt(fx))
    return "REJECT" if violations else "PASS"


# ── constraint evaluation on OPEN mandates (spec: closed values must satisfy
# the open constraints; "Any unknown Constraints MUST be treated as failing") ──

def _amount_range_violation():
    agent = _key("agent-key-1")
    chain, up = _payment_chain(agent, agent,
                               constraints=[AmountRange(max=500, currency="USD")])   # pm amount=1000
    return _payment_outcome(chain, up)


def _amount_range_within():
    agent = _key("agent-key-1")
    chain, up = _payment_chain(agent, agent,
                               constraints=[AmountRange(max=5000, currency="USD")])  # pm amount=1000
    return _payment_outcome(chain, up)


def _allowed_payees_violation():
    agent = _key("agent-key-1")
    chain, up = _payment_chain(
        agent, agent,
        constraints=[AllowedPayees(allowed=[Merchant(id="s-good", name="Good Shop")])])
    return _payment_outcome(chain, up)   # pm payee is s-1 -> not allowed


def _unknown_constraint():
    """An unrecognized constraint type in the OPEN mandate — minted with OUR
    primitives (the reference's typed builders can't emit one). MUST fail."""
    import json as _json
    import pathlib as _pathlib
    import mint
    fx = _json.loads((_pathlib.Path(__file__).resolve().parents[1] / "selfcheck" /
                      "fixtures" / "2026-04-08" / "ap2" /
                      "checkout_ap2.valid.json").read_text())
    wire = mint.mint_chain(fx, constraints=[{"type": "checkout.quantum_limit",
                                             "limit": 1}])
    plat = JWK(**mint.platform_public_jwk())
    holder = MandateClient()
    try:
        payloads = holder.verify(token=wire, key_or_provider=lambda _t: plat,
                                 expected_aud="merchant", expected_nonce="merchant-nonce")
        violations = CheckoutMandateChain.parse(payloads).verify(
            checkout_jwt=mint.mint_checkout_jwt(fx))
    except Exception:
        return "REJECT"
    return "REJECT" if violations else "PASS"


def _issuer_jwt_hash_mode():
    """The draft's alternative binding mode: the closed hop commits to the issuer
    JWT only (allowing disclosure redaction). A valid chain must still verify —
    and our frozen layer accepts the issuer_jwt_hash binding branch too."""
    import frozen
    agent = _key("agent-key-1")
    chain, up = _payment_chain(agent, agent, hash_mode="issuer_jwt_hash")
    if frozen.frozen_verify(chain)[0] is not True:
        return "REJECT"
    return _payment_outcome(chain, up)


def _missing_consent_lone_open():
    # a lone open mandate presented where a closed 2-hop authorization is required.
    user = _key("user-key-1")
    holder = MandateClient()
    open_tok = holder.create(
        payloads=[OpenCheckoutMandate(constraints=[], cnf=make_cnf(_key("agent")))],
        issuer_key=user,
    )
    # testbed policy: a completed authorization MUST be a >=2-hop chain.
    return "REJECT" if len(open_tok.split("~~")) < 2 else "PASS"


# id, req_ids, matrix_case, expect, run
CASES = [
    ("e2e.checkout_happy_path", ["PAY-035", "PAY-045"], "48", "PASS", _happy_checkout),
    ("e2e.payment_happy_path", ["PAY-038", "PAY-047"], "49", "PASS", _happy_payment),
    ("e2e.reject_wrong_root_key", ["PAY-040"], "20", "REJECT", _wrong_root_key),
    ("e2e.reject_consent_forgery", ["PAY-031"], "31", "REJECT", _consent_forgery),
    ("e2e.reject_wrong_aud", ["PAY-043"], "33", "REJECT", _wrong_aud),
    ("e2e.reject_replayed_nonce", ["PAY-043"], "34", "REJECT", _wrong_nonce),
    ("e2e.reject_constraint_violation", ["PAY-045"], "43/44", "REJECT", _constraint_violation),
    ("e2e.reject_checkout_hash_mismatch", ["PAY-035"], "7", "REJECT", _checkout_hash_mismatch),
    ("e2e.reject_transaction_id_mismatch", ["PAY-047"], "8/46", "REJECT", _transaction_id_mismatch),
    ("e2e.reject_missing_consent", ["PAY-035"], "32", "REJECT", _missing_consent_lone_open),
    ("e2e.our_mint_reference_interop", ["PAY-041", "PAY-042"], "37", "PASS", _our_mint_interop),
    ("e2e.reject_amount_range_violation", ["PAY-045"], "43", "REJECT", _amount_range_violation),
    ("e2e.amount_range_within", ["PAY-045"], "42", "PASS", _amount_range_within),
    ("e2e.reject_disallowed_payee", ["PAY-045"], "43", "REJECT", _allowed_payees_violation),
    ("e2e.reject_unknown_constraint", ["PAY-045"], "44", "REJECT", _unknown_constraint),
    ("e2e.issuer_jwt_hash_binding_mode", ["PAY-042"], "39", "PASS", _issuer_jwt_hash_mode),
]
