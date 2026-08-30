# P1 — 2026-08-25 carry-forward register: disposition report

Built on `lane/0825-register` (worktree `.claude/worktrees/lane-0825-register`), against the
vendored release at `conformance/.vendor/ucp-2026-08-25` (tag `cd78fb38`, commit
`cd78fb38e819de77d9b527d110476eccb876f1bd`). Scope: every row in
`conformance/requirements/2026-04-08/*.json` (482 rows / 16 areas) classified against the
reorganized 2026-08-25 doc/schema tree and re-anchored where it survives. This pass does
**not** add rows for genuinely new 2026-08-25 surface (location, loyalty, split payments,
Actions, permalink, authentication, 3DS challenge, device-data-collection, ...) — that is a
separate, complementary workstream (see "Other concurrent lanes" below).

## P0 recap (harness changes, committed separately — `342740b`)

- `conformance/coverage/matrix.py`: `VERSIONS` gains `"2026-08-25"`. Added an explicit
  `CURRENT_SITE_VERSION = "2026-04-08"` constant so `coverage_gate.py`'s site-copy-freshness
  check keeps grading the version the public site actually advertises, instead of silently
  following `VERSIONS[-1]` onto a version with no site copy at all.
- `conformance/selfcheck/verify_register.py`: `VERSION_TREE` gains
  `"2026-08-25": "ucp-2026-08-25"`.
- Deliberately **unchanged**: `verify_register_completeness.py` (the reorganized tree has new
  normative surface this pass does not attempt to fully extract — see P2), and
  `coverage_lock.json` / `review_signoffs.json` (see "Coverage-lock / review-signoff scope"
  below). `checkset_manifest.py` / `area_manifest_04_08.json` / `expected_total` lock the
  *runtime* fixture-check suite (`run_04_08.py`), a different concern — no `run_08_25.py`
  exists or is being added, so nothing there needed a fourth entry.

## Disposition totals

482 old rows → **478 written** (carried + renamed + reworded) + **4 dead**.

| Area | old rows | carried | renamed | reworded | dead |
|---|---:|---:|---:|---:|---:|
| cart | 33 | 32 | 0 | 1 | 0 |
| catalog | 38 | 32 | 1 | 4 | 1 |
| checkout-lifecycle | 57 | 53 | 1 | 3 | 0 |
| discounts-consent | 37 | 30 | 0 | 7 | 0 |
| discovery | 6 | 3 | 0 | 3 | 0 |
| error-envelope | 34 | 34 | 0 | 0 | 0 |
| fulfillment | 30 | 21 | 2 | 5 | 2 |
| identity-linking | 62 | 55 | 0 | 7 | 0 |
| negotiation-errors | 5 | 5 | 0 | 0 | 0 |
| order | 33 | 33 | 0 | 0 | 0 |
| overview | 14 | 11 | 0 | 3 | 0 |
| payment | 47 | 43 | 1 | 3 | 0 |
| signals-attribution-eligibility | 21 | 18 | 0 | 2 | 1 |
| signatures | 41 | 31 | 3 | 7 | 0 |
| totals | 17 | 17 | 0 | 0 | 0 |
| transports | 7 | 6 | 0 | 1 | 0 |
| **TOTAL** | **482** | **424** | **8** | **46** | **4** |

Every written id traces back to an old row in the same area file (no invented ids, no
duplicates, no cross-area leakage — verified programmatically). `versions` is `["2026-08-25"]`
on every row; `source` uses the literal `"ucp:"` repo prefix on every row (see convention note
below); every row carries `"lineage": {"from": "2026-04-08", "disposition": ...}`.

## DEAD (4) — no corresponding requirement in 2026-08-25

| id | old requirement | evidence of removal |
|---|---|---|
| CAT-006 | Implementations SHOULD accept a page size of at least 10 | The floor is gone: grepped all of `docs/` + `source/` for "at least 10" / "page size" — only unrelated hits (an unrelated Lookup batch-size SHOULD, an unrelated `common/location/lookup.md` SHOULD). Replaced by a different mechanism: default page size is now only RECOMMENDED at 10 (see CAT-005/007/008, reworded). |
| FUL-004 | fulfillment_method.type MUST be one of shipping or pickup | Superseded by the new generalized Destinations model (fulfillment.md new "## Destinations" section) — method typing is no longer a closed shipping/pickup enum; see FUL-005/FUL-030 (reworded) for the replacement mechanism. |
| FUL-029 | Extensions that add new fulfillment method types MUST add an extension schema that adds the method to the fulfillment_method type enum and... | Same Destinations-model supersession as FUL-004 — the enum-extension mechanism this row described no longer exists in the new schema shape. |
| SAE-019 | Eligibility verification failure MUST only affect the messages array | Searched `checkout.md` / `shopping/checkout/index.md` and the eligibility-related schema files — the specific "only affect messages array" isolation guarantee has no surviving textual counterpart; the eligibility error-shaping rules were restructured (see SAE-021, reworded).

## RENAMED (8) — same substance, name/identifier changed

| id | area | what changed |
|---|---|---|
| CAT-004 | catalog | actor term `implementations` → the defined role `Business` (this release formalizes capitalized Platform/Business roles throughout) |
| CHK-042 | checkout-lifecycle | schema `$ref` path only: `types/signals.json` → `../common/types/signals.json` (signals.json relocated to the new shared `common/types/` folder); the `ucp_request: optional` contract is byte-identical |
| FUL-008 | fulfillment | `fulfillment_option.json`'s `required` obligation decomposed via a new shared `fulfillment_option_base.json` + `allOf` composition; the combined effective requirement (id, title, totals all required) is identical |
| FUL-017 | fulfillment | prose field name corrected `total` (singular, previously diverged from the schema) → `totals` (plural, matching the schema's actual array field) — converges a prior prose/schema mismatch |
| PAY-033 | payment | AP2 signing-key array renamed `signing_keys` → `keys`, consistently across the doc (also relocated `ap2-mandates.md` → `payment/extensions/ap2-mandates.md`) |
| SIG-006 | signatures | key-derivation field reference broadened from `crv` alone to compound `kty`/`crv` (kty now also load-bearing since keys can be EC or OKP) |
| SIG-025 | signatures | row label disambiguated `"On duplicate"` → `"On duplicate (matching payload)"` because a new sibling "mismatched payload" row was added; the matching-payload requirement itself is unchanged |
| SIG-033 | signatures | `signer's "signing_keys"` → `signer's published key set` (generalized wording, paired with the `signing_keys[]`→`keys[]` rename elsewhere); same error code/condition |

## REWORDED (46) — same intent, materially different prose; flagged for human review

Full verbatim old/new quotes and detailed rationale are recorded in each row's own `notes`
field in `conformance/requirements/2026-08-25/<area>.json` (that is the authoritative source —
this table is a navigation index, not a substitute).

| id | area | why flagged |
|---|---|---|
| CART-026 | cart | idempotency duplicate/conflict detection now explicitly tied to request-body matching (new cross-ref to signatures.md replay-protection) |
| CAT-005 | catalog | schema dropped literal `default: 10`; value 10 downgraded from schema default to RECOMMENDED prose |
| CAT-007 | catalog | drops the "SHOULD accept ≥10 / clamp silently" framing; broadened to cover the new default-page-size case |
| CAT-008 | catalog | actor renamed Clients→Platform *and* scope broadened to "either value" (requested or default) |
| CAT-024 | catalog | drops literal "default limit of 10", defers to the shared (now-RECOMMENDED) pagination contract |
| CHK-024 | checkout-lifecycle | adds an explicit `complete_in_progress` carve-out condition; promotes lowercase "clients must" to bold Platform MUST |
| CHK-025 | checkout-lifecycle | response contract split into 3 branches (sync-completed-with-order / async-complete_in_progress-without-order / other); **citations-gate REVIEWED_EQUIVALENT added** — see below |
| CHK-048 | checkout-lifecycle | "duplicate keys" / "different parameters" now explicitly defined via request-body matching; new cross-ref to signatures.md |
| DSC-031 | discounts-consent | scope broadened from checkout create/update only to cart create/update *and* checkout complete |
| DSC-032 | discounts-consent | drops "boolean consent states" (field is now a nested Purpose/Segment map, see DSC-033); extends to cart |
| DSC-033 | discounts-consent | flat boolean properties (`analytics`, `preferences`, `marketing`, `sale_of_data`) replaced by reverse-DNS-keyed purpose objects; **`sale_of_data`→`sale_or_sharing` is a genuine identifier change**; old oracle assertion shape is stale, needs re-verification |
| DSC-034 | discounts-consent | **normative reversal**: `complete_checkout` consent was `"omit"` (MUST NOT), now `"optional"` (MAY) — a compliant 08-25 implementation would fail the old rule |
| DSC-035 | discounts-consent | drops "declarative" terminology; moved into a new, more elaborate Scope section |
| DSC-036 | discounts-consent | SHOULD-NOT prose guidance formalized into a MUST tied to the new structural `source` field |
| DSC-037 | discounts-consent | one-liner folded into a longer "Confirm semantics" rule with new normative content (mandates including every advertised key once submitted) |
| DISC-001 | discovery | scope broadened "Profiles MUST be HTTPS" → "Published artifacts MUST be HTTPS" (now covers schema/transport-doc fetches too) |
| DISC-003 | discovery | scope broadened same as DISC-001; a new SHOULD (ETag/Last-Modified validator) appears alongside, not carried as a new row (out of scope) |
| DISC-004 | discovery | drops "profile" qualifier — HTTPS-only now explicitly covers jwks_uri/CIMD fetches in the identity-resolution chain too |
| FUL-001 | fulfillment | scope expanded: extension now applies to Catalog as well as Checkout (new "Catalog Discovery" section) |
| FUL-005 | fulfillment | old two-case (shipping=address/pickup=location) description replaced by the new generalized Destinations model |
| FUL-021 | fulfillment | old flat "SHOULD render in provided order" now explicitly permits platform reordering (new MAY carve-out) |
| FUL-022 | fulfillment | ordering authority flips: "present in order provided by business" removed, replaced by "platform chooses ordering" |
| FUL-030 | fulfillment | schema gains a required `type` discriminator field not present in 2026-04-08 |
| IDL-001 | identity-linking | "v1 auth mechanism / future versions" framing removed — `config.providers` + Accelerated IdP Flow are now a shipped feature |
| IDL-030 | identity-linking | a previously-SHOULD-only publishing behavior becomes a conditional MUST when the AS is off-domain |
| IDL-033 | identity-linking | issuer-match comparison target changed from "always the business domain" to "whatever Step 1 resolved" (new 3-step discovery pipeline) |
| IDL-056 | identity-linking | `config.providers` graduates from unshipped placeholder to real shipped feature; framing/keyword placement changed materially |
| IDL-057 | identity-linking | "ignore unknown fields → always direct OAuth" no longer holds; must now filter unsupported provider types and apply Identity Providers rules |
| IDL-059 | identity-linking | scope_token regex pattern broadened to permit hyphens (schema-level, not just a rename) |
| IDL-062 | identity-linking | OIDC-fallback discovery target changed from "business domain" (always) to "{issuer}" (Step-1-resolved, may differ) |
| OVR-002 | overview | **security-relevant**: `spec` field REQUIRED → downgraded to MAY (schema stays MUST) |
| OVR-003 | overview | **security-relevant**: origin-authority binding narrowed from spec+schema to schema only; spec URL explicitly exempted from provenance check |
| OVR-007 | overview | adjacent permission strengthened MAY→SHOULD; this row's own MUST-NOT text is unchanged but the surrounding context broadened to cover jwks_uri/CIMD fetches |
| PAY-021 | payment | `card_credential.json` marked `deprecated: true`, steered toward new `pan_credential.json` / `network_token_credential.json` |
| PAY-022 | payment | same deprecation as PAY-021; candidate for retirement in favor of the new schemas' equivalent MUST-NOT rows |
| PAY-026 | payment | `binding.json` generalized from a checkout-specific primitive (`checkout_id` required) to a generic capability-resource binding (`type`+`id` required); delegated-identity property removed, replaced by the new Tokenization Handler surface |
| SAE-003 | signals-attribution-eligibility | inline pattern replaced by a shared, more permissive `reverse_domain_name.json` $ref (allows hyphens, digit-led segments, punycode) |
| SAE-021 | signals-attribution-eligibility | adds a new MUST carve-out for Action-caused failures; original SHOULD retained for the non-Action case |
| SIG-004 | signatures | flat "SHOULD use P-256" replaced by a counterparty-driven algorithm-choice framework (pulls in WBA/AP2 considerations) |
| SIG-005 | signatures | P-384-specific permission generalized into a multi-key, any-algorithm rule (P-384 or new EdDSA/Ed25519) |
| SIG-008 | signatures | `signing_keys` array name dropped (now `keys[]`); publishing/lookup rules delegated to two cross-referenced overview sections (capability-based resolution) |
| SIG-030 | signatures | the blanket "`created` is OPTIONAL" rule now scoped to default-UCP signatures only; WBA-shape signatures carry their own additional timestamp requirements |
| SIG-036 | signatures | verification is now algorithm-generic (ECDSA or EdDSA) and failure action changed from hard `error()` to `skip_signature()` (try-next-candidate under multi-signature/WBA) |
| SIG-037 | signatures | same skip-and-retry behavioral change as SIG-036; key resolution is now capability-based rather than a fixed `profile.signing_keys` lookup |
| SIG-038 | signatures | same skip-and-retry behavioral change as SIG-036/037 for the digest-mismatch check |
| MCP-006 | transports | same pagination-contract rewording as CAT-024, embedded in the MCP conformance list |

**Security-relevant items for priority review:** OVR-002, OVR-003 (origin-authority binding
relaxation), DSC-034 (consent-on-complete reversal), DSC-033 (consent shape + `sale_of_data`→
`sale_or_sharing` rename — stale oracle assertion), PAY-021/PAY-022 (card_credential deprecation).

## Citations-gate fallout and fix (harness-level, not content)

Populating the 08-25 register and wiring `matrix.VERSIONS` (P0) exposed a real structural gap:
several `MCheck`/`Check` objects in `conformance/checks/merchant_checks.py` (23 checks) and
`conformance/checks/area_negotiation.py` (1 check) carry a `req_ids_map={"2026-04-08": [...]}"`
override (because the 2026-04-08 register renumbered these ids away from their 2026-01-11/
2026-01-23 meaning) but had no `"2026-08-25"` entry. Since these checks carry no filename
version-token or module `VERSIONS` marker, their file-target scope is "all versions" — so once
`2026-08-25` joined `matrix.VERSIONS`, the *default* (pre-2026-04-08-renumbering) `req_ids` was
what got attributed at 2026-08-25, not the 2026-04-08 meaning our carried-forward register
actually uses under that id. The citations gate correctly caught this as 21 divergent-text
attributions (similarity 0.24–0.4, well under the 0.55 threshold).

**Fix**: mirrored every existing `"2026-04-08"` key in these 24 `req_ids_map` entries with an
identical `"2026-08-25"` key (`conformance/checks/merchant_checks.py`,
`conformance/checks/area_negotiation.py`) — a mechanical, data-only extension of the exact same
mechanism the codebase already uses for id renumbering, not a new code path. This is a general
fix (every entry of this shape), not a per-case patch.

That reduced the gate to one real, substantive failure: `checkout.complete_order` (predicate
`p_completed`: `status=="completed" AND order truthy`) auto-extends to grade `CHK-025` at
2026-08-25, whose text was reworded (see table above) into three response branches. Verified the
check's predicate still exactly matches the *synchronous*-completion branch, which both versions
describe identically — the check is not over-strict, it simply doesn't yet exercise the new
async `complete_in_progress` branch. Added a `REVIEWED_EQUIVALENT["CHK-025"]` entry to
`conformance/selfcheck/verify_citations.py` (same pattern as the pre-existing `ORD-001` entry)
documenting this, and flagging the async branch as a candidate for a future oracle/probe.

## Coverage-lock / review-signoff scope decision

Per the mission's own guidance ("if the gate cannot represent [pending-review], keep the 08-25
lane out of the gate's scope explicitly and say so"): `coverage_lock.json` and
`review_signoffs.json` are **left untouched** — no `"2026-08-25"` key was added to either. Both
gates key off the JSON files' own version entries, not `matrix.VERSIONS`, so this was already
possible with zero code changes. Rationale: many `merchant_checks.py` checks now auto-attribute
coverage to 2026-08-25 (the same mechanism above), but `review_signoffs.json` has no "pending"
state — locking that coverage in now would mean either fabricating signoffs or running a full
independent adversarial-review pass over every auto-attributed check, which is out of scope for
a register-only build. `gen_coverage_lock.py --check` confirms this is safe and fully reversible
(dry-run: nothing would be dropped; the 3 existing versions' locks are untouched either way).

## Gates run (all pass)

```
register quote-check:        1431/1431 verified, 0 line-warnings, 0 FAILED   (was 953/953 pre-P1)
citation-soundness gate:      PASS — CHK-025 and ORD-001 reviewed-equivalent, all else text-equivalent
coverage-lock gate:            PASS — 689 locked ids, 3 versions, unchanged
review-signoff gate:           PASS — 531 locked CHECK ids, 3 versions, unchanged
register-completeness gate:    PASS — unaffected, still scoped to the 3 existing versions
gen_coverage_lock.py --check:  dry-run — nothing would be dropped (additive-safety confirmed)
```

## Quality verification

**20-row spot-check** (random sample across all 16 areas and all three written dispositions,
seed 20260830): CART-029, CHK-047, IDL-006, CAT-022, OVR-011, PAY-004, TOT-015, CART-017,
IDL-045, SIG-015, ORD-004, TOT-014, SAE-009, DSC-019, IDL-046, TOT-004, DSC-036, SAE-012,
TOT-007, CART-015. Every one independently re-opened at its exact cited path/line (beyond the
automated `verify_register.py` pass) and confirmed verbatim in context — 20/20 clean.

**Kill-check**: deliberately replaced CART-029's quote with fabricated text not present in the
spec. `verify_register.py 2026-08-25` correctly went red (`FAIL CART-029 QUOTE_NOT_FOUND`,
477/478 verified). Restored the original row; gate returned to 1431/1431 verified, 0 failed.

## Other concurrent lanes on this repo (read-only reconciliation, per request — not merged)

Two other worktrees are active on this same v2026-08-25 migration:

- **`lane/0825-newsurface`** (P2, "new surface") — extracts genuinely NEW 2026-08-25
  requirements with no 2026-04-08 antecedent (location, loyalty, split-payments, Actions,
  permalink, authentication, 3DS challenge, device-data-collection, capability-namespace-
  authority, request-constraints, buyer-consent-v2, replay-protection-payload-matching) into
  separate area files under the same `conformance/requirements/2026-08-25/` directory.
  Complementary to this carry-forward pass, not overlapping (disjoint filenames; this pass
  explicitly declined to add new-surface rows — see e.g. DISC-003, PAY-021/026 notes above).
- **`lane/0825-golden`** — builds a reference/golden merchant server for 2026-08-25
  (`conformance/testbed/golden-0825/`), unrelated to the register.

**Source-prefix convention reconciliation** (requested): this lane's rows use the established,
existing-3-versions convention — the literal repo token `"ucp:"` with the actual vendor
directory resolved externally via `verify_register.py`'s `VERSION_TREE` (this pass's P0 change
added the `"2026-08-25": "ucp-2026-08-25"` entry there). The P2 lane's rows instead use a
self-resolving literal alias, `"ucp-2026-08-25:<path>"`, and did not modify `verify_register.py`.

Traced both through `verify_register.py`'s `load_file()`: `root = ucp_dir if repo == "ucp" else
repo`. When `repo == "ucp"` (this lane's convention), `root` comes from the version-keyed
`VERSION_TREE` lookup. When `repo != "ucp"` (P2's convention), `root` is the literal repo string
itself, **bypassing `VERSION_TREE` entirely** — which is exactly why P2 didn't need to touch
`verify_register.py`. Confirmed empirically: ran this lane's (P0-patched) `verify_register.py`
against P2's actual `location.json` (48 rows) — 48/48 resolve cleanly, because
`"ucp-2026-08-25"` the literal string happens to equal the real vendor folder name.

**Recommendation for the merged lane**: standardize on the `"ucp:"` + `VERSION_TREE` convention
(this lane's), for two reasons — (1) it's what all three existing versions already use, so a
single convention holds across all four; (2) the literal-alias convention hardcodes the local
vendor folder's name into every citation string, working today only because
`"2026-08-25"` (the spec version) coincidentally matches `"ucp-2026-08-25"` (the vendor folder
suffix) — a naming coincidence, not a guaranteed invariant, whereas `"ucp:"` stays insulated from
any future vendor-folder rename via the single `VERSION_TREE` indirection point. This is a
find/replace of the repo-prefix string in P2's already-written files, not a re-verification of
their quotes (no other change needed) — worth doing before the two lanes merge, not after.

## Worktree / branch

- Worktree: `~/Documents/Claude/Projects/ucp-conformance/.claude/worktrees/lane-0825-register`
- Branch: `lane/0825-register`
- P0 commit: `342740b` (harness). P1 commit: this directory + citations-gate fix, committed
  alongside this report.
