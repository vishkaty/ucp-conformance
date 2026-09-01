#!/usr/bin/env python3
"""
test_r8_keys_location.py — kill-proof for the R8/R14/S8a signing-key-location fix.

GAP-LEDGER-0825.md's R8/R14/S8a rows document a real bug class: a UCP signing-key
CONSUMER hardcoded to one field location silently finds nothing when talking to a
counterparty that publishes at the OTHER location -- self.jwks stays empty and
signature verification quietly no-ops rather than failing loud. Our own golden-0825
independently reproduced the write-side half of this bug (R14, fixed); this file
proves the READ side (reference_agent.extract_signing_keys) is correct against a
REAL, frozen capture of an independently-shaped 08-25 counterparty -- not just our own
sandbox, which we control on both ends.

Fixture: conformance/agent/fixtures_08_25/golden_0825_discovery.json, a frozen capture
of golden-0825's actual GET /.well-known/ucp response (provenance in the fixture
itself). Confirmed by capture: this response publishes ONLY the top-level `keys[]`
array -- no `ucp.signing_keys` at all -- so a reader hardcoded to the nested 04-08
location finds NOTHING here. That is the exact defect this test kills.

Run:  python3 conformance/agent/test_r8_keys_location.py
Exit 0 = the current extract_signing_keys is proven correct AND the test proves it
would have caught the bug (the frozen pre-fix reproduction below returns empty on the
same fixture); 1 = a regression.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from reference_agent import extract_signing_keys   # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures_08_25", "golden_0825_discovery.json")


def _pre_fix_extract(body):
    """A frozen reproduction of the ORIGINAL (buggy) extraction logic this file
    replaced: `prof.get("signing_keys") or []`, reading ONLY the nested 04-08
    location. Kept here, never in production code, purely so this test can prove the
    fix actually changes the observable answer on a real 08-25 fixture (a check that
    can't fail proves nothing -- the P-1 discipline this whole suite runs on)."""
    prof = (body or {}).get("ucp") or {}
    return prof.get("signing_keys") or []


def main():
    doc = json.load(open(FIXTURE))
    golden_body = doc["discovery_response"]

    fails = []

    # 1. Sanity on the fixture itself: golden-0825 must actually publish ONLY at the
    #    top-level location (if this ever stops being true the fixture is stale and
    #    this test no longer proves what it claims to).
    if "keys" not in golden_body or not golden_body["keys"]:
        fails.append("fixture regressed: golden-0825 capture no longer has a top-level "
                      "keys[] -- re-capture from a live golden-0825 boot")
    if (golden_body.get("ucp") or {}).get("signing_keys"):
        fails.append("fixture regressed: golden-0825 capture now ALSO has ucp.signing_keys "
                      "-- this fixture is meant to prove the top-level-ONLY case; re-capture "
                      "or add a second fixture for the dual-publish case")

    # 2. THE KILL PROOF: the pre-fix (nested-only) reader finds NOTHING on this real
    #    08-25 response -- reproducing the S8a-class bug concretely, not hypothetically.
    pre_fix = _pre_fix_extract(golden_body)
    if pre_fix:
        fails.append(f"pre-fix reproduction unexpectedly found keys ({pre_fix!r}) -- this "
                      f"test can no longer prove the fix matters (mutation-path dead)")

    # 3. THE FIX: extract_signing_keys (current, shipped) finds the key at its
    #    canonical 08-25 top-level location.
    fixed = extract_signing_keys(golden_body)
    if not fixed:
        fails.append("extract_signing_keys found NO keys on golden-0825's real discovery "
                      "response -- the R8 fix is broken (regression)")
    elif not any(isinstance(k, dict) and k.get("kid") for k in fixed):
        fails.append(f"extract_signing_keys returned keys but none carry a kid: {fixed!r}")

    # 4. Regression guard: the fix must ALSO still find a 04-08-shaped (nested-only)
    #    key set -- reading both locations must not have broken the old one.
    old_shape = {"ucp": {"signing_keys": [{"kid": "old-shape-key", "kty": "EC"}]}}
    old_fixed = extract_signing_keys(old_shape)
    if not any(k.get("kid") == "old-shape-key" for k in old_fixed):
        fails.append(f"extract_signing_keys regressed on the 04-08 nested shape: {old_fixed!r}")

    # 5. Dedup guard: a business publishing the SAME kid at both locations (a
    #    migration-window dual-publish, like our own sandbox.py) must not double-count.
    dual_shape = {"keys": [{"kid": "dual-key", "kty": "EC"}],
                  "ucp": {"signing_keys": [{"kid": "dual-key", "kty": "EC"}]}}
    dual_fixed = extract_signing_keys(dual_shape)
    if len([k for k in dual_fixed if k.get("kid") == "dual-key"]) != 1:
        fails.append(f"extract_signing_keys did not dedupe a dual-published kid: {dual_fixed!r}")

    if fails:
        print("test-r8-keys-location: FAIL")
        for f in fails:
            print(f"  x {f}")
        return 1
    print("test-r8-keys-location: PASS — extract_signing_keys correctly reads the top-level "
          "keys[] location on a real, frozen golden-0825 capture where the nested-only "
          "pre-fix reader finds nothing (kill proof), and still reads the nested 04-08 "
          "shape + dedupes a dual-published kid (regression guards).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
