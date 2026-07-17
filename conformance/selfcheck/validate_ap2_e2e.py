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
import frozen  # noqa: E402
import semantic  # noqa: E402

GOLD = HERE / "fixtures" / "ap2" / "golden"


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
    if not semantic.AVAILABLE:
        print("semantic tier: SKIPPED (ap2 reference not installed)")
        return ok
    print("semantic tier (reference verifier):")
    for cid, reqs, mc, expect, run in semantic.CASES:
        try:
            got = run()
        except Exception as exc:
            got = f"ERR {type(exc).__name__}: {exc}"
        ok &= check(f"  {cid} [{','.join(reqs)}] expect {expect} -> {got}", got == expect)
    return ok


def main():
    ok = True
    ok = frozen_tier(ok)
    ok = semantic_tier(ok)
    print("\nap2-e2e: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
