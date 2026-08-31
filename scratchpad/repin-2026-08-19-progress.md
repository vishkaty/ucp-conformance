# Re-pin 2026-08-19 — progress log (lane/repin-2026-08-19)

## Step 1: upstream facts VERIFIED via gh api (2026-08-19)

- samples#179 (vishkaty) "feat(rest/nodejs): sign order-event webhooks (RFC 9421)"
  MERGED 2026-08-17T19:55:27Z, merge commit 7049d8d1ad3cc49f2bab3647307d95e469376a7f
- samples#187 (vishkaty) "fix(rest/nodejs): answer simulate-shipping order-not-found with the UCP error envelope"
  MERGED 2026-08-19T15:07:30Z, merge commit bd06e290b6cd64c3fb9115f861ca59399d65f6e3
- conformance#87 (FanouZeng-TT) "fix: accept in-band missing fulfillment errors"
  MERGED 2026-08-17T08:13:24Z, merge commit 7bad47b475c4fb785f1762083f317d1601ff8476
- samples main HEAD    = bd06e290b6cd64c3fb9115f861ca59399d65f6e3 (2026-08-19T15:07:29Z, the #187 merge itself)
- conformance main HEAD = fdbdafdbe7c72071aeb330ab1f4a894c531e3fca (2026-08-18T18:33:52Z, #88 — ONE commit past 7bad47b4/#87; precedent 4a30765 pins to actual main HEAD at re-pin time, so we pin fdbdafd)

### Commit ranges reviewed (every commit, author + title)
conformance 6ee0b53..fdbdafd (11): #86 #85 #84 #83 #82 (webhook-test soundness, FanouZeng-TT/yingzhehu-TT), #87 (in-band missing fulfillment errors), #90 #91 (docs), #92 (validate published SDK dep), #93 (fixture dynamic fulfillment option ids), #88 (disable response model for mock webhook route). All test-soundness/docs; no new assertion class that could false-fail our golden.
samples ab78116..bd06e290 (10): OUR #179 (Node RFC 9421 webhook signing — the Node-flip trigger), OUR #187 (Node testing.ts order-not-found UCP envelope), OUR #180 (python webhook-test checkouts completed by server-assigned id), #159 damaz91 (python cart capability + discount extension — NEW golden surface, watch selftest), #181 (Node: quote one fulfillment group per method), #182/#183/#184/#185/#186 docs-only.

## Pin-recording mechanism (read from 4a30765 + fetch_sources.sh)
- Pins live ONLY in conformance/SOURCES.lock.json (repo-wide grep for ab78116/6ee0b53 confirms; other hits are historical prose).
- conformance/ci/fetch_sources.sh materializes .vendor/ at the locked SHAs.
- merchant_golden_snapshot.json snapshots OUR OWN fixture (Ceramic Teapot), not upstream — no snapshot regen needed for a re-pin.
- Allowlist already EMPTY (both #174-class entries deleted at 4a30765); no #179/#187-class entries to delete. Self-expiry can only fire if a check DEVIATES anew — watch selftest.
- packaging bundle auto-synced+staged by selftest.sh.

## Planned edits
1. SOURCES.lock.json: official_conformance_suite 6ee0b53→fdbdafd, reference_sample_server ab78116→bd06e290, notes + generated_context.
2. differential_nodejs.config.json: webhooks.signed=true, webhooks.retries=true (mirrors 4a30765 flower flip; the hold recorded in SOURCES.lock "one future re-pin flips the Node config for both" — this is that re-pin).
3. differential_targets.json: refresh the stale Node-target note (still claims CHK-050/053 allowlist entries exist and pin 52377503).
4. fetch_sources.sh, then full selftest: bash conformance/ci/selftest.sh --verbose --require-server (matches CI: run_suite.py --require-server --verbose with both goldens up, Node on :3000).

## Edits made
1. SOURCES.lock.json: official_conformance_suite 6ee0b53c→fdbdafdb (2026-08-18T18:33:52Z), reference_sample_server ab781163→bd06e290 (2026-08-19T15:07:29Z); notes rewritten per the 4a30765 pattern (commit-by-commit review recorded, prior history kept as breadcrumbs); generated_context gained the 2026-08-19 pass.
2. differential_nodejs.config.json: webhooks gained "signed": true, "retries": true (mirrors flower flip at 4a30765; closes the hold "one future re-pin flips the Node config for both").
3. differential_targets.json: Node-target note refreshed — was still claiming pin 52377503 + two live allowlist entries; now records the healed-deviation history (#168→#174→#187), the EMPTY allowlist, and the 08-19 signed/retries flip.

## Pre-selftest verification
- Vendored samples HEAD == bd06e290; testing.ts carries ResourceNotFoundError/ucpErrorResponse (#187); webhook_signer.ts present (#179).
- #179 key model: ephemeral key auto-generated when WEBHOOK_SIGNING_KEY unset, public JWK published in profile signing_keys[] — serve_node_reference.sh needs no change.
- ucp-schema oracle binary present (schema gates run for real).
- Only remaining #179/hold references in the repo are the ones written this pass.

## Selftest result: 67 PASSED / 4 FAILED (log: scratchpad/selftest-repin-2026-08-19.log)

### The two DESIGNED flips — both GREEN, evidenced
1. **Node webhooks.signed/.retries flip**: differential "nodejs-reference-sample: 56 checks pass, 0 deviation(s)" (was 47 clean-passes at the pre-#174 pin, and signed/retry checks previously could not run). The signed/retry cluster grades LIVE against the Node reference and passes. #179's key model needs no serve-script change (ephemeral key auto-generated, JWK published in profile signing_keys[]).
2. **#187 order-not-found envelope**: zero Node deviations of the flat-detail class; allowlist was already EMPTY and stayed empty (nothing to delete, nothing self-expired). Python golden webhook cluster (7 checks) still sound (clean-pass + kill_safe).

### The WRONG-WAY flip — STOPPED, root-caused, NOT allowlisted/registered
All 4 red gates (merchant, probe-hygiene, differential[flower], fuzz) share ONE root cause: **samples#159 (damaz91, merged 08-14, python golden cart capability + discount extension) shipped two crash bugs**. #180 is test-only — exonerated. Registers were NOT touched: probe-hygiene/fuzz registers require an upstream link ("an entry without one is suppression") and this session is READ-ONLY vs GitHub.

**Bug A — cart create 500s on any spec-shaped request that includes `currency`:**
- `services/cart_service.py:119` builds `Cart(..., currency="USD", ..., **cart_data)` where `cart_data = cart_req.model_dump(exclude={"line_items"})`. The SDK's CartCreateRequest has `extra=allow` and no declared `currency`, so a request-supplied currency rides through as an extra key → `TypeError: models.UnifiedCart() got multiple values for keyword argument 'currency'` → HTTP 500 text/plain.
- Wire proof: POST /carts {line_items,currency:"USD"} → 500 "Internal Server Error"; identical POST WITHOUT currency → 201 conformant cart (the negative control; also why cart.conversion_carries_codes still passes — its probe omits currency).
- Fails: cart.response_shape, cart.line_item_shape, totals.subtotal_and_total, totals.additive_non_negative, cart.response_body_valid_json, signals.attribution_no_effect (6 merchant-gate BROKEN + same 6 as flower differential deviations + 6 probe-hygiene unexplained-5xx).

**Bug B — checkout create 500s on wrongtype `currency` / `line_items[].id` (8 NEW fuzz crashes):**
- SDK CheckoutCreateRequest: `extra=allow`, no `currency` field → wrongtype extras pass the request boundary; `checkout_service.py:366` then constructs the response `Checkout` model with the raw value → uncaught `pydantic ValidationError: currency Input should be a valid string [input_value=123]` → 500.
- Wire proof: POST /checkout-sessions with currency:123 → 500; traceback in /tmp/ucp_test/server.log.
- Fuzz also logged 12 SOFT spec-contradicting accepts (triage, not gate-failing).

### Why no green path exists that still delivers the goal
#159 merged 08-14 < #179 (08-17) < #187 (08-19): any samples pin containing the goal PRs contains #159. Linear history, upstream SHAs only — cherry-pick pins are not a thing here.

### Next actions (owner-gated, NOT done this session)
1. File the two #159 crash bugs upstream on samples (shown-before-post rule; wire repros + tracebacks above; suggested fixes: cart_service exclude currency from the splat or drop the hardcoded kwarg; checkout_service catch ValidationError → 422/UCP envelope or validate extras at the boundary).
2. Register: Bug A entries in known_reference_defects.json (6 probe ids) + Bug B entries in known_fuzz_defects.json (8 case ids), each WITH the upstream link — gates then acknowledge loudly and pass; differential flower rows may need the same treatment via differential_allowlist (real target bug, cite the issue).
3. On the fix landing upstream: re-pin, entries self-expire RED, delete them — the designed payoff loop.

## Status
- [x] Step 1 verify upstream
- [x] Edits
- [x] fetch_sources (samples + conformance re-cloned; all other pins unchanged)
- [x] full selftest run end-to-end: 67/71 gates pass; 4 red, all one upstream root cause (above); the two designed flips GREEN
- [x] commit: 74031f3142f10a3b056e46fd0b81627744cff898 on lane/repin-2026-08-19, author Vishal Katyal <vishal@katyal.ai>, no trailers, NOT pushed. 3 files: conformance/SOURCES.lock.json, conformance/ci/differential_nodejs.config.json, conformance/ci/differential_targets.json. Packaging bundle unaffected (bundle excludes ci/ and SOURCES.lock).
