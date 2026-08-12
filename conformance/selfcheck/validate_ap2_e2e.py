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

KNOWN-REFERENCE-DEFECT register (self-expiring, ci/known_ap2_reference_defects.json):
a semantic case may encode CORRECT behavior the pinned reference does not yet have
because WE found the bug and our fix PR is still open upstream (AP2#329/#330). Such
a case is registered with the upstream links + the exact buggy outcome; the runner
acknowledges that outcome loudly instead of failing, and the entry EXPIRES by
construction — a registered case that starts producing its correct outcome (e.g.
after a reference re-pin) is STALE and REDS the gate until the entry is deleted,
after which the case enforces the correct behavior permanently. Register hygiene
(links present, case known, observed != expect) is checked hermetically on every
run, even when the reference SDK is absent. `--selftest` kill-proves the
classifier + hygiene rules without the SDK or network.

Exit 0 = all cases behaved as specified; 1 = a case diverged.
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "testbed"))
sys.path.insert(0, str(HERE.parents[0] / "common"))
import crypto  # noqa: E402
import frozen  # noqa: E402
import mint  # noqa: E402
import nested  # noqa: E402
import provenance  # noqa: E402
import semantic  # noqa: E402

GOLD = HERE / "fixtures" / "ap2" / "golden"
NESTED = GOLD / "nested"
KNOWN = HERE.parents[0] / "ci" / "known_ap2_reference_defects.json"

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


# ── known-reference-defect register (self-expiring) ─────────────────────────

def load_known(path=KNOWN):
    try:
        return {d["case_id"]: d for d in json.loads(path.read_text())["defects"]}
    except FileNotFoundError:
        return {}


def register_hygiene(known, case_expect):
    """Return the list of problems that make an entry SUPPRESSION, not
    acknowledgement. `case_expect` maps every semantic case id -> its expected
    outcome. Hermetic: runs on every gate invocation, SDK installed or not."""
    problems = []
    for cid, d in known.items():
        for field in ("upstream", "fix_pr", "filed", "spec_must", "evidence",
                      "expect", "observed"):
            if not d.get(field):
                problems.append(f"{cid}: missing {field!r} — an entry without the "
                                "upstream report is suppression, not acknowledgement")
        if cid not in case_expect:
            problems.append(f"{cid}: names no existing semantic case — a register "
                            "entry must acknowledge a case that actually runs")
        elif d.get("expect") and d["expect"] != case_expect[cid]:
            problems.append(f"{cid}: register expect={d['expect']!r} disagrees with "
                            f"the case's expect={case_expect[cid]!r}")
        if d.get("observed") == d.get("expect"):
            problems.append(f"{cid}: observed == expect — the entry acknowledges "
                            "nothing and would mask a real regression")
    return problems


def classify_case(expect, got, entry):
    """One semantic case's verdict against the register.

    no entry:  got==expect -> "pass", else "fail"  (the normal enforcing path)
    entry:     got==expect -> "stale"  (the reference is FIXED: the entry has
                              expired; RED until it is deleted, then the case
                              enforces — the automatic flip)
               got==entry.observed -> "acknowledged"  (the filed bug still
                              reproduces; loud, not a failure)
               anything else -> "fail"  (new information, never silenced)
    """
    if entry is None:
        return "pass" if got == expect else "fail"
    if got == expect:
        return "stale"
    if got == entry.get("observed"):
        return "acknowledged"
    return "fail"


def semantic_tier(ok, known):
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
        verdict = classify_case(expect, got, known.get(cid))
        if verdict == "acknowledged":
            d = known[cid]
            print(f"  ! {cid} [{','.join(reqs)}] {expect} -> {got}: KNOWN pinned-"
                  f"reference defect, filed upstream")
            print(f"      {d['upstream']}  fix: {d['fix_pr']}  (filed {d['filed']})")
        elif verdict == "stale":
            ok &= check(f"  {cid}: register entry is STALE — the reference now "
                        f"produces the correct outcome ({got}). Delete it from "
                        f"{KNOWN.name}; the case then enforces.", False)
        else:
            # "expect PASS" = a valid flow the reference accepts; "expect REJECT" =
            # a violation the reference rejects. We assert the reference behaves as
            # the draft specifies; we do not grade the reference itself.
            ok &= check(f"  {cid} [{','.join(reqs)}] {expect} -> {got}",
                        verdict == "pass")
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


def payment_binding_tier(ok):
    """The OUR-LAYER correct behavior behind the filed AP2#329/#330 — always runs,
    our codec only, GREEN today: the platform-minted payment mandate PRESERVES
    type-specific instrument extension fields through mint->wire->parse, and the
    closed transaction_id binding is verified DEFAULT-CLOSED. Each positive
    carries its kill (a wire the detector must flag), so no check can pass by
    being unable to fail."""
    print("payment-binding tier (filed AP2#329/#330 correct behavior — our codec):")
    cj_a = mint.mint_checkout_jwt({"id": "co_A", "totals": [{"type": "total",
                                                            "amount": 199}]})
    cj_b = mint.mint_checkout_jwt({"id": "co_B", "totals": [{"type": "total",
                                                            "amount": 1000}]})
    x402 = {"id": "x402-usdc-1", "type": "x402",
            "payee_address": "0xAbCd0001",
            "facilitator": "https://facilitator.example"}
    wire = mint.mint_payment_chain(cj_a, payment_instrument=x402)
    ok &= check("  x402-instrument chain: frozen_verify OK",
                frozen.frozen_verify(wire)[0] is True)

    # #329: the signed instrument keeps its type-specific extension fields.
    inst = nested.closed_payment_instrument(wire)
    ok &= check("  #329 extension fields SURVIVE our mint->wire->parse "
                "(payee_address + facilitator)",
                isinstance(inst, dict)
                and inst.get("payee_address") == x402["payee_address"]
                and inst.get("facilitator") == x402["facilitator"])
    # kill: the detector must be able to report absence.
    plain = nested.closed_payment_instrument(mint.mint_payment_chain(cj_a))
    ok &= check("  #329 kill: extension-less chain -> detector reports ABSENT",
                isinstance(plain, dict) and "payee_address" not in plain)

    # #330: closed checkout-binding verified default-closed.
    ok &= check("  #330 correct binding -> ACCEPT",
                nested.verify_payment_checkout_binding(wire, cj_a)[0] is True)
    ok &= check("  #330 kill: mandate bound to a DIFFERENT checkout -> REJECT",
                nested.verify_payment_checkout_binding(
                    mint.mint_payment_chain(cj_b), cj_a)[0] is False)
    ok &= check("  #330 kill: transaction_id ABSENT -> REJECT (fail closed)",
                nested.verify_payment_checkout_binding(
                    mint.mint_payment_chain(cj_a, transaction_id=None),
                    cj_a)[0] is False)
    ok &= check("  #330 kill: transaction_id BLANK -> REJECT (fail closed)",
                nested.verify_payment_checkout_binding(
                    mint.mint_payment_chain(cj_a, transaction_id=""),
                    cj_a)[0] is False)
    return ok


def _selftest():
    """Hermetic kill-tests for the register classifier + hygiene (no SDK, no
    network): the acknowledged / stale / fail split, the automatic flip to
    enforcing on a fixed reference, and every hygiene rule that keeps an entry
    an acknowledgement rather than a suppression."""
    fails = []
    entry = {"case_id": "c", "expect": "REJECT", "observed": "PASS",
             "upstream": "https://u", "fix_pr": "https://p", "filed": "2026-08-10",
             "spec_must": "s", "evidence": "e"}

    if classify_case("REJECT", "PASS", entry) != "acknowledged":
        fails.append("reproducing filed defect must be acknowledged, not failed")
    if classify_case("REJECT", "REJECT", entry) != "stale":
        fails.append("a FIXED reference must expire the entry (the flip to enforcing)")
    if classify_case("REJECT", "ERR ValueError: boom", entry) != "fail":
        fails.append("an outcome matching neither expect nor observed is new "
                     "information and must fail")
    if classify_case("REJECT", "PASS", None) != "fail":
        fails.append("an UNREGISTERED divergence must fail (no silent laxity)")
    if classify_case("PASS", "PASS", None) != "pass":
        fails.append("a clean unregistered case must pass")

    case_expect = {"c": "REJECT"}
    if register_hygiene({"c": entry}, case_expect):
        fails.append("a complete, consistent entry must raise no hygiene problem")
    bad = dict(entry)
    bad.pop("upstream")
    if not register_hygiene({"c": bad}, case_expect):
        fails.append("an entry without the upstream link must be flagged")
    if not register_hygiene({"ghost": entry}, case_expect):
        fails.append("an entry naming no existing case must be flagged")
    if not register_hygiene({"c": {**entry, "observed": "REJECT"}}, case_expect):
        fails.append("observed==expect acknowledges nothing and must be flagged")
    if not register_hygiene({"c": {**entry, "expect": "PASS"}}, case_expect):
        fails.append("register/case expect disagreement must be flagged")

    # The COMMITTED register itself must be hygienic against the real case list.
    real = register_hygiene(load_known(),
                            {cid: exp for cid, _r, _m, exp, _f in semantic.CASES})
    for p in real:
        fails.append(f"committed register: {p}")

    if fails:
        print("ap2-defect-register selftest: FAIL")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("ap2-defect-register selftest: PASS — acknowledged/stale/fail are "
          "distinguished (a fixed reference flips the case to enforcing), every "
          "hygiene rule bites, and the committed register is hygienic.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="AP2 mandate E2E gate.")
    ap.add_argument("--selftest", action="store_true",
                    help="hermetic register-classifier + hygiene kill-tests")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    print(provenance.basis_banner())
    print()
    ok = True
    ok = frozen_tier(ok)
    ok = nested_tier(ok)
    ok = payment_binding_tier(ok)
    known = load_known()
    problems = register_hygiene(
        known, {cid: exp for cid, _r, _m, exp, _f in semantic.CASES})
    for p in problems:
        ok &= check(f"  register hygiene: {p}", False)
    ok = semantic_tier(ok, known)
    print("\nap2-e2e: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
