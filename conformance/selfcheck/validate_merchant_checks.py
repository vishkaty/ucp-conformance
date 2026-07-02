#!/usr/bin/env python3
"""
validate_merchant_checks.py — the REFERENCE GATE for the merchant-agnostic suite.

The merchant runner (checks/merchant.py) trusts each MCheck to be *sound*: a
clean-pass means the server really satisfies the requirement, and a deviation is a
real defect — never an artifact of a mis-built check (e.g. asserting a response has
fulfillment when the request never asked for it).

This gate proves that soundness the only honest way: run every merchant check against
the KNOWN-GOOD reference server (Flower Shop, spec 2026-01-23) and require each one to
BOTH clean-pass AND be kill_safe (its mutations all caught). A check that deviates on
the reference is broken (a false-deviation generator); a check that isn't kill_safe
can false-PASS. Either fails this gate, so it can never reach a real merchant.

Run (reference server must be live on :8182):
    python3 conformance/selfcheck/validate_merchant_checks.py [--server http://localhost:8182]
Exit 0 = every merchant check is sound; 1 = a broken/weak check (blocks release).
"""
import sys, json, argparse, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))
import merchant_checks                                   # noqa: E402
from merchant import MerchantCtx, discover               # noqa: E402
from engine import CLEAN                                  # noqa: E402

# The reference server (Flower Shop) seeded data — the fixed, known-good target.
# Doubles as the canonical example of the --config schema a real merchant supplies.
def _pay(token, last):
    return {"payment": {"instruments": [{"id": "instr_" + last, "handler_id": "mock_payment_handler",
        "type": "card", "display": {"brand": "Visa", "last_digits": last},
        "credential": {"type": "token", "token": token},
        "billing_address": {"street_address": "123 Main St", "address_locality": "Anytown",
            "address_region": "CA", "address_country": "US", "postal_code": "12345"}}]},
        "risk_signals": {}}

REF_CONFIG = {
    "product_id": "bouquet_roses", "currency": "USD",
    "payment_handlers": [{"id": "google_pay", "name": "google.pay", "version": "2026-01-23",
        "spec": "https://example.com/spec", "config_schema": "https://example.com/schema",
        "instrument_schemas": ["https://example.com/is"], "config": {}}],
    "fulfillment_option_id": "std-ship",          # valid option to reach ready_for_complete
    "complete_payment": _pay("success_token", "1234"),   # happy-path completion
    "fail_payment": _pay("fail_token", "0000"),          # known-failing token -> 402
    "out_of_stock_id": "gardenias",               # seeded out-of-stock product -> 4xx
    "discount": {"valid_code": "10OFF", "second_valid_code": "WELCOME20",
                 "invalid_code": "INVALID_CODE"},  # seeded codes
}

# Our own controlled merchant fixture (spec 2026-04-08) — the golden for catalog/cart/
# checkout/order, capabilities the official samples don't implement (or don't implement
# at this spec version). See conformance/fixtures/merchant/.
CONTROLLED_CONFIG = {
    "product_id": "teapot_ceramic", "currency": "USD",
    "catalog": {"variant_id": "teapot_ceramic_v1"},
    "complete_payment": _pay("success_token", "1234"),   # fixture accepts any non-fail token
    "fail_payment": _pay("fail_token", "0000"),          # seeded failing token -> 402
    "out_of_stock_id": "trivet_cork",                    # seeded zero-stock product -> 4xx
    "discount": {"valid_code": "10OFF", "second_valid_code": "TEA5",
                 "invalid_code": "NOPE_NOT_A_CODE",      # seeded codes (see server.py)
                 "case_insensitive": True,               # fixture matches codes any-case
                 # scenario carts for the 01-23-scoped checks (DSC-010 / DSC-018):
                 "automatic": {"product_id": "teapot_ceramic", "quantity": 2},
                 "item": {"code": "MUGLOVE", "product_id": "mug_enamel", "quantity": 2}},
    "ap2": True,   # 01-23 mode emits ap2.merchant_authorization on checkout responses
}

GOLDENS = {"flower": REF_CONFIG, "controlled": CONTROLLED_CONFIG}

def main():
    ap = argparse.ArgumentParser(description="Reference gate for merchant checks.")
    ap.add_argument("--server", default="http://localhost:8182")
    ap.add_argument("--golden", choices=sorted(GOLDENS), default="flower",
                    help="which golden's config to use (flower=Flower Shop, controlled=our fixture)")
    args = ap.parse_args()
    profile, _ = discover(args.server)
    ctx = MerchantCtx(args.server, profile, GOLDENS[args.golden])
    results, detail = merchant_checks.run_merchant_checks(ctx)

    broken, weak, ok, skipped = [], [], [], []
    for chk, d in detail:
        st = d["status"]
        # any not-applicable/not-tested status (incl. suffixed reasons like
        # "not-applicable (no MCP transport)") is a legitimate skip on a golden
        # that lacks the capability/transport — not a broken check
        if isinstance(st, str) and st.startswith(("not-applicable", "not-tested")):
            skipped.append((chk.id, st)); continue
        if st != CLEAN:                       # deviation/inconclusive on a KNOWN-GOOD server → broken check
            broken.append((chk.id, st)); continue
        if not d.get("kill_safe"):            # clean but mutations survive → can false-PASS
            weak.append((chk.id, d.get("survivors"))); continue
        ok.append(chk.id)

    print(f"Reference gate — merchant checks vs {args.server}\n")
    for cid in ok:
        print(f"  ✓ {cid:32} sound (clean-pass + kill_safe)")
    for cid, st in skipped:
        print(f"  · {cid:32} skipped on reference ({st})")
    for cid, st in broken:
        print(f"  ✗ {cid:32} BROKEN — {st} on known-good server (false-deviation generator)")
    for cid, surv in weak:
        print(f"  ✗ {cid:32} WEAK — not kill_safe, survivors={surv} (can false-PASS)")

    n_run = len(ok) + len(broken) + len(weak)
    print(f"\n  {len(ok)}/{n_run} run checks sound · {len(skipped)} skipped (n/a on reference)")
    if broken or weak:
        print("  GATE FAILED — fix the check(s) above before they can grade a real merchant.")
        return 1
    print("  GATE PASSED — every runnable merchant check is sound on the reference server.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
