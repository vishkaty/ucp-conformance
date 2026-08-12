#!/usr/bin/env python3
"""
validate_ap2_bridge.py — the AP2↔UCP CROSS-PROTOCOL BRIDGE gate: the seam
neither org tests because each assumes the other. AP2's official suite pins its
own SDK; UCP's spec assumes the AP2 layer just works. This gate asserts the two
actually compose, in both directions, at the PINNED revisions:

  HERMETIC seam tier (always runs — our code + the vendored pinned spec):
    * bridge.ucp_schema_accepts_ap2_wire — the wire the AP2 reference SDK mints
      (the committed golden + our byte-convention mint) satisfies UCP's OWN
      ap2_mandate.json checkout_mandate pattern. RECONCILED expect: PASS;
      at the pins it REJECTS (ucp#599) → registered, self-expiring.
    * bridge.mandate_algs_defined_in_signature_table — every algorithm
      ap2-mandates.md permits for merchant_authorization is defined in
      signatures.md's JWK table. RECONCILED expect: PASS; at the pin ES512 is
      undefined (ucp#571) → registered, self-expiring.
    * bridge.ucp_merchant_accepts_platform_mandate — a UCP-checkout-derived
      mandate (our platform mint over the 04-08 AP2 fixture) is ACCEPTED by the
      conformant UCP merchant verifier (the same verifier the live fixture
      enforces with).
    * the ES256/JCS UCP-binding NEGATIVES: wrong-alg mAuth, non-JCS mAuth,
      mismatched checkout_hash — each REJECTED (structural/nested reuse; the
      mAuth alg-swap is bridge-local, the one surface the matrix rows don't
      carry).

  REFERENCE tier (when the pinned AP2 SDK is importable — CI installs it):
    * bridge.reference_mints_ucp_merchant_accepts — a mandate the AP2 reference
      SDK mints (its holder/keys) over a REAL UCP checkout_jwt (embedded
      merchant_authorization and all) is ACCEPTED by the UCP merchant verifier.
    * bridge.our_mint_accepted_by_reference — the other direction, reusing
      semantic._our_mint_interop (no duplicate logic).

Known-seam-defect register: conformance/ci/known_ucp_seam_defects.json — SAME
mechanism as P1-9's AP2 register (classifier + hygiene imported from
validate_ap2_e2e.py, not reimplemented): while the pinned spec carries the
contradiction the case's documented outcome is acknowledged LOUDLY; the moment
a re-pin produces the reconciled outcome the entry is STALE and REDS the gate
until deleted, after which the case enforces. --selftest kill-proves the
extractors and the expiry flip hermetically.

Exit 0 = bridge holds (with acknowledged seams); 1 = divergence/stale entry;
2 = environment skip (vendored UCP spec not fetched).
"""
import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "testbed"))
sys.path.insert(0, str(HERE.parents[0] / "common"))
import crypto  # noqa: E402
import merchant_verify  # noqa: E402
import mint  # noqa: E402
import nested  # noqa: E402
import provenance  # noqa: E402
import semantic  # noqa: E402
import structural  # noqa: E402
from validate_ap2_e2e import classify_case, load_known, register_hygiene  # noqa: E402

VENDOR_UCP = HERE.parents[0] / ".vendor" / "ucp"
SCHEMA = VENDOR_UCP / "source" / "schemas" / "shopping" / "ap2_mandate.json"
MANDATES_MD = VENDOR_UCP / "docs" / "specification" / "ap2-mandates.md"
SIGNATURES_MD = VENDOR_UCP / "docs" / "specification" / "signatures.md"
KNOWN = HERE.parents[0] / "ci" / "known_ucp_seam_defects.json"
GOLD = HERE / "fixtures" / "ap2" / "golden"
_FX = HERE / "fixtures" / "2026-04-08" / "ap2" / "checkout_ap2.valid.json"

_STRUCTURAL = {cid: (exp, fn) for cid, exp, fn in structural.CASES}


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    return bool(cond)


# ── spec extractors (fail LOUDLY on a reshaped spec — never silently green) ──

def checkout_mandate_pattern(schema_path=SCHEMA):
    """The pinned schema's checkout_mandate pattern, or None if unlocatable."""
    try:
        schema = json.loads(schema_path.read_text())
        pat = schema["$defs"]["checkout_mandate"]["pattern"]
        return pat if isinstance(pat, str) and pat else None
    except Exception:
        return None


def permitted_mandate_algs(md_path=MANDATES_MD):
    """Algs ap2-mandates.md permits ('**Algorithms:** ES256 (required), …')."""
    try:
        text = md_path.read_text()
    except Exception:
        return None
    m = re.search(r"\*\*Algorithms:\*\*([^\n]*)", text)
    if not m:
        return None
    algs = set(re.findall(r"\bES(?:256|384|512)\b", m.group(1)))
    return algs or None


def defined_signature_algs(md_path=SIGNATURES_MD):
    """Algs defined in signatures.md's JWK curve/alg table rows."""
    try:
        text = md_path.read_text()
    except Exception:
        return None
    algs = set(re.findall(r"\|\s*P-\d+\s*\|\s*`(ES\d+)`\s*\|", text))
    return algs or None


# ── hermetic seam cases ──────────────────────────────────────────────────────

def _wires():
    golden = json.loads((GOLD / "checkout_chain.json").read_text())["wire"]
    ours = mint.mint_chain(json.loads(_FX.read_text()))
    return golden, ours


def case_schema_accepts_wire(pattern=None):
    """RECONCILED behavior: UCP's own checkout_mandate pattern accepts the wire
    the AP2 reference SDK serializes (golden) AND our byte-convention mint."""
    pat = checkout_mandate_pattern() if pattern is None else pattern
    if pat is None:
        raise RuntimeError("checkout_mandate pattern not found in the pinned "
                           "schema — extractor must not silently pass")
    golden, ours = _wires()
    return "PASS" if (re.match(pat, golden) and re.match(pat, ours)) else "REJECT"


def case_algs_defined(permitted=None, defined=None):
    """RECONCILED behavior: every ap2-mandates.md-permitted alg has a defined
    key form in signatures.md's table."""
    p = permitted_mandate_algs() if permitted is None else permitted
    d = defined_signature_algs() if defined is None else defined
    if p is None or d is None:
        raise RuntimeError("alg extractors found no Algorithms line / JWK "
                           "table in the pinned spec — must not silently pass")
    return "PASS" if p <= d else "REJECT"


def case_merchant_accepts():
    """A UCP-checkout-derived mandate is accepted by the conformant merchant."""
    _, ours = _wires()
    _, plat_q = crypto.keypair(mint.PLATFORM_SEED)
    _, merch_q = crypto.keypair(mint.MERCHANT_SEED)
    err = merchant_verify.verify_checkout_mandate(ours, plat_q, merch_q)
    return "PASS" if err is None else "REJECT"


def case_wrong_alg_mauth():
    """UCP-binding negative: a merchant_authorization declaring ES384 (over
    ES256 signature bytes) must not verify — the binding pins ES256."""
    d_merch, merch_q = crypto.keypair(mint.MERCHANT_SEED)
    body = {"id": "co_alg", "totals": [{"type": "total", "amount": 100}]}
    hb = crypto.b64url(json.dumps({"alg": "ES384", "kid": "merchant_2026"},
                                  separators=(",", ":"), sort_keys=True).encode())
    pb = crypto.b64url(crypto.jcs_canonicalize(body))
    sig = crypto.ecdsa_p256_sign((hb + "." + pb).encode("ascii"), d_merch)
    mauth = hb + ".." + crypto.b64url(sig)
    return "REJECT" if not crypto.jws_detached_verify(mauth, body, merch_q) \
        else "PASS"


def _structural_leg(case_id):
    exp, fn = _STRUCTURAL[case_id]
    got = fn()
    return got if exp == "REJECT" else ("PASS" if got == exp else "REJECT")


def case_non_jcs_mauth():
    return _structural_leg("st.reject_non_jcs_mauth")


def case_hash_mismatch():
    """UCP-binding negative: checkout_hash naming a different checkout_jwt —
    the committed syntactically-valid golden, our nested verifier."""
    wire = json.loads((GOLD / "nested" / "nested_ucp.hash_mismatch.json")
                      .read_text())["wire"]
    _, merch_q = crypto.keypair(mint.MERCHANT_SEED)
    return "REJECT" if not nested.verify_ucp_nested(wire, merch_q)[0] else "PASS"


HERMETIC_CASES = [
    ("bridge.ucp_schema_accepts_ap2_wire", "PASS", case_schema_accepts_wire),
    ("bridge.mandate_algs_defined_in_signature_table", "PASS", case_algs_defined),
    ("bridge.ucp_merchant_accepts_platform_mandate", "PASS", case_merchant_accepts),
    ("bridge.reject_wrong_alg_mauth", "REJECT", case_wrong_alg_mauth),
    ("bridge.reject_non_jcs_mauth", "REJECT", case_non_jcs_mauth),
    ("bridge.reject_mismatched_checkout_hash", "REJECT", case_hash_mismatch),
]


# ── reference tier ───────────────────────────────────────────────────────────

def case_reference_mints_ucp_accepts():
    """AP2 → UCP: the reference SDK's holder mints (create/present, its own
    jwcrypto keys) over a REAL UCP checkout_jwt — merchant-signed, embedding a
    verifying merchant_authorization — and the UCP merchant verifier accepts,
    resolving the platform key from the reference user's public JWK."""
    from ap2.sdk.mandate import MandateClient
    from ap2.sdk.generated.checkout_mandate import CheckoutMandate
    from ap2.sdk.generated.open_checkout_mandate import OpenCheckoutMandate
    from ap2.tests.conftest import make_cnf
    import sdjwt

    user = semantic._key("bridge-user-1")
    agent = semantic._key("bridge-agent-1")
    fx = json.loads(_FX.read_text())
    checkout_jwt = mint.mint_checkout_jwt(fx)     # merchant-signed UCP checkout
    holder = MandateClient()
    open_tok = holder.create(
        payloads=[OpenCheckoutMandate(constraints=[], cnf=make_cnf(agent))],
        issuer_key=user)
    chain = holder.present(
        holder_key=agent, mandate_token=open_tok,
        payloads=[CheckoutMandate(
            checkout_jwt=checkout_jwt,
            checkout_hash=sdjwt.hash_ascii(checkout_jwt, "sha-256"))],
        aud="merchant", nonce="merchant-nonce")
    user_pub = json.loads(user.export_public())
    plat_q = crypto.pub_from_jwk(user_pub)
    _, merch_q = crypto.keypair(mint.MERCHANT_SEED)
    err = merchant_verify.verify_checkout_mandate(chain, plat_q, merch_q)
    return "PASS" if err is None else "REJECT"


def case_our_mint_reference_accepts():
    """UCP → AP2: reuse semantic's two-way interop case (no duplicate logic)."""
    return semantic._our_mint_interop()


REFERENCE_CASES = [
    ("bridge.reference_mints_ucp_merchant_accepts", "PASS",
     case_reference_mints_ucp_accepts),
    ("bridge.our_mint_accepted_by_reference", "PASS",
     case_our_mint_reference_accepts),
]


def _expect_map():
    return {cid: exp for cid, exp, _f in HERMETIC_CASES + REFERENCE_CASES}


def run_cases(ok, cases, known, label):
    print(label)
    for cid, expect, run in cases:
        try:
            got = run()
        except Exception as exc:
            got = f"ERR {type(exc).__name__}: {exc}"
        verdict = classify_case(expect, got, known.get(cid))
        if verdict == "acknowledged":
            d = known[cid]
            print(f"  ! {cid} {expect} -> {got}: KNOWN pinned-spec seam, "
                  f"filed upstream")
            print(f"      {d['upstream']}  (filed {d['filed']})")
        elif verdict == "stale":
            ok &= check(f"  {cid}: register entry is STALE — the pinned spec/"
                        f"SDK now produces the reconciled outcome ({got}). "
                        f"Delete it from {KNOWN.name}; the case then enforces.",
                        False)
        else:
            ok &= check(f"  {cid}: expect {expect} -> {got}", verdict == "pass")
    return ok


def _selftest():
    fails = []

    # extractor kills: a reconciled schema/table must flip the case to PASS
    # (which the classifier renders STALE -> the self-expiry actually fires).
    if case_schema_accepts_wire(pattern=r"^[A-Za-z0-9_~.-]+$") != "PASS":
        fails.append("a widened pattern accepting the AP2 wire must yield PASS")
    if classify_case("PASS", "PASS",
                     load_known(KNOWN).get("bridge.ucp_schema_accepts_ap2_wire")) \
            != "stale":
        fails.append("a reconciled schema must render the #599 entry STALE "
                     "(the expiry flip)")
    if case_algs_defined(permitted={"ES256", "ES384", "ES512"},
                         defined={"ES256", "ES384", "ES512"}) != "PASS":
        fails.append("a completed alg table must yield PASS")
    if case_algs_defined(permitted={"ES256", "ES512"},
                         defined={"ES256", "ES384"}) != "REJECT":
        fails.append("an undefined permitted alg must yield REJECT")

    # loudness: a reshaped/unlocatable spec must yield None from the extractor
    # (which the case turns into a RuntimeError), never a silent verdict.
    if checkout_mandate_pattern(pathlib.Path("/nonexistent")) is not None:
        fails.append("a missing schema must not yield a pattern")
    if permitted_mandate_algs(pathlib.Path("/nonexistent")) is not None:
        fails.append("a missing ap2-mandates.md must not yield algs")
    if defined_signature_algs(pathlib.Path("/nonexistent")) is not None:
        fails.append("a missing signatures.md must not yield algs")
    orig_fn = globals()["permitted_mandate_algs"]
    try:
        globals()["permitted_mandate_algs"] = lambda: None
        try:
            case_algs_defined()
            fails.append("a None extractor result must raise, not verdict")
        except RuntimeError:
            pass
    finally:
        globals()["permitted_mandate_algs"] = orig_fn

    # the real pinned spec still carries both seams (else entries are stale
    # and the MAIN run reds — this asserts selftest agrees with reality).
    if SCHEMA.exists():
        if case_schema_accepts_wire() != "REJECT":
            fails.append("pinned schema unexpectedly accepts the AP2 wire — "
                         "run the main gate: the #599 entry should be STALE")
        if case_algs_defined() != "REJECT":
            fails.append("pinned specs unexpectedly agree on algs — the #571 "
                         "entry should be STALE")

    # merchant-leg kill: a consent-forged mandate must be rejected.
    _, plat_q = crypto.keypair(mint.PLATFORM_SEED)
    _, merch_q = crypto.keypair(mint.MERCHANT_SEED)
    forged = mint.mint_chain(json.loads(_FX.read_text()),
                             close_seed=b"bridge-attacker")
    if merchant_verify.verify_checkout_mandate(forged, plat_q, merch_q) is None:
        fails.append("the merchant leg must reject a consent-forged mandate")

    # register hygiene of the COMMITTED seam register against bridge cases.
    for p in register_hygiene(load_known(KNOWN), _expect_map()):
        fails.append(f"committed seam register: {p}")

    if fails:
        print("ap2-bridge selftest: FAIL")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("ap2-bridge selftest: PASS — reconciled-spec expiry flips, extractor "
          "loudness, merchant-leg kill and seam-register hygiene all hold.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="AP2<->UCP cross-protocol bridge gate.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if not SCHEMA.exists() or not MANDATES_MD.exists() or not SIGNATURES_MD.exists():
        print("ap2-bridge: SKIP (vendored UCP spec not fetched — run "
              "conformance/ci/fetch_sources.sh)")
        return 2
    if args.selftest:
        return _selftest()

    print(provenance.basis_banner())
    print()
    ok = True
    known = load_known(KNOWN)
    for p in register_hygiene(known, _expect_map()):
        ok &= check(f"seam-register hygiene: {p}", False)
    ok = run_cases(ok, HERMETIC_CASES, known,
                   "hermetic seam tier (our code + the vendored pinned spec):")
    if semantic.AVAILABLE:
        ok = run_cases(ok, REFERENCE_CASES, known,
                       f"reference tier — two-way mint/verify vs "
                       f"{provenance.REFERENCE} @ {provenance.REFERENCE_SHA[:10]}:")
    else:
        print("reference tier: SKIPPED (reference SDK not installed) — the "
              "hermetic seam tier above ran in full")
    print("\nap2-bridge: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
