#!/usr/bin/env python3
"""
validate_ap2_e2e.py — the AP2 mandate END-TO-END conformance gate (user/agent/
merchant delegate chains). Two tiers, mirroring the Hybrid-(C) split:

  FROZEN tier (always runs, our codec only):
    * each committed golden chain frozen_verify()s OK, and
    * every frozen mutant (tampered/orphan disclosure, corrupt sd_hash, broken
      `~~`) is REJECTed — kill-safe: the check cannot false-pass.

  SEMANTIC tier (runs when the pinned reference SDK is installed):
    * a valid checkout/payment flow is ACCEPTED, and
    * each violation (wrong root key, consent forgery, wrong aud, replayed nonce,
      constraint violation, checkout_hash / transaction_id mismatch, missing
      consent) is REJECTed by the reference verifier.

This is the executable form of the 49-case matrix (ops/ap2-e2e-testbed-design);
groups A–H are covered here, with more rows added in later batches.

Exit 0 = all cases behaved as specified; 1 = a case diverged.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "testbed"))
sys.path.insert(0, str(HERE.parents[0] / "common"))
import crypto  # noqa: E402
import frozen  # noqa: E402
import nested  # noqa: E402
import provenance  # noqa: E402
import semantic  # noqa: E402

GOLD = HERE / "fixtures" / "ap2" / "golden"
NESTED = GOLD / "nested"

# Same deterministic merchant key the 04-08 AP2 fixture signs with.
_MERCHANT_SEED = b"ap2-merchant-fixture"


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    return bool(cond)


def frozen_tier(ok):
    print("frozen tier (our codec — always runs):")
    for gf in sorted(GOLD.glob("*.json")):
        wire = json.loads(gf.read_text())["wire"]
        accepted, reason = frozen.frozen_verify(wire)
        ok &= check(f"  golden {gf.stem}: frozen_verify OK", accepted)
        for mname, mut in frozen.FROZEN_MUTANTS.items():
            try:
                rejected = frozen.frozen_verify(mut(wire))[0] is False
            except Exception:
                rejected = True  # a mutation that fails to even parse is a reject
            ok &= check(f"  {gf.stem} / {mname}: REJECTed", rejected)
    return ok


def semantic_tier(ok):
    # Moving layer: INTEROP OBSERVATIONS against the pinned reference (a fixture),
    # never conformance verdicts on anyone's implementation.
    if not semantic.AVAILABLE:
        print("semantic tier (interop vs reference): SKIPPED (reference not installed)")
        return ok
    print(f"semantic tier — interop observations vs {provenance.REFERENCE} "
          f"@ {provenance.REFERENCE_SHA[:10]}:")
    for cid, reqs, mc, expect, run in semantic.CASES:
        try:
            got = run()
        except Exception as exc:
            got = f"ERR {type(exc).__name__}: {exc}"
        # "expect PASS" = a valid flow the reference accepts; "expect REJECT" = a
        # violation the reference rejects. We assert the reference behaves as the
        # draft specifies; we do not grade the reference itself.
        ok &= check(f"  {cid} [{','.join(reqs)}] {expect} -> {got}", got == expect)
    return ok


def nested_tier(ok):
    """UCP nested-binding layer (PAY-042 / spec L207-209, L395-408) — our crypto only.

    The negatives are generator-minted VALID chains whose UCP nesting is broken, so
    they pass the generic frozen layer and only this verifier can catch them —
    that is the kill-safety for the nested-binding check specifically.
    """
    print("nested-binding tier (UCP layer, our codec — always runs):")
    _, merchant_q = crypto.keypair(_MERCHANT_SEED)
    cases = [
        ("valid", True),            # full nesting holds -> ACCEPT
        ("missing_mauth", False),   # embedded checkout lacks merchant_authorization (PAY-042)
        ("tampered_terms", False),  # terms edited after the business signed
        ("hash_mismatch", False),   # checkout_hash names a different checkout_jwt
    ]
    for name, expect_ok in cases:
        path = NESTED / f"nested_ucp.{name}.json"
        if not path.exists():
            ok &= check(f"  nested {name}: MISSING GOLDEN {path.name}", False)
            continue
        wire = json.loads(path.read_text())["wire"]
        got, reason = nested.verify_ucp_nested(wire, merchant_q)
        want = "ACCEPT" if expect_ok else "REJECT"
        ok &= check(f"  nested {name}: expect {want} -> {reason}", got is expect_ok)
    return ok


def main():
    print(provenance.basis_banner())
    print()
    ok = True
    ok = frozen_tier(ok)
    ok = nested_tier(ok)
    ok = semantic_tier(ok)
    print("\nap2-e2e: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
