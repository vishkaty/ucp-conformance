#!/usr/bin/env python3
"""
validate_speclint.py — the gate that proves speclint's predicates are SOUND.

A speclint predicate is only trustworthy if it fires on a real, verified
contradiction AND stays silent on a consistent-but-different input. This gate
proves both directions for every predicate, the same discipline the merchant
suite's reference gate uses:

  POSITIVE CONTROL  run the predicate against the SHA-pinned vendored spec and
                    require it to reproduce the exact, independently hand-verified
                    finding set (no more, no less).
  CLASS NEGATIVE    run the predicate against a synthetic consistent pair
                    (fixtures/class_negatives/) and require ZERO findings, proving
                    it does not constant-FIRE over any two-sided input.

Run:  python3 conformance/speclint/validate_speclint.py
Exit 0 = every predicate is sound; 1 = a predicate is broken (blocks the lane).
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from parsers.openapi import required_headers_by_operation      # noqa: E402
from parsers.openrpc import required_meta_by_method            # noqa: E402
from predicates import transport_header_parity                 # noqa: E402
from predicates import signature_example_coverage              # noqa: E402
from parsers.signed_examples import signed_examples_in_tree    # noqa: E402

ROOT = HERE.parents[1]
VENDOR = ROOT / "conformance" / ".vendor" / "ucp" / "source" / "services" / "shopping"
VENDOR_0825 = (ROOT / "conformance" / ".vendor" / "ucp-2026-08-25" / "source"
               / "services" / "shopping")
FIX = HERE / "fixtures" / "class_negatives"
VENDOR_0825_DOCS = (ROOT / "conformance" / ".vendor" / "ucp-2026-08-25"
                    / "docs" / "specification")

# Independently hand-verified at the v2026-08-25 release pin (2026-09-01).
# The identity-resolution gate (overview/index.md) requires a signature to
# cover ucp-agent, signature-agent and idempotency-key WHEN PRESENT, and to
# be SKIPPED otherwise. Exactly two shipped examples carry a gate header
# their own Signature-Input omits, so a conformant verifier must skip the
# very requests the spec offers as models. Both are ucp-agent.
#   - shopping/checkout/rest.md : filed upstream 2026-09-01 (our PR)
#   - shopping/order/index.md   : already fixed by our open ucp#659
# This set IS the golden: an upstream fix OR a new violation turns the gate
# red rather than letting the claim go stale.
EXPECTED_SIGEX_0825 = {
    ("docs/specification/shopping/checkout/rest.md", "ucp-agent"),
    ("docs/specification/shopping/order/index.md", "ucp-agent"),
}

# Independently hand-verified at pin a2d8bf0b (and re-verified on current ucp
# main 63be476): REST requires Idempotency-Key on all writes; MCP meta requires
# it only on complete/cancel, so exactly these four create/update operations
# diverge.  This set IS the golden — a change here must be a deliberate re-pin.
EXPECTED_PARITY = {
    ("create_cart", "Idempotency-Key", "rest"),
    ("create_checkout", "Idempotency-Key", "rest"),
    ("update_cart", "Idempotency-Key", "rest"),
    ("update_checkout", "Idempotency-Key", "rest"),
}

# Re-attested at the v2026-08-25 release pin cd78fb38 (GAP-LEDGER-0825 G11,
# PLAN-0825 A.3, 2026-08-31 mechanical re-run): the #723 reorg + #736/#741
# refactor did NOT change this shape — REST still requires Idempotency-Key on
# the same four operations; MCP's base `meta` schema still requires only
# `ucp-agent`, and only complete_checkout/cancel_checkout carry the per-method
# `allOf` branch that adds `idempotency-key`. Identical to EXPECTED_PARITY by
# value (kept as its own named constant, not an alias, so a future edit to
# either pin's golden set is a deliberate, reviewed change to ONE of them, not
# an accidental shared mutation).
EXPECTED_PARITY_0825 = {
    ("create_cart", "Idempotency-Key", "rest"),
    ("create_checkout", "Idempotency-Key", "rest"),
    ("update_cart", "Idempotency-Key", "rest"),
    ("update_checkout", "Idempotency-Key", "rest"),
}


def _load(path):
    return json.loads(pathlib.Path(path).read_text())


def _transport_parity_findings(vendor_dir):
    rest = required_headers_by_operation(_load(vendor_dir / "rest.openapi.json"))
    mcp = required_meta_by_method(_load(vendor_dir / "mcp.openrpc.json"))
    return {(f.operation, f.header, f.required_in)
            for f in transport_header_parity(rest, mcp)}


def check_transport_parity():
    failures = []

    # POSITIVE CONTROL (2026-04-08) — vendored spec must reproduce the golden
    # finding set.
    got = _transport_parity_findings(VENDOR)
    if got != EXPECTED_PARITY:
        failures.append(
            "POSITIVE CONTROL mismatch on 2026-04-08 vendored spec:\n"
            f"    missing (expected, not found): {sorted(EXPECTED_PARITY - got)}\n"
            f"    unexpected (found, not expected): {sorted(got - EXPECTED_PARITY)}")

    # POSITIVE CONTROL (2026-08-25) — the re-attest lock (G11): proves the
    # mechanical finding at the release pin matches what was independently
    # verified, so an upstream fix OR a new divergence turns this gate red
    # instead of the claim silently going stale.
    got_0825 = _transport_parity_findings(VENDOR_0825)
    if got_0825 != EXPECTED_PARITY_0825:
        failures.append(
            "POSITIVE CONTROL mismatch on 2026-08-25 vendored spec (G11 re-attest):\n"
            f"    missing (expected, not found): {sorted(EXPECTED_PARITY_0825 - got_0825)}\n"
            f"    unexpected (found, not expected): {sorted(got_0825 - EXPECTED_PARITY_0825)}")

    # CLASS NEGATIVE — a consistent synthetic pair must yield zero findings.
    crest = required_headers_by_operation(_load(FIX / "parity_consistent_rest.openapi.json"))
    cmcp = required_meta_by_method(_load(FIX / "parity_consistent_mcp.openrpc.json"))
    neg = transport_header_parity(crest, cmcp)
    if neg:
        failures.append(f"CLASS NEGATIVE fired on a consistent pair: {neg}")

    return failures


def _sigex_findings(docs_dir):
    examples = signed_examples_in_tree(docs_dir)
    return {(f.source, f.component) for f in signature_example_coverage(examples)}


def check_signature_example_coverage():
    failures = []

    # POSITIVE CONTROL — the vendored release pin must reproduce the golden set.
    got = _sigex_findings(VENDOR_0825_DOCS)
    if got != EXPECTED_SIGEX_0825:
        failures.append(
            "POSITIVE CONTROL mismatch on 2026-08-25 vendored docs:\n"
            f"    missing (expected, not found): {sorted(EXPECTED_SIGEX_0825 - got)}\n"
            f"    unexpected (found, not expected): {sorted(got - EXPECTED_SIGEX_0825)}")

    # CLASS NEGATIVE — a conformant example must yield zero findings, proving the
    # predicate does not constant-FIRE on any signed example.
    neg = signature_example_coverage(signed_examples_in_tree(FIX / "sigex_conformant"))
    if neg:
        failures.append(f"CLASS NEGATIVE fired on a conformant example: {neg}")

    return failures


def main():
    failures = check_transport_parity() + check_signature_example_coverage()
    if failures:
        print("SPECLINT GATE: FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("SPECLINT GATE: PASS")
    print(f"  transport_header_parity @2026-04-08: {len(EXPECTED_PARITY)} golden "
          "findings reproduced")
    print(f"  transport_header_parity @2026-08-25: {len(EXPECTED_PARITY_0825)} golden "
          "findings reproduced (G11 re-attest)")
    print(f"  signature_example_coverage @2026-08-25: "
          f"{len(EXPECTED_SIGEX_0825)} golden findings reproduced")
    print("  class-negatives silent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
