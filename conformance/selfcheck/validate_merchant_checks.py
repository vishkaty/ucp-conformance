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
def _pay(token, last, handler="mock_payment_handler"):
    """`handler` must be an id the golden ADVERTISES: the flower reference seeds
    mock_payment_handler; the controlled fixture advertises spck_tokenpay and
    (PAY-014) rejects completions naming an unadvertised handler."""
    return {"payment": {"instruments": [{"id": "instr_" + last, "handler_id": handler,
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
                 "invalid_code": "INVALID_CODE",   # seeded codes
                 # The reference matches codes case-insensitively as of samples#128
                 # (db.py: func.upper(Discount.code) == code.upper()), which is our
                 # own merged fix. Declaring it here turns DSC case-insensitivity
                 # from a dormant check into a live regression guard on that fix:
                 # if the reference ever reverts to exact-PK matching, this goes red
                 # instead of quietly reporting not-tested.
                 "case_insensitive": True},
    # WEBHOOK/EVENTS (04-08): the reference delivers order webhooks to the URL in
    # the platform profile named by UCP-Agent (ucp_implementation.py
    # extract_webhook_url) with the FULL order entity as body (samples#140 — our
    # merged fix). Declaring simulate turns webhook.order_created_full_entity from
    # dormant into a live regression guard on that fix
    # (validate_webhook_reference.py proves it clean-pass + kill_safe AND proves
    # the kill direction on the real reference). `signed`/`retries` are
    # DELIBERATELY absent: the reference does not yet sign webhook deliveries (no
    # RFC 9421 headers, no UCP-Agent on the delivery — the ucp#568 contract) nor
    # retry failures, so those checks would false-deviate a capability the golden
    # does not have ("correctly dormant, do not force"). The same gate pins those
    # gaps as tripwires and goes red the moment upstream implements them.
    "webhooks": {"simulate": True, "wait_seconds": 8.0},
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
    "complete_payment": _pay("success_token", "1234", "spck_tokenpay"),   # fixture accepts any non-fail token
    "fail_payment": _pay("fail_token", "0000", "spck_tokenpay"),   # seeded failing token -> 402
    "out_of_stock_id": "trivet_cork",       # seeded zero-stock product (04-08: business outcome)
    # CHK-051: the fixture implements the 04-08 two-layer contract — unavailable
    # merchandise is an HTTP-200 business outcome (partial baskets are created
    # without the unavailable line + an item_unavailable message). The official
    # reference still answers 400 there, so REF_CONFIG deliberately omits this
    # key (correctly dormant; flip it the moment upstream adopts the 200 rule).
    "unavailable": {"business_outcome": True},
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
    # line from a legitimately consolidated one. The RECEIVER-tier cart-to-checkout
    # conversion checks (merchant_checks_04_08_receiver.py) also use it as the
    # CONFLICTING checkout-payload product the conversion MUST ignore (CART-001).
    "cart": {"second_product_id": "kettle_copper"},
    # ELIGIBILITY / SIGNALS-ATTRIBUTION area (04-08 receiver tier): reverse-domain
    # eligibility claim values the fixture recognizes/rejects (server.py
    # ELIG_VERIFIABLE / ELIG_UNVERIFIABLE). `verifiable` resolves at completion;
    # `unverifiable` is recognized (surfaces a provisional discount) but fails
    # verification at completion; `unrecognized` is ignored without error. A real
    # merchant supplies claim values its own business logic recognizes/verifies.
    "eligibility": {"verifiable": "com.spck.loyalty_gold",
                    "unverifiable": "com.spck.vip_unverifiable",
                    "unrecognized": "com.spck.unknown_benefit"},
    # WEBHOOK/EVENTS area (04-08): the merchant discovers the platform's order
    # webhook_url from the platform profile named in UCP-Agent and can deliver
    # signed order events to a LOOPBACK receiver. Supplying `simulate` asserts
    # THREE things about the merchant under test (W2-F2/F3, adversarial review):
    #   1. it can reach a local receiver (omit for remote merchants -> honest skip);
    #   2. it delivers within `wait_seconds` (default 8s — order.md pins no
    #      timing, so slow queued delivery needs a wider window);
    #   3. HARNESS CONVENTION: it will fetch a plain-HTTP LOOPBACK platform-profile
    #      URL for this test, a documented carve-out from the HTTPS-only profile
    #      rules (DISC-004 / signatures.md rule 5) that no spec text sanctions —
    #      a strictly-conformant merchant needs the HTTPS harness variant (backlog).
    # `signed` additionally asserts it SIGNS its deliveries per order.md "Webhook
    # Signature Verification" (RFC 9421 headers + UCP-Agent naming the business
    # profile) — gates the ORD-026/027/028 + SIG-014/015/017/027 checks; `retries`
    # asserts it retries a failed delivery within the window (ORD-031). Both are
    # split from `simulate` because the official reference delivers webhooks but
    # implements neither (see merchant_checks_04_08_events._WH_SIGNED/_WH_RETRY).
    "webhooks": {"simulate": True, "wait_seconds": 8.0,
                 "signed": True, "retries": True},
    "totals": {"sublines": True},   # 04-08 mode itemizes the subtotal entry (TOT-017)
    # PAYMENT AREA (04-08 grind): the fixture's seeded handler declaration and the
    # 3DS soft-decline token (escalate_token -> requires_escalation + continue_url).
    # `filtered` (PAY-015): the seeded CONTEXT-SENSITIVE handler (dev.spck.giftpay)
    # is dynamically removed from responses whose cart contains the named product
    # (the fixture's stand-in for "remove BNPL for subscription items").
    "payment": {"handler_key": "dev.spck.tokenpay",
                "handler_id": "spck_tokenpay",
                "escalation_payment": _pay("escalate_token", "9999", "spck_tokenpay"),
                "filtered": {"product_id": "kettle_copper",
                             "handler_key": "dev.spck.giftpay"}},
    # DISCOVERY/VERSIONING (04-08): the fixture publishes a supported_versions map
    # whose URIs serve version-specific LEAF profiles (OVR-009/OVR-010).
    "discovery": {"supported_versions": True},
    # IDEMPOTENCY test hooks (04-08 receiver tier, mint-hook precedent): the
    # fixture can AGE a stored idempotency key (SIG-023 — 24h retention is not
    # observable in one run without moving the server's own clock) and can
    # simulate a storage OUTAGE (SIG-024 — fail closed with 503). A real merchant
    # opts in by exposing the hooks; without them the checks skip honestly.
    "idempotency": {"age_hook": True, "outage_hook": True},
    # negotiation-failure platform profiles (discovery area, 04-08): each URL makes a
    # fetching business exhibit one negotiation error. The fixture recognizes these
    # SEEDED URLs (server.py negotiate_platform simulates the fetch outcome); a real
    # merchant needs config URLs that genuinely exhibit each failure.
    "negotiation": {
        # The fixture resolves the platform profile URL from UCP-Agent, so the
        # profile-URL duties (NEG-005 missing -> 400 invalid_profile_url, DISC-004
        # reject non-HTTPS) are gradeable here. server.py rejects non-loopback
        # http:// with 400 invalid_profile_url. A business that does not fetch
        # profiles omits this key and those checks skip instead of false-flagging.
        "validates_profile_url": True,
        # `harness` asserts the merchant FETCHES a loopback platform profile and
        # negotiates the REAL capability intersection from it (OVR-005/OVR-012 —
        # the same loopback carve-out webhooks.simulate documents). Merchants
        # that cannot reach a local harness omit this -> honest skip.
        "harness": True,
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
        # SIG-009: the platform's PREVIOUS signing key, rotated out inside the
        # 7-day grace window — the fixture keeps accepting it (TRUSTED_PLATFORM_
        # KEYS carries its public part as the rotated entry). TEST key, committed
        # on purpose (derived from the fixture's deterministic keygen).
        "rotated_private_jwk": {
            "kid": "spck-platform-sig-2025", "kty": "EC", "crv": "P-256",
            "x": "trOfp-wdZbq4DptegBp30j2ZhfOQktq1xwV9p192Vpo",
            "y": "35f58EZuhhP5adAnylqYQkE0w7PqynX4RH3j0VSUdxY",
            "d": "z1FmQAxm-O1vmNqG99IJpUFWKbDRhRNj7SBiekFKhSU"},
        # SIG-035: a key PUBLISHED in the platform's signing_keys whose algorithm
        # (Ed25519) this merchant does not support — referencing it must yield
        # 400 algorithm_unsupported (resolution succeeds, algorithm cannot).
        "unsupported_alg_kid": "spck-platform-ed25519",
    },
    # OAUTH area (identity-linking): the fixture's registered platform clients and
    # gated operations (server.py OAUTH_CLIENTS / ORDER_*_SCOPES). TEST credentials,
    # committed on purpose. A real merchant supplies its own registered client(s)
    # and the operation(s) its config.scopes gate.
    "identity": {
        # asserts IDL-050's conditional clause (serves native/agent platforms)
        "serves_public_clients": True,
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
        # the fixture exposes POST /testing/oauth/mint (deterministic expired/
        # revoked/foreign-client tokens) so the suite can probe "the business
        # validates the token on every request" (IDL-042 exp/revocation, IDL-025
        # client binding) — a real merchant opts in by exposing the hook.
        "token_mint": True,
        # OVR-006: the platform profile the public client is REGISTERED to act
        # for, plus a conflicting profile no client is bound to — a valid token
        # presented under the conflicting UCP-Agent MUST be rejected.
        "profile_binding": {"registered": "https://spck.dev/agent",
                            "mismatched": "https://intruder.example/platform.json"},
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

    from validate_probe_hygiene import load_known
    known_defects = load_known() if args.golden == "flower" else {}
    broken, weak, ok, skipped, ref_defects = [], [], [], [], []
    for chk, d in detail:
        st = d["status"]
        # any not-applicable/not-tested status (incl. suffixed reasons like
        # "not-applicable (no MCP transport)") is a legitimate skip on a golden
        # that lacks the capability/transport — not a broken check
        if isinstance(st, str) and st.startswith(("not-applicable", "not-tested")):
            skipped.append((chk.id, st)); continue
        if st != CLEAN:                       # deviation/inconclusive on a KNOWN-GOOD server → broken check
            # ...unless the golden is known NOT to be good on this exact point. This gate
            # rests on the golden being conformant; where we have diagnosed, fixed and
            # reported a defect in it, the deviation is a true finding about the reference
            # and not evidence that our check is broken. The register demands an upstream
            # link and self-expires once the defect stops reproducing (probe-hygiene), so
            # this cannot become a way to keep a genuinely broken check green.
            if chk.id in known_defects:
                ref_defects.append((chk.id, st, known_defects[chk.id])); continue
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
    for cid, st, d in ref_defects:
        print(f"  ! {cid:32} {st} caused by a REPORTED defect in the golden, not by the check")
        print(f"      {d['upstream']} (filed {d['filed']})")

    n_run = len(ok) + len(broken) + len(weak)
    print(f"\n  {len(ok)}/{n_run} run checks sound · {len(skipped)} skipped (n/a on reference)")
    if broken or weak:
        print("  GATE FAILED — fix the check(s) above before they can grade a real merchant.")
        return 1
    print("  GATE PASSED — every runnable merchant check is sound on the reference server.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
