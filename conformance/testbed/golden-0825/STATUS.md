# golden-0825 — status

OUR OWN minimal reference store server for UCP **v2026-08-25**, built as harness
infrastructure for this repo (like `conformance/ci/serve_golden.sh` serves the
04-08 flower shop). It exists because **no upstream v2026-08-25 reference server
exists yet**: `samples` has no migration branch (still pinned at 00333a80, serving
04-08 — see `conformance/SOURCES.lock.json` → `reference_sample_server.note`), and
the 08-25 launch-edition writeup (`public/state-of-ucp/launch-2026-08-25.html`,
hazard 7) confirms this is a known, real version-skew window, not an oversight on
our part.

Built by **adapting, not rewriting**, the vendored samples Python server
(`conformance/.vendor/samples/rest/python/server`) to speak v2026-08-25, pinning
`ucp-sdk==0.5.0` (the first PyPI release whose generated models match the 08-25
schemas — python-sdk tag `v2026-08-25`, PR #87 "update Python SDK schemas for UCP
release 2026-08-25", cut 2026-08-27).

## What serves

Boots with `serve_golden_0825.sh`, seeded from the same flower-shop fixture data
as the 04-08 golden. Full CRUD lifecycle, every response validated against the
**released** v2026-08-25 schemas via the official `ucp-schema` validator:

| Route | Verb | Status |
|---|---|---|
| `/.well-known/ucp` | GET | serves, validates against `profile.json#business_schema` |
| `/checkout-sessions` | POST (create) | serves, validates against `checkout.json` op=create |
| `/checkout-sessions/{id}` | GET (read) | serves, validates op=read |
| `/checkout-sessions/{id}` | PUT (update) | serves, validates op=update |
| `/checkout-sessions/{id}/complete` | POST | serves, validates op=complete |
| `/checkout-sessions/{id}/cancel` | POST | serves, validates op=cancel |
| `/carts` | POST (create) | serves, validates against `cart.json` op=create |
| `/carts/{id}` | GET / PUT | serves, validates op=read / op=update |
| `/carts/{id}/cancel` | POST | serves, validates op=cancel |
| `/orders/{id}` | GET | serves, validates against `order.json` op=read |
| `/orders/{id}` | PUT | serves (accepts a caller-supplied order document) |
| `/testing/simulate-shipping/{id}` | POST | serves (test-only sim endpoint, secret-gated) |
| `/webhooks/partners/{partner_id}/events/order` | POST | present in generated routes, not exercised by the smoke suite |
| `/discovery-mcp`, MCP transport (`routes/mcp.py`) | — | present, not exercised — REST is the proven transport |

**Discovery advertises** (`server/routes/discovery_profile.json`), version
`2026-08-25`, all names verified against the vendored release source
(`conformance/.vendor/ucp-2026-08-25/source/schemas/**/*.json` `name` fields, not
guessed):

- `dev.ucp.shopping` (service, transport=rest)
- `dev.ucp.shopping.checkout`, `.cart`, `.order` — core, proven end-to-end
- `dev.ucp.shopping.discount` — proven: a real promo code (`10OFF` from the seed
  data) applies correctly and the enriched checkout still validates
- `dev.ucp.shopping.fulfillment` — proven: shipping destination + rate selection
  (`std-ship` / `exp-ship-us` from `shipping_rates.csv`) computes correct options
  and totals
- `dev.ucp.shopping.buyer_consent` — proven for valid input: a `buyer.consent`
  purpose keyed by reverse-DNS (e.g. `com.example.marketing`) round-trips and
  validates. **Known gap**: a malformed purpose key (not reverse-DNS, e.g. bare
  `"marketing"`) surfaces as an unhandled 500 instead of a 422 — a general
  "extension `ValidationError` isn't caught into the UCP error envelope" gap
  inherited from the 04-08 base's exception handling, not introduced by this
  migration and not fixed here (out of scope for a schema-migration pass).
- `dev.shopify.shop_pay`, `com.google.pay`, `dev.mock.payment_handler` (payment
  handlers, unchanged shape from 04-08)

**Honestly NOT advertised** (new 08-25 surfaces, not implemented):
`dev.ucp.common.location.search`, `dev.ucp.common.location.lookup`,
`dev.ucp.common.payment.terms`, `dev.ucp.common.payment.split_payments`, request
constraints (`#655`/`#744`) as a negotiated surface, and the `amenity.*` family.
The smoke suite asserts the location capabilities are absent, not just untested.

## Migration: what actually broke (and how it was fixed)

The mission's guessed break ("generated_routes imports shopping.payment_create_request
etc.") was correct but incomplete. Full list, in the order hit:

1. **Import path moves** (`shopping.X` → `common.types.X` or `common.X`) — the
   common-primitives/payment refactor (#736/#741): `payment`, `payment_create_request`
   → `common.types.*`; `ap2_mandate` → `common.payment_ap2_mandate`;
   `types.total`, `types.postal_address` → `common.types.*`. Fixed across
   `generated_routes/ucp_routes.py`, `routes/ucp_implementation.py`, `models.py`,
   `services/{checkout,cart,fulfillment}_service.py`.
2. **`ShippingDestination` (and the fulfillment-method `destinations` field) got a
   required `type` discriminator** (const `shipping_address`) it didn't have in
   04-08. Missing it 422s; response construction needed it added explicitly.
3. **`FulfillmentMethod.destinations` was narrowed from the specific
   `ShippingDestination` to the generic `FulfillmentDestination` base** (only
   `id`/`type` declared; postal/address fields moved to `extra="allow"` data).
   This is a real spec generalization (the base now needs to support non-shipping
   destinations for the new Location capabilities), not a bug in the release —
   but the flower-shop `checkout_service.py` still did *direct* attribute access
   (`dest.street_address`) assuming those fields always exist. That 500s the
   instant a caller omits one, and 500s again on ANY reload of a persisted
   checkout (see next point), because the field is truly absent, not `None`.
   Fixed by using `getattr(dest, "field", None)` everywhere a destination's
   optional address members are read, and by constructing responses with
   `FulfillmentDestination` (not `ShippingDestination`) so pydantic doesn't
   reject the wrong sibling class. Four call sites in `checkout_service.py`.
4. **The 34-null class reproduces exactly where the mission said to check**:
   `checkout_service.py`'s order-persistence path (`complete_checkout`) dumped
   the order with `order.model_dump(mode="json", by_alias=True)` — no
   `exclude_none=True` — right before `GET /orders/{id}` serves it back
   VERBATIM (`routes/order.py` declares `response_model=dict[str, Any]`, so
   FastAPI's response-model filtering never runs). Every other `model_dump` call
   that builds a served checkout body already carried `exclude_none=True`
   (inherited from the samples fix this class targets); this one path did not.
   One-line fix; `test_no_null_class_regression`-equivalent assertion
   (`assert_no_nulls`) in the smoke suite proves it stays fixed.

None of these four are per-scenario patches — each is a general fix to how a
shared model/field is read or dumped, so the fix covers every call site of that
pattern, not just the one the smoke suite happens to exercise.

## Validator

Used the already-vendored `ucp-schema` Rust CLI (`conformance/.vendor/ucp-schema`,
built at 1.4.0 from the pinned commit; the globally-installed 1.4.1 from crates.io
is the same engine per `SOURCES.lock.json`'s own note — 1.4.1 was a release-please
version bump with no engine change vs the pinned SHA). **It did not crash on any
core surface** (discovery, checkout ×5 ops, cart ×4 ops, discount, fulfillment) —
the mission's contingency ("post-#66 head build if 1.4.1 crashes") was not needed
for CORE. PR #66 ("preserve schema resource identity in bundling/composition/
selection", merged 2026-08-24) may still matter for deeper multi-extension
composition scenarios we did not probe (e.g. discount+fulfillment+buyer_consent
extending the same checkout simultaneously with conflicting refs) — flagged as a
gap, not fixed, since it never actually manifested.

Extended `conformance/selfcheck/schema_oracle.py` (the repo's shared, trusted
oracle wrapper — not a bespoke validator) with a `"2026-08-25"` `SCHEMA_BASE`
entry, and generalized `_ucp_schema_path()` to prefer `profile.json` when present
(08-25's hierarchy reorg, #723, split the `{ucp: ...}` wrapper out of `ucp.json`
into its own `profile.json`) and fall back to `ucp.json` for older generations
that never had the split — a version-agnostic fix, not a special case; verified
it does not change behavior for 04-08/01-23/01-11 (no `profile.json` exists in
their vendored trees) and re-ran the existing `schema_parity()` self-check
(8/8 fixtures, still PASS).

## Smoke suite

`smoke/test_golden_0825_smoke.py`, 5 tests, all green:

```
test_server_boots_and_discovery_validates PASSED
test_checkout_happy_path_create_update_complete_order PASSED
test_checkout_cancel_lifecycle PASSED
test_cart_lifecycle_create_get_update_cancel PASSED
test_validator_kill_check_rejects_broken_payload PASSED
5 passed in ~6s
```

Covers: boot; discovery validates against the released profile schema AND
honestly omits new-surface capabilities; full checkout lifecycle
(create→update→complete→order GET) with every wire body validated; a separate
cancel lifecycle; the full cart lifecycle (create/get/update/cancel); the
34-null class does not reproduce (`assert_no_nulls` on the persisted order); and
a kill-check that deletes a required field from an already-valid response and
asserts the SAME validator call now rejects it — proving the 5 green results
above aren't a vacuously-passing wiring bug (a validator stub that always
returns `(True, "")` would pass every other test in this file).

Run:
```
cd conformance/testbed/golden-0825/server
uv run --group dev pytest ../smoke/test_golden_0825_smoke.py -v
```
Requires `conformance/.vendor/ucp-2026-08-25` fetched
(`conformance/ci/fetch_sources.sh`) and the vendored `ucp-schema` validator built
at `conformance/.vendor/ucp-schema/target/release/ucp-schema`
(`cd conformance/.vendor/ucp-schema && cargo build --release`) — that exact path
is what `schema_oracle.BIN` checks; a PATH-only `ucp-schema` install (e.g. via
`cargo install`) is NOT picked up by the shared oracle, on purpose, so the
binary under test stays the one the repo pins. Tests that need the oracle call
`_require_oracle()` and SKIP (not silently pass) when either the vendor tree or
the built binary is missing.

## R11: defect-injection mode + mutant battery (PLAN-0825 SS C.4 / SS8-L3.4)

The smoke suite's kill-check (above) proves the oracle CAN reject a broken
payload in principle. It does not prove the wall holds for the specific
constraint classes that actually broke ucp-sdk 0.5.0, or across every served
surface. R11 closes that gap: a config-driven defect-injection mode the
golden itself ships, off by default, plus a battery runner that proves it.

**Design.** `server/defects.py` is the one choke point: a single ASGI
middleware in `server.py` that, when armed (`--defects_config` set AND a
mutant named in `--defects_state_file`), patches a matching response's JSON
body per `server/defects_config.json` — DATA, never per-scenario server code
(the walls doctrine). The arm state is hot-reloaded per request from the tiny
state file, so ONE server boot serves the whole battery instead of a
boot-per-mutant. `--defects_config` unset (the default; a normal boot never
sets it) short-circuits before any body is even read — see
`server/defects_test.py` for the exact byte-identity proof (object identity,
not just equality, at the unit level; an end-to-end boot-to-boot byte
comparison is NOT meaningful here because golden-0825 mints a fresh ephemeral
webhook-signing keypair every boot — see
`conformance/selfcheck/validate_golden_0825_battery.py`'s phase-0 docstring).

**Catalog** (`server/defects_config.json`, 19 mutants): 4 mirror the proven
ucp-sdk 0.5.0 constraint-drop families (python-sdk#88-#93 — JWK's 5
conditional rules, the C62/scale rule, the maxProperties family, discriminator
array-retyping) served as real wire responses; 14 more cover the served core
one per surface/op (discovery, checkout ×5, cart ×4, order ×2, discount,
fulfillment, consent); 1 (maxProperties/`location_serves`) is fixture-only,
served through a dedicated `GET /testing/defect-fixtures/{key}` test route
(secret-gated like `/testing/simulate-shipping/{id}`, 404s whenever defects
mode is off) because this golden honestly does not implement Location
Search/Lookup — grafting that schema onto an unrelated business response
would validate vacuously, so it gets its own minimal wire host instead of a
fabricated capability.

**Battery runner**
(`conformance/selfcheck/validate_golden_0825_battery.py`, own port 8199 —
never 8182, selftest.sh's main golden): boots the golden, arms each mutant in
turn, and requires FIRED (the response demonstrably reflects the configured
patch — proven by re-walking the same patch instructions the server applied,
not just "differs somewhere") + CAUGHT (the mutant's declared official-
validator call rejects it) + RESTORED (disarmed, the same op validates clean
again). `--selftest` kill-tests the runner itself: a planted mutant with a
typo'd route (configured, never served) alongside a known-good positive
control, asserting the runner reports the first as `LOADER-BROKEN` and the
second `KILLED` — proving the fired-check actually does its job, not just the
oracle-check. Report-only line in `conformance/ci/run_suite.py` (too heavy —
two server boots, ~15s — for the default per-change gate run; the schema-
census precedent), self-expiring after 14 days.

**Result, first live run (2026-08-31): 18/19 killed, 1 acknowledged.** The
19th (`sdkdrop-jwk-missing-crv`) was recorded as an ACKNOWLEDGED oracle
COMPOSITION bug at the time — `so.validate_profile()` appeared not to
enforce `jwk_public_key`'s allOf-conditional `crv` requirement through the
full `business_schema` chain. **CORRECTED 2026-08-31 (lane/p3-wave2, R14 in
`ops/GAP-LEDGER-0825.md`): that diagnosis was wrong.** There was no oracle
bug. This golden published its signing key at `ucp.keys[]` (nested) plus the
retired top-level `signing_keys[]` — neither is the schema-canonical
location. `profile.json`'s `$defs.base` declares `keys` as a TOP-LEVEL
sibling of `ucp` (`properties: {ucp, keys}`), confirmed in prose at
`overview/index.md#L1262-1265` ("MUST appear in the top-level `keys[]`
array"). Because `ucp.keys[]` is unvalidated `additionalProperties` noise
(`ucp.json`'s `business_schema` declares no `keys` property inside `ucp`
at all), the oracle was never exercising the crv rule in the first place —
the mutant's patched field simply wasn't schema-governed at that path. This
golden's own verifier, `ucp_signing._extract_keys`, had the identical bug
(reading `ucp.keys[]`), so the two sides were self-consistent and the defect
was invisible to the smoke suite — exactly the class of bug
`GAP-LEDGER-0825.md`'s S8a entry already named in the Node samples server,
now found independently in this Python golden. **Fixed**:
`routes/discovery.py` now publishes `keys[]` at the top level;
`ucp_signing._extract_keys` reads the same location; the mutant's patch path
is now `["keys", 0, "crv"]`. Re-run: **19/19 killed, 0 acknowledged**
(`defects_config.json`'s `acknowledged_gaps` is now empty; the closed entry's
text is kept in `_acknowledged_gaps_history` for the record).

The barred door (SS C.6) is unaffected by this work: golden-0825 remains
absent from `conformance/ci/differential_targets.json` and
`conformance/coverage/` — this lane adds test machinery, no evidence claims.

Run:
```
python3 conformance/selfcheck/validate_golden_0825_battery.py
python3 conformance/selfcheck/validate_golden_0825_battery.py --selftest
cd conformance/testbed/golden-0825/server && uv run --group dev pytest defects_test.py -v
```

## Deliberately NOT done (budget-box)

- MCP/embedded/A2A transports (`routes/mcp.py` exists, untested) — REST is the
  proven transport, matching the mission's "get CORE serving first."
- Request constraints (#655/#744) and Location Search/Lookup (#589) — new 08-25
  surfaces; not implemented, honestly not advertised, per the mission's explicit
  sequencing ("new-surface stubs only AFTER core is green").
- The vendored upstream `cart_test.py` / `integration_test.py` (the samples
  repo's own unit/integration tests) had the same import breaks fixed
  opportunistically, but were not made to actually pass: they assume a
  `from server.server import app` resolution that only works when the package
  is invoked from `rest/python/` (the parent of both `client/` and `server/` in
  upstream's layout); our `testbed/golden-0825/` has no sibling `client/`, so
  that import path is a pre-existing layout assumption, not a v2026-08-25 break.
  Our own smoke suite exercises the server over real HTTP instead and does not
  depend on it.
- Webhook delivery (`/webhooks/partners/.../events/order`) — present, unexercised.
- `PUT /orders/{id}` — present, unexercised (accepts caller-supplied order data
  verbatim; no schema validation was proven for that path).

## Files

- `conformance/testbed/golden-0825/server/` — the adapted FastAPI server
- `conformance/testbed/golden-0825/test_data/flower_shop/` — seed CSVs (copied
  from the vendored samples fixture, unchanged)
- `conformance/testbed/golden-0825/serve_golden_0825.sh` /
  `stop_golden_0825.sh` — boot/teardown, mirroring `conformance/ci/serve_golden.sh`
  / `stop_golden.sh` (seed → sync → SDK-pin guard → boot → health-poll → pid file)
- `conformance/testbed/golden-0825/smoke/test_golden_0825_smoke.py` — the TDD suite
- `conformance/selfcheck/schema_oracle.py` — extended (not rewritten) with the
  2026-08-25 schema base and the profile.json/ucp.json fallback
- `conformance/testbed/golden-0825/server/defects.py` — R11 defect-injection
  engine (the middleware choke point + mutation primitive)
- `conformance/testbed/golden-0825/server/defects_config.json` — the 19-mutant
  catalog (DATA)
- `conformance/testbed/golden-0825/server/defects_test.py` — hermetic unit
  tests (byte-identity proof + patch correctness)
- `conformance/testbed/golden-0825/server/server_state.py` — the shared,
  lazily-constructed `DefectsEngine` singleton
- `conformance/testbed/golden-0825/server/routes/defect_fixtures.py` —
  test-only fixture-echo route for the one mutant with no natural business host
- `conformance/selfcheck/validate_golden_0825_battery.py` — the battery runner
  (own boot, own port 8199, `--selftest` kill-tests the runner itself)
