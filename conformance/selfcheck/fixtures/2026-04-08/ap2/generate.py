#!/usr/bin/env python3
"""Generate the AP2 mandate fixtures with REAL signatures.

The valid fixture's ap2.merchant_authorization is a detached JWS over the JCS
canonicalization of the checkout body (excluding the ap2 field), signed with the
fixed test merchant key — so "valid" means cryptographically valid, not merely
shaped right. Re-run after changing the checkout body; the ap2-crypto/AP2 check
gates re-verify the signatures on every CI run.

  python3 conformance/selfcheck/fixtures/2026-04-08/ap2/generate.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "common"))
import crypto  # noqa: E402

# The fixture merchant's signing key — the AP2 checks resolve the same seed.
MERCHANT_SEED = b"ap2-merchant-fixture"
KID = "merchant-2026"
HERE = pathlib.Path(__file__).resolve().parent

# Base the AP2 fixture on the schema-valid checkout fixture so it satisfies the
# FULL pinned checkout schema (ucp, links included) — the AP2 reference SDK's
# Checkout model rejects a links-less checkout, which caught the earlier minimal
# hand-rolled body. Only the id is overridden.
_BASE = HERE.parents[0] / "checkout_response.valid.json"


def checkout_body():
    body = json.loads(_BASE.read_text())
    body["id"] = "chk_ap2_fixture"
    return body


def main():
    d, _ = crypto.keypair(MERCHANT_SEED)
    checkout = checkout_body()
    auth = crypto.jws_detached_sign({"alg": "ES256"}, checkout, d, kid=KID)
    fixture = {**checkout, "ap2": {"merchant_authorization": auth}}
    (HERE / "checkout_ap2.valid.json").write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote checkout_ap2.valid.json (auth len {len(auth)})")


if __name__ == "__main__":
    main()
