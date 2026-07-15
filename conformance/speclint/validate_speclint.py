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

ROOT = HERE.parents[1]
VENDOR = ROOT / "conformance" / ".vendor" / "ucp" / "source" / "services" / "shopping"
FIX = HERE / "fixtures" / "class_negatives"

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


def _load(path):
    return json.loads(pathlib.Path(path).read_text())


def check_transport_parity():
    failures = []

    # POSITIVE CONTROL — vendored spec must reproduce the golden finding set.
    rest = required_headers_by_operation(_load(VENDOR / "rest.openapi.json"))
    mcp = required_meta_by_method(_load(VENDOR / "mcp.openrpc.json"))
    got = {(f.operation, f.header, f.required_in)
           for f in transport_header_parity(rest, mcp)}
    if got != EXPECTED_PARITY:
        failures.append(
            "POSITIVE CONTROL mismatch on vendored spec:\n"
            f"    missing (expected, not found): {sorted(EXPECTED_PARITY - got)}\n"
            f"    unexpected (found, not expected): {sorted(got - EXPECTED_PARITY)}")

    # CLASS NEGATIVE — a consistent synthetic pair must yield zero findings.
    crest = required_headers_by_operation(_load(FIX / "parity_consistent_rest.openapi.json"))
    cmcp = required_meta_by_method(_load(FIX / "parity_consistent_mcp.openrpc.json"))
    neg = transport_header_parity(crest, cmcp)
    if neg:
        failures.append(f"CLASS NEGATIVE fired on a consistent pair: {neg}")

    return failures


def main():
    failures = check_transport_parity()
    if failures:
        print("SPECLINT GATE: FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("SPECLINT GATE: PASS")
    print(f"  transport_header_parity: {len(EXPECTED_PARITY)} golden findings "
          "reproduced; class-negative silent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
