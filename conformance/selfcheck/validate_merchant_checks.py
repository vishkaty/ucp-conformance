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
    "catalog": {"variant_id": "teapot_ceramic_v1",
                "max_batch": 25,                         # fixture's MAX_LOOKUP_BATCH
                "configurable_product_id": "teacup_glaze",  # option axes Color x Size
                "paginated_query": "*",                  # matches the whole seed catalog
                "paginated_total": 13},                  # len(server.PRODUCTS)
    "complete_payment": _pay("success_token", "1234"),   # fixture accepts any non-fail token
    "fail_payment": _pay("fail_token", "0000"),          # seeded failing token -> 402
    "out_of_stock_id": "trivet_cork",                    # seeded zero-stock product -> 4xx
    "discount": {"valid_code": "10OFF", "second_valid_code": "TEA5",
                 "invalid_code": "NOPE_NOT_A_CODE",      # seeded codes (see server.py)
                 "case_insensitive": True,               # fixture matches codes any-case
                 "rejected_messages": True,              # fixture emits rejection warnings
                 # scenario carts for the 01-23-scoped checks (DSC-010 / DSC-018):
                 "automatic": {"product_id": "teapot_ceramic", "quantity": 2},
                 "item": {"code": "MUGLOVE", "product_id": "mug_enamel", "quantity": 2}},
    "ap2": True,   # 01-23 mode emits ap2.merchant_authorization on checkout responses
    # ORDER area: the fixture serves the TEST-ONLY post-order adjustment hook
    # (POST /testing/orders/{id}/adjust — 04-08 signed semantics, 01-era unsigned
    # log entries) and the 01-era fulfillment-event hook
    # (POST /testing/orders/{id}/fulfill — ORD-009); second product = the surviving
    # line item in the removed-line-item scenario (ORD-002/007/009).
    "order": {"simulate_adjustment": True, "simulate_fulfillment": True,
              "second_product_id": "mug_enamel"},
    # CART area (04-08): a second distinct product so the update-replaces-not-merges
    # probe (CART-017, merchant_checks_04_08_cartupdate.py) can tell a replaced-away
    # line from a legitimately consolidated one.
    "cart": {"second_product_id": "kettle_copper"},
    # WEBHOOK/EVENTS area (04-08): the merchant discovers the platform's order
    # webhook_url from the platform profile named in UCP-Agent and can deliver
    # signed order events to a LOOPBACK receiver (the fixture's offline policy
    # fetches loopback harness profiles only). A remote merchant cannot reach a
    # local receiver, so omitting this key skips the webhook checks honestly.
    "webhooks": {"simulate": True},
    "totals": {"sublines": True},   # 04-08 mode itemizes the subtotal entry (TOT-017)
    # PAYMENT AREA (04-08 grind): the fixture's seeded handler declaration and the
    # 3DS soft-decline token (escalate_token -> requires_escalation + continue_url)
    "payment": {"handler_key": "dev.spck.tokenpay",
                "handler_id": "spck_tokenpay",
                "escalation_payment": _pay("escalate_token", "9999")},
    # negotiation-failure platform profiles (discovery area, 04-08): each URL makes a
    # fetching business exhibit one negotiation error. The fixture recognizes these
    # SEEDED URLs (server.py negotiate_platform simulates the fetch outcome); a real
    # merchant needs config URLs that genuinely exhibit each failure.
    "negotiation": {
        "unsupported_version_profile_url": "https://spck.dev/fixture/platform/legacy-version.json",
        "incompatible_caps_profile_url": "https://spck.dev/fixture/platform/no-common-caps.json",
        "unreachable_profile_url": "https://spck.dev/fixture/platform/unreachable-profile.json",
        "malformed_profile_url": "https://spck.dev/fixture/platform/malformed-profile.json",
    },
    # RFC 9421 signatures (2026-04-08 signatures.md; SIGNATURES area checks):
    #   responses: the fixture signs every JSON response (ES256, @status +
    #     content-digest + content-type) with the key it publishes in the profile's
    #     signing_keys[].
    #   request_private_jwk: TEST private key (committed on purpose) whose public
    #     part the fixture bakes into TRUSTED_PLATFORM_KEYS — supplying it asserts
    #     the merchant under test verifies ES256-signed requests (SIG-002).
    "signature": {
        "responses": True,
        "request_private_jwk": {
            "kid": "spck-platform-sig-2026", "kty": "EC", "crv": "P-256",
            "x": "fdOWNX6FUcEYKQntKv0Pb0wpcIEV6HrDZK4Ud9oF_rY",
            "y": "-Ie-pMb2OxUqg4GR_B6wObhra9-fRe5YWzWAAv7dNKk",
            "d": "EymkNYgazGbLoD16l-fw7K-C9WNJEIv4hn_RpRgW5xY"},
    },
    # OAUTH area (identity-linking): the fixture's registered platform clients and
    # gated operations (server.py OAUTH_CLIENTS / ORDER_*_SCOPES). TEST credentials,
    # committed on purpose. A real merchant supplies its own registered client(s)
    # and the operation(s) its config.scopes gate.
    "identity": {
        # public client: token_endpoint_auth 'none' + PKCE S256 (RFC 8252 agent)
        "client_id": "spck-platform-public",
        "redirect_uri": "https://platform.spck.dev/oauth/callback",
        "scopes": ["dev.ucp.shopping.order:read", "dev.ucp.shopping.order:manage"],
        "public_none": True,                 # metadata advertises 'none' (IDL-023)
        # confidential client for the client_secret_basic checks (IDL-024/IDL-007@01)
        "confidential": {"client_id": "spck-platform-confidential",
                         "client_secret": "spck-confidential-secret-2026"},
        # registered loopback redirect — the PORT is ignored at match time (IDL-021)
        "loopback_redirect": "http://127.0.0.1:7777/oauth/cb",
        # an operation gated by ONE scope (identity_required / access checks) and
        # one needing TWO scopes with a strict-subset token (insufficient_scope —
        # proves the challenge lists the FULL set, IDL-047)
        "gated": {"method": "GET", "path": "/orders",
                  "scopes": ["dev.ucp.shopping.order:read"]},
        "gated_multi": {"method": "POST", "path": "/orders/ord_probe/cancel",
                        "scopes": ["dev.ucp.shopping.order:read",
                                   "dev.ucp.shopping.order:manage"],
                        "have_scopes": ["dev.ucp.shopping.order:read"]},
        "continue_url": True,        # 401 bodies carry an onboarding continue_url
        "resource_metadata": True,   # challenges carry resource_metadata (RFC 9728)
        # 01-era (2026-01-11/01-23) standard scope vocabulary
        "scope_01era": "ucp:scopes:checkout_session",
    },
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
