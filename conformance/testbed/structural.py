#!/usr/bin/env python3
"""
structural.py — the HERMETIC (frozen-independent) executable cases of the AP2
mandate matrix (ops/ap2-e2e-testbed-design-2026-07-16.md): the structural/schema
shapes (group A), issuer-signature and key-binding negatives (C/D), freshness
(E), the chain-form conventions (F41), payment↔checkout binding (H46) and the
receipt binding (H47) — all built from OUR OWN frozen-standard primitives
(RFC 9901 / RFC 8785 / RFC 7515 via common/crypto + common/sdjwt), minted by
mint.py knobs (data, not per-case code), so every case runs everywhere,
including CI runners where the reference SDK failed to install (the soft-install
weakness P1-9 flagged: the hermetic tier must never hinge on it).

Two verifier surfaces:

  * `structural_verify(wire, kind)` — the SHAPE oracle: chain form (no attached
    KB-JWT — the AP2 `~~` convention closes with a KB HOP), the open mandate's
    vct/cnf/constraints, the closed mandate's vct, checkout_hash presence,
    ISO-4217 currency + integer minor-unit amounts, and no terminal-hop cnf.
    Layered ON TOP of frozen.frozen_verify; merchant_verify is deliberately NOT
    changed (the live fixture keeps its byte-identical behavior).
  * signature/freshness cases go through merchant_verify.verify_checkout_mandate
    (the same verifier the enforce gate proves live) with fixture keys.

Receipt binding (H47): the pinned reference's ReceiptClient documents
`reference` as "the hash of the closed mandate this receipt is binding to"
(sdk/receipt_wrapper.py) but leaves the value CALLER-SUPPLIED and never derives
it — so the derivation below (reference == sd_hash of the terminal closed hop,
the same hash the chain itself binds hops with) is OUR convention, cross-checked
against the reference's own sd_hash math, not a reference-pinned byte contract.
That softness is documented in the matrix (case 47 caveat).

Outcome convention (same as semantic.py): each case's run() returns "PASS" or
"REJECT"; the runner compares to `expect`.
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))
sys.path.insert(0, str(HERE))
import crypto  # noqa: E402
import frozen  # noqa: E402
import merchant_verify  # noqa: E402
import mint  # noqa: E402
import nested  # noqa: E402
import sdjwt  # noqa: E402

_FX = (HERE.parents[0] / "selfcheck" / "fixtures" / "2026-04-08" / "ap2"
       / "checkout_ap2.valid.json")
_CURRENCY = re.compile(r"^[A-Z]{3}$")   # ISO-4217 alpha code shape


def _fixture():
    return json.loads(_FX.read_text())


def _keys():
    _, plat_q = crypto.keypair(mint.PLATFORM_SEED)
    _, merch_q = crypto.keypair(mint.MERCHANT_SEED)
    return plat_q, merch_q


def _disclosed_values(hop):
    """Every dict value disclosed by a hop (the mandate objects live here)."""
    vals = []
    for disc in hop.disclosures:
        try:
            arr = sdjwt.decode_disclosure(disc)
        except Exception:
            continue
        if isinstance(arr[-1], dict):
            vals.append(arr[-1])
    return vals


def _mandate_value(hop, vct_prefix):
    """The first disclosed value whose vct starts with `vct_prefix` (or, if none
    carries a vct at all, the first disclosed dict — a missing vct is then the
    caller's finding, not silently skipped)."""
    vals = _disclosed_values(hop)
    for v in vals:
        if isinstance(v.get("vct"), str) and v["vct"].startswith(vct_prefix):
            return v
    return vals[0] if vals else None


def structural_verify(wire, kind="checkout"):
    """Return (ok: bool, reason: str) for the STRUCTURAL matrix properties."""
    accepted, reason = frozen.frozen_verify(wire)
    if not accepted:
        return False, f"frozen: {reason}"
    hops = sdjwt.parse_chain(wire)
    if len(hops) < 2:
        return False, "chain: a completed authorization needs an open AND a closed hop"

    # F41 — the AP2 chain closes with a KB HOP joined by `~~`; an ATTACHED
    # compact KB-JWT (RFC 9901's single-credential presentation form) is not
    # the delegate-chain wire and must be rejected here.
    for i, h in enumerate(hops):
        if h.kb_jwt is not None:
            return False, f"chain: hop{i} carries an attached KB-JWT (not the `~~` form)"

    # A3/A5/A6 — the OPEN mandate: vct literal, cnf.jwk present and a real key.
    open_val = _mandate_value(hops[0], "mandate.")
    if not isinstance(open_val, dict):
        return False, "open: hop0 discloses no mandate object"
    if open_val.get("vct") != f"mandate.{kind}.open.1":
        return False, f"open: vct != mandate.{kind}.open.1"
    if not isinstance(open_val.get("constraints"), list):
        return False, "open: constraints must be a list"
    jwk = (open_val.get("cnf") or {}).get("jwk")
    if not isinstance(jwk, dict):
        return False, "open: missing cnf.jwk (no key to bind the closing agent)"
    try:
        q = crypto.pub_from_jwk(jwk)
    except Exception:
        return False, "open: cnf.jwk is not a parseable EC JWK"
    if not crypto.ec_on_curve(q):
        return False, "open: cnf.jwk point is not on P-256"

    # A5/A4/A29 — the CLOSED mandate: vct literal, checkout binding, no cnf.
    closed_val = _mandate_value(hops[-1], "mandate.")
    if not isinstance(closed_val, dict):
        return False, "closed: terminal hop discloses no mandate object"
    if closed_val.get("vct") != f"mandate.{kind}.1":
        return False, f"closed: vct != mandate.{kind}.1"
    if "cnf" in closed_val:
        return False, "closed: a TERMINAL hop must not bind a further cnf key"
    if kind == "checkout":
        if not isinstance(closed_val.get("checkout_hash"), str) \
                or not closed_val["checkout_hash"]:
            return False, "closed: missing checkout_hash (required binding field)"
    if kind == "payment":
        amt = closed_val.get("payment_amount")
        if not isinstance(amt, dict):
            return False, "closed: missing payment_amount"
        if not isinstance(amt.get("currency"), str) \
                or not _CURRENCY.match(amt["currency"]):
            return False, "closed: payment_amount.currency is not ISO-4217 alpha"
        if not isinstance(amt.get("amount"), int) or isinstance(amt.get("amount"), bool):
            return False, "closed: payment_amount.amount must be an integer minor-unit"
    return True, "ok"


# ── receipt binding (H47) — our convention, see module docstring ─────────────

def mandate_receipt_reference(wire):
    """The reference value a receipt MUST carry for this chain: the sd_hash of
    the terminal (closed) hop — the same digest primitive the chain itself uses
    to bind hops (RFC 9901 sd_hash, cross-checked against the reference SDK's
    compute_sd_hash in validate_sdjwt_vs_reference)."""
    hops = sdjwt.parse_chain(wire)
    return hops[-1].sd_hash()


def receipt_binds_mandate(receipt_reference, wire):
    """(ok, reason): the receipt's `reference` names THIS chain's closed mandate."""
    if not isinstance(receipt_reference, str) or not receipt_reference:
        return False, "receipt: reference absent or blank (fail closed)"
    if receipt_reference != mandate_receipt_reference(wire):
        return False, "receipt: reference names a DIFFERENT closed mandate"
    return True, "ok"


# ── case runners ─────────────────────────────────────────────────────────────

def _outcome_structural(wire, kind="checkout"):
    return "PASS" if structural_verify(wire, kind)[0] else "REJECT"


def _outcome_merchant(wire):
    plat_q, merch_q = _keys()
    return "PASS" if merchant_verify.verify_checkout_mandate(
        wire, plat_q, merch_q) is None else "REJECT"


def _full(wire, kind="checkout"):
    """Structural AND merchant-side verification both green."""
    if _outcome_structural(wire, kind) == "REJECT":
        return "REJECT"
    return _outcome_merchant(wire) if kind == "checkout" else "PASS"


def _checkout_valid():
    return _full(mint.mint_chain(_fixture()))


def _payment_valid():
    cj = mint.mint_checkout_jwt(_fixture())
    wire = mint.mint_payment_chain(cj)
    if _outcome_structural(wire, "payment") == "REJECT":
        return "REJECT"
    return "PASS" if nested.verify_payment_checkout_binding(wire, cj)[0] else "REJECT"


def _open_wellformed():
    # the open mandate's cnf key must be a REAL on-curve key (A3), asserted
    # directly (structural_verify checks it; this case isolates the property).
    hops = sdjwt.parse_chain(mint.mint_chain(_fixture()))
    val = _mandate_value(hops[0], "mandate.")
    try:
        q = crypto.pub_from_jwk(val["cnf"]["jwk"])
    except Exception:
        return "REJECT"
    return "PASS" if crypto.ec_on_curve(q) and isinstance(val.get("constraints"),
                                                          list) else "REJECT"


def _missing_checkout_hash():
    return _outcome_structural(mint.mint_chain(_fixture(), omit_checkout_hash=True))


def _wrong_vct():
    return _outcome_structural(mint.mint_chain(_fixture(),
                                               closed_vct="mandate.checkout.2"))


def _open_missing_cnf():
    return _outcome_structural(mint.mint_chain(_fixture(), open_cnf=False))


def _bad_currency():
    cj = mint.mint_checkout_jwt(_fixture())
    wire = mint.mint_payment_chain(
        cj, payment_amount={"amount": 1000, "currency": "usd"})
    return _outcome_structural(wire, "payment")


def _bad_merchant_sig():
    return _outcome_merchant(mint.mint_chain(_fixture(),
                                             checkout_signer_seed=b"not-the-merchant"))


def _wrong_platform_key():
    wire = mint.mint_chain(_fixture())
    wrong_q = crypto.keypair(b"attacker-platform")[1]
    _, merch_q = _keys()
    return "PASS" if merchant_verify.verify_checkout_mandate(
        wire, wrong_q, merch_q) is None else "REJECT"


def _alg_none_root():
    return _outcome_merchant(mint.mint_chain(_fixture(), hop0_unsigned=True))


def _alg_swap():
    # declared ES384 over ES256 signature bytes: the UCP binding pins ES256 and
    # a declared/actual mismatch must never verify.
    return _outcome_merchant(mint.mint_chain(_fixture(), hop1_alg="ES384"))


def _non_jcs_mauth():
    """The UCP-binding JCS negative (case 23): a merchant_authorization signed
    over a NON-canonical serialization of the checkout body. The verifier
    recomputes JCS(body minus ap2), so the signature must not verify."""
    d_merch, _ = crypto.keypair(mint.MERCHANT_SEED)
    body = {"id": "co_jcs", "totals": [{"type": "total", "amount": 500}]}
    # sign over a NON-JCS byte serialization (spaces, unsorted insertion order)
    hdr = {"alg": "ES256", "kid": "merchant_2026"}
    hb = crypto.b64url(json.dumps(hdr, separators=(",", ":"), sort_keys=True).encode())
    pb = crypto.b64url(json.dumps({"totals": body["totals"], "id": body["id"]},
                                  indent=1).encode())
    sig = crypto.ecdsa_p256_sign((hb + "." + pb).encode("ascii"), d_merch)
    bad_mauth = hb + ".." + crypto.b64url(sig)
    checkout = dict(body, ap2={"merchant_authorization": bad_mauth})
    return _outcome_merchant(mint.mint_chain(checkout))


def _wrong_agent_key():
    return _outcome_merchant(mint.mint_chain(_fixture(), close_seed=b"attacker-agent"))


def _alg_none_kb():
    return _outcome_merchant(mint.mint_chain(_fixture(), hop1_unsigned=True))


def _wrong_kb_typ():
    return _outcome_merchant(mint.mint_chain(_fixture(), hop1_typ="JWT"))


def _terminal_cnf():
    extra = {"cnf": {"jwk": mint.platform_public_jwk()}}
    return _outcome_structural(mint.mint_chain(_fixture(), closed_extra=extra))


def _lone_open():
    wire = mint.mint_chain(_fixture())
    lone = sdjwt.split_chain(wire)[0]      # the open hop alone, restored form
    s = _outcome_structural(lone)
    m = _outcome_merchant(lone)
    return "REJECT" if (s == "REJECT" and m == "REJECT") else "PASS"


def _future_iat():
    import time
    return _outcome_merchant(mint.mint_chain(_fixture(),
                                             iat=int(time.time()) + 3600))


def _expired():
    import time
    return _outcome_merchant(mint.mint_chain(_fixture(),
                                             exp=int(time.time()) - 3600))


def _future_nbf():
    import time
    return _outcome_merchant(mint.mint_chain(_fixture(),
                                             nbf=int(time.time()) + 3600))


def _attached_kb():
    wire = mint.mint_chain(_fixture())
    # transform the terminal hop into the attached-KB presentation form:
    # strip its trailing '~' and append a (well-formed) compact KB-JWT.
    d_agent, _ = crypto.keypair(mint.AGENT_SEED)
    kb = crypto.jws_compact_sign({"alg": "ES256", "typ": "kb+jwt"},
                                 json.dumps({"aud": "merchant"}).encode(), d_agent)
    assert wire.endswith("~")
    return _outcome_structural(wire + kb)


def _payment_wrong_checkout():
    cj_a = mint.mint_checkout_jwt({"id": "co_A", "totals": []})
    cj_b = mint.mint_checkout_jwt({"id": "co_B", "totals": []})
    wire = mint.mint_payment_chain(cj_b)
    return "REJECT" if not nested.verify_payment_checkout_binding(
        wire, cj_a)[0] else "PASS"


def _receipt_binds():
    wire = mint.mint_chain(_fixture())
    ok, _ = receipt_binds_mandate(mandate_receipt_reference(wire), wire)
    return "PASS" if ok else "REJECT"


def _receipt_mismatch():
    wire = mint.mint_chain(_fixture())
    other = mint.mint_chain(_fixture(), aud="other-merchant")
    ok, _ = receipt_binds_mandate(mandate_receipt_reference(other), wire)
    return "PASS" if ok else "REJECT"


# id, expect, run — hermetic, our primitives only (no reference SDK anywhere).
CASES = [
    ("st.checkout_valid_accepts", "PASS", _checkout_valid),
    ("st.payment_valid_accepts", "PASS", _payment_valid),
    ("st.open_wellformed", "PASS", _open_wellformed),
    ("st.reject_missing_checkout_hash", "REJECT", _missing_checkout_hash),
    ("st.reject_wrong_vct", "REJECT", _wrong_vct),
    ("st.reject_open_missing_cnf", "REJECT", _open_missing_cnf),
    ("st.reject_bad_currency", "REJECT", _bad_currency),
    ("st.reject_bad_merchant_sig", "REJECT", _bad_merchant_sig),
    ("st.reject_wrong_platform_key", "REJECT", _wrong_platform_key),
    ("st.reject_alg_none_root", "REJECT", _alg_none_root),
    ("st.reject_alg_swap", "REJECT", _alg_swap),
    ("st.reject_non_jcs_mauth", "REJECT", _non_jcs_mauth),
    ("st.reject_wrong_agent_key", "REJECT", _wrong_agent_key),
    ("st.reject_alg_none_kb", "REJECT", _alg_none_kb),
    ("st.reject_wrong_kb_typ", "REJECT", _wrong_kb_typ),
    ("st.reject_terminal_cnf", "REJECT", _terminal_cnf),
    ("st.reject_lone_open", "REJECT", _lone_open),
    ("st.reject_future_iat", "REJECT", _future_iat),
    ("st.reject_expired", "REJECT", _expired),
    ("st.reject_future_nbf", "REJECT", _future_nbf),
    ("st.reject_attached_kb", "REJECT", _attached_kb),
    ("st.reject_payment_wrong_checkout", "REJECT", _payment_wrong_checkout),
    ("st.receipt_reference_binds", "PASS", _receipt_binds),
    ("st.reject_receipt_reference_mismatch", "REJECT", _receipt_mismatch),
]
