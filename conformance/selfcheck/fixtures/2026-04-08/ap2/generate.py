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

CHECKOUT = {
    "id": "chk_ap2_fixture",
    "status": "ready_for_complete",
    "currency": "USD",
    "line_items": [{
        "id": "li_1",
        "item": {"id": "bouquet_roses", "title": "Red Rose", "price": 3500},
        "quantity": 1,
        "totals": [{"type": "subtotal", "amount": 3500},
                   {"type": "total", "amount": 3500}],
    }],
    "totals": [{"type": "subtotal", "amount": 3500},
               {"type": "total", "amount": 3500}],
}


def main():
    d, _ = crypto.keypair(MERCHANT_SEED)
    auth = crypto.jws_detached_sign({"alg": "ES256"}, CHECKOUT, d, kid=KID)
    fixture = {**CHECKOUT, "ap2": {"merchant_authorization": auth}}
    (HERE / "checkout_ap2.valid.json").write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote checkout_ap2.valid.json (auth len {len(auth)})")


if __name__ == "__main__":
    main()
