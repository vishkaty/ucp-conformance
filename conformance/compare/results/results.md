# Kill-rate comparison — spck suite vs official UCP conformance suite

_Generated 2026-08-12T11:18:18-0400 · methodology + fairness rules: see README.md in this directory. All numbers reproducible via `reproduce.sh`._

## Pinned inputs

- **spck_repo**: `ce4d9bf85170c967374e94eab378f0906db8242f+dirty`
- **official_conformance**: `6ee0b53c1925a6ed49bf38d7d4d6f2bdc4e8c7be`
- **official_python_sdk**: `a0d8308e080c48bfe70128eeaabbddfa165ddc46`
- **golden_samples**: `52377503de720f8707717f9eba10f064f1413c64`

## Clean-golden baselines (through the same passthrough proxy)

- official: 76 tests, 0 failed, 0 errors, 3 skipped — stable across repeat baseline runs
- spck: aggregate `incomplete`, 37 applicable MUSTs clean-pass, 0 deviations

## Per-mutant results (head-to-head = shared surface only)

| mutant | injected MUST-defect | spec MUST | shared? | spck | official | deterministic |
|---|---|---|---|---|---|---|
| disc-version-nondate | Discovery profile advertises a non-date version string ('draft') | OVR-010 | Y | **CATCH** — discovery.version, negotiation.compatible_version_processed | **CATCH** — test_discovery, test_version_negotiation | yes |
| resp-ucp-envelope-missing | Checkout responses omit the ucp envelope entirely | OVR-004 | Y | **CATCH** — checkout.response_fields, payment.response_handlers_echo, response.capabilities_relevant, response.envelope_every_response | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| chk-missing-status | Checkout responses omit the required top-level `status` field | CHK-034 | Y | **CATCH** — checkout.cancel, checkout.complete_order, checkout.create_valid, checkout.response_fields | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| chk-missing-totals | Checkout responses omit the required top-level `totals` field | CHK-034 | Y | **CATCH** — discount.single_applied, totals.checkout_additive_non_negative, totals.checkout_entry_type_and_amount, totals.checkout_subtotal_and_total | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| chk-missing-currency | Checkout responses omit the required top-level `currency` field | CHK-034 | Y | **CATCH** — checkout.response_fields | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| chk-status-bad-enum | Checkout `status` carries a value outside the six lifecycle values ('shipped') | CHK-033 | Y | **CATCH** — checkout.cancel, checkout.complete_order, checkout.create_valid, checkout.retrieve | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| tot-subtotal-removed | No totals entry of type `subtotal` in checkout responses | TOT-005 | Y | **CATCH** — totals.checkout_additive_non_negative, totals.checkout_subtotal_and_total | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| tot-total-removed | No totals entry of type `total` in checkout responses | TOT-006 | Y | **CATCH** — totals.checkout_subtotal_and_total | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| tot-discount-positive | Discount totals entries carry POSITIVE amounts (sign flipped) | TOT-014, DSC-021 | Y | **CATCH** — totals.checkout_subtractive_negative | **CATCH** — test_discount_flow, test_fixed_amount_discount, test_multiple_discounts_accepted, test_multiple_discounts_one_rejected | yes |
| tot-subtotal-negative | Subtotal totals entries carry NEGATIVE amounts (sign flipped) | TOT-015 | Y | **CATCH** — totals.checkout_additive_non_negative | **CATCH** — test_discount_flow, test_fixed_amount_discount, test_multiple_discounts_accepted, test_multiple_discounts_one_rejected | yes |
| tot-entry-missing-amount | Subtotal totals entries omit the required `amount` field | TOT-020 | Y | **CATCH** — totals.checkout_additive_non_negative, totals.checkout_entry_type_and_amount | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| err-missing-messages | Error responses omit the `messages` array | ERR-028, ERR-030 | Y | **CATCH** — checkout.protocol_error_shape, checkout.unavailable_all_error_envelope | **CATCH** — test_complete_without_fulfillment, test_out_of_stock, test_payment_failure, test_product_not_found | yes |
| err-ucp-status-success | Error responses carry ucp.status 'success' instead of 'error' | ERR-029 | Y | **CATCH** — checkout.unavailable_all_error_envelope | MISS | yes |
| err-http-200-always | Server returns HTTP 200 for every request it actually rejected (errors only signaled in the body) | OVR-014, CHK-017 | Y | **CATCH** — checkout.completed_immutable, checkout.create_requires_line_items, checkout.idempotency_conflict, checkout.protocol_error_shape | **CATCH** — test_cannot_cancel_completed_checkout, test_cannot_complete_canceled_checkout, test_cannot_update_canceled_checkout, test_cannot_update_completed_checkout | yes |
| complete-missing-order | Complete-checkout response omits the populated `order` field | CHK-025 | Y | **CATCH** — checkout.complete_order, order.confirmation_fields, order.entity_shape, order.line_item_shape | **CATCH** — test_complete_checkout, test_invalid_adjustment_status, test_malformed_adjustment_payload, test_order_adjustments | yes |
| order-missing-permalink | Order confirmation omits `permalink_url` | ORD-020 | Y | **CATCH** — order.confirmation_fields | **CATCH** — test_complete_checkout | yes |
| ful-option-untitled | Every fulfillment option omits the required `title` | FUL-008 | Y | **CATCH** — fulfillment.option_shape | **CATCH** — test_option_titles_distinguish_siblings, test_unknown_discount_code, test_order_fulfillment_retrieval, test_discount_entry_is_negative | yes |
| chk-body-not-json | Checkout response bodies are not valid JSON (truncated by one byte) | CHK-044 | Y | **CATCH** — checkout.cancel, checkout.complete_order, checkout.create_valid, checkout.deterministic_logic | **CATCH** — test_ap2_mandate_completion, test_token_binding_completion, test_buyer_consent, test_buyer_info_persistence | yes |
| disc-cache-control-missing | Discovery profile served without any Cache-Control header | DISC-003 | N — addendum | **CATCH** — discovery.profile_cache_control | MISS | yes |
| resp-content-type-wrong | Responses served with Content-Type text/html instead of application/json | OVR-008 | N — addendum | **CATCH** — response.content_type_json | MISS | yes |

## Head-to-head catch-rate (shared surface only)

- **spck suite: 18/18 (100%)**
- **official suite: 17/18 (94%)**

## Addendum — surfaces only spck covers (NEVER counted in the head-to-head)

- 2/2 caught by spck; the official suite does not attempt these surfaces, so no official number is claimed for them.
  - disc-cache-control-missing: Discovery profile served without any Cache-Control header (DISC-003) — caught by spck. HTTP response-header surface: the official suite has no header assertions anywhere, so counting this against it would pad our number. Reported only in the spck-only addendum.
  - resp-content-type-wrong: Responses served with Content-Type text/html instead of application/json (OVR-008) — caught by spck. HTTP response-header surface (see disc-cache-control-missing).

## Official-suite misses on the common set (candidate upstream test-gap filings)

- err-ucp-status-success: Error responses carry ucp.status 'success' instead of 'error' (ERR-029)
