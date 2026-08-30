# P2-REPORT — v2026-08-25 new-normative-surface extraction

**Lane:** `lane/0825-newsurface` (worktree `.claude/worktrees/lane-0825-newsurface`)
**Scope:** P2 of the v2026-08-25 register build — extract the NEW normative surface
(requirements that did not exist at v2026-04-08) from the vendored release
`conformance/.vendor/ucp-2026-08-25` (tag `v2026-08-25`, commit `cd78fb38e8`).
**Counterpart:** the P1 lane (`lane/0825-register`, worktree
`.claude/worktrees/lane-0825-register`) carries forward the 04-08 rows for the
2026-08-25 version. This lane's scope is strictly text with no 04-08 antecedent;
every area file below was verified absent from `conformance/.vendor/ucp` (the
04-08 pin) before extraction (see "Dedup method").

## Result: 219 rows extracted across 12 area files; 216 net after landing de-dup

**Landing addendum (2026-08-30, extra-high review):** at merge time, three rows
duplicated content already owned by P1's carry-forward files and were deleted —
`CAP-002` (restates `CAP-001` + `CAP-009` combined), `CNST-011` (restates
`DSC-037`'s core clause, narrowed to own it — see that file's own landing note),
and `CNST-015` (restates `DSC-036` verbatim). This lane's own extraction/dedup
method (below) correctly found no antecedent in the 04-08 tree for any of the
three — the collision is with P1's *carry-forward* rows, which this lane's
worktree never had visibility into (P1 and P2 ran as parallel, isolated lanes;
see "Other concurrent lanes" in P1-REPORT.md). The table and the `219`/`219`
verification figures below are the as-extracted counts this lane actually
produced and verified in isolation — left as originally written for an accurate
lane history. The net program-wide count after landing is **216** for this
lane's files, **694** across the full merged 2026-08-25 register (see
`conformance/selfcheck/verify_register.py 2026-08-25` -> `694/694 verified`).
Also at landing: every citation in this lane's 12 files was re-pointed from the
`ucp-2026-08-25:` alias to `ucp:` (see "Filing conventions" below — the
alias choice this lane made was sound at the time but is superseded).

| File | Area | Prefix | Rows (as extracted) | Rows (post-landing) | Named in mission's target list? |
| --- | --- | --- | --- | --- | --- |
| `request-constraints.json` | request-constraints | RC | 26 | 26 | Yes (#655 + #744) |
| `actions.json` | actions | ACT | 14 | 14 | No — swept in (feeds Payment Authentication; wholly new generic mechanism) |
| `payment-authentication.json` | payment-authentication | PAUTH | 20 | 20 | Yes |
| `payment-3ds-challenge.json` | payment-3ds-challenge | TDS | 10 | 10 | Yes (the "3DS" half) |
| `payment-device-data-collection.json` | payment-device-data-collection | DDC | 9 | 9 | Yes (the "device-data-collection" half) |
| `split-payments.json` | split-payments | SPL | 18 | 18 | Partially — carries the concrete `instrument_group`/`allowed_combinations` mechanism; the release delta names "instrument requirements via constraints (d25cce30)" for the *other* mechanism (see Notes) |
| `location.json` | location | LOC | 48 | 48 | Yes (#589), includes amenity.* |
| `loyalty.json` | loyalty | LOY | 18 | 18 | Yes (loyalty registries) |
| `buyer-consent-v2.json` | buyer-consent-v2 | CNST | 19 | 17 (CNST-011, CNST-015 deleted — de-dup) | Yes (the consent surface) |
| `capability-namespace-authority.json` | capability-namespace-authority | CAP | 12 | 11 (CAP-002 deleted — de-dup) | Yes (capability naming/namespace rules) |
| `replay-protection-payload-matching.json` | replay-protection-payload-matching | REPLAY | 3 | 3 | Yes (idempotency/replay, as released) |
| `permalink.json` | permalink | PERM | 22 | 22 | No — swept in (wholly new capability, security-heavy) |
| **Total** | | | **219** | **216** | |

Every row's `quote` was machine-verified against the pinned vendor source with
`conformance/selfcheck/verify_register.py 2026-08-25` (see "Verification" below):
**219/219 verified, 0 line-warnings, 0 failed** (this lane's rows, in isolation,
at extraction time). A further 20-row random sample was re-read against the
source prose by hand for semantic soundness of the `requirement`/`keyword`/
`testability` classification (not just quote presence); no issues found.

## Per-area detail and what's new vs 04-08

- **RC — Request Constraints (26).** `ucp.request_constraints` (#655, response-carried)
  + explicit `path` targeting (#744) + the shared Constraint Expression grammar
  (`common/types/request_constraints.json`, `constraint_expression.json`). Wholly
  new mechanism; zero hits for `request_constraints`/`constraint_expression` in the
  04-08 vendor tree. Also covers `available_payment_instrument.json`'s new
  `constraints` field (RC-007) — the "instrument requirements via constraints"
  wiring named in the release delta's hazard table (commit `d25cce30`): an
  available-instrument declaration can carry a Constraint Expression describing
  itself, reusing the same grammar as `ucp.request_constraints`.
- **ACT — Actions (14).** The generic `## Actions` mechanism in
  `overview/index.md` + `common/types/actions.json`. Not named explicitly in the
  mission's target list, but it is the load-bearing infrastructure Payment
  Authentication is built on (declared Action types, single-use `id` semantics,
  the Actions/Messages boundary), and it is itself 100% new text — no `## Actions`
  section or `outstanding unit of extension-defined work` language exists in the
  04-08 `overview.md`. Swept in per the "walk every NEW doc file" method
  instruction.
- **PAUTH / TDS / DDC — Payment Authentication + its two Action types (20 + 10 + 9
  = 39).** `dev.ucp.common.payment.authentication` declares
  `device_data_collection` and `three_ds_challenge`. Split into three files
  because each has its own dense, independently-testable contract (blocking
  behavior, deadlines, embedded-notification shapes, security requirements), but
  they share one extension and are best reviewed together.
- **SPL — Split Payments (18).** `dev.ucp.common.payment.split_payments`:
  `allowed_combinations` of `instrument_group` (`types`/`min`/`max`), the
  specified-amount/open-amount processing model, and the atomic
  complete-or-void-everything error contract. Wholly new capability.
- **LOC — Location (48, the largest area).** `dev.ucp.common.location.search` +
  `.lookup` (#589): distance/serves spatial relations, `hours`/`exception_hours`
  operating-hours evaluation, the `amenities` map and its reverse-DNS `amenity.*`
  vocabulary/naming rule, the `items` availability filter, batch lookup with
  `inputs` correlation and `batch_limit_applied`. 04-08's
  `shopping/types/retail_location.json` had none of this — no hours, no
  amenities, no spatial relations, no item-availability filter — so essentially
  the entire capability is new text.
- **LOY — Loyalty (18).** `dev.ucp.common.loyalty`: memberships/tiers/benefits/
  rewards, the provisional/verified state machine, `display_id` masking, and the
  three data-minimization MUSTs. Wholly new extension.
- **CNST — Buyer Consent v2 (19).** The 04-08 `dev.ucp.shopping.buyer_consent`
  capability (four flat booleans: `analytics`, `preferences`, `marketing`,
  `sale_of_data` — already registered as `DSC-*` rows in the 04-08
  `discounts-consent.json`, carried forward by P1) is **replaced wholesale** at
  08-25 by a two-level purpose+segment model with `source` attribution,
  `dev.ucp.consent.*` well-known identifiers, and seven numbered normative
  requirements. Filed under a new prefix (`CNST`, not `DSC`) specifically to
  avoid colliding with P1's carry-forward file of the same area name — see the
  merge note in that file's `_note`.
- **CAP — Capability Namespace Authority (12).** 04-08's `Namespace Governance`
  section already existed (naming convention, governance model) and is presumably
  carried forward by P1 under `DISC-*`. This file captures **only** the wholly
  new "Authority Binding" subsection: an exact `schema`-URL-to-namespace
  derivation algorithm (WHATWG URL parsing, label-aligned prefix/exact match,
  public-suffix warning) plus the enforcement rule (no-fetch-on-mismatch,
  no-redirect-following) — replacing 04-08's two-row "Spec URL Binding" table.
  Also the new `capability.json` constraint that `business_schema` now requires
  `schema` (04-08 only required it on `platform_schema`).
- **REPLAY — Idempotency/replay, as released (3, deliberately small).** Per the
  explicit mission instruction, this captures *only* the mismatched-payload
  duplicate-key handling actually in the released `signatures.md` (409/`-32000`
  rejection, SHA-256/Content-Digest payload matching, the platform's
  fresh-key-on-payload-change obligation) — **not** our own pending upstream
  `#782`. The much larger surrounding rewrite of `signatures.md` (EdDSA/Ed25519
  support, Web Bot Auth dual-audience signatures, JWK `OKP` shape) is a separate,
  large new-normative-surface area that is **out of scope for this pass** — see
  "Identified but out of scope" below.
- **PERM — Permalink (22).** `dev.ucp.shopping.permalink`: a wholly new
  browser-GET capability (compact item-path encoding, `continue_to` destination
  preference, JSON-Pointer query-parameter mapping, redirect resolution). Not
  named in the mission's target list, but swept in because it is (a) wholly new
  — no `permalink.md`/`permalink.json` existed at 04-08 — and (b) unusually
  security-dense (open redirect, response-header injection, mass-assignment,
  secret/PII leakage), which the Method instruction's "extract every MUST/MUST
  NOT" directive covers regardless of the target-area list.

## Dedup method

For every candidate area, before extracting a single row: (1) confirmed the
capability/schema/doc file has **no same-named counterpart anywhere** in
`conformance/.vendor/ucp` (the 04-08 pin) via `grep -rli` for the capability
name, schema `$id` fragment, and key vocabulary terms; (2) for areas that
*extend* a 04-08 capability under the same name (buyer-consent,
capability/namespace), diffed the specific subsection against the 04-08
equivalent and extracted only lines with no antecedent. Spot-checked again at
the end of the pass (see the "Confirmed clean" greps in-session) — zero hits in
the 04-08 tree for `request_constraints`, `constraint_expression`,
`instrument_group`, the Actions vocabulary, `device_data_collection`,
`three_ds_challenge`, `split_payments`, `location.search`/`location.lookup`,
`amenity`, `loyalty`, `permalink`, or `authority_prefix`/derivation-algorithm
language.

## Transition-schema deletions — explicitly investigated, zero rows

The mission asked for deletions "as negative requirements only if normative
text states them." Checked: `account_info`, `merchant_fulfillment_config`, and
`retail_location` (the three 04-08 schema files with no 08-25 counterpart,
matching the state-of-ucp page's `fdcf8934`/`6907368b` cleanup-commit citations).
**Finding: zero hits for any of these three names anywhere in the 08-25 vendor
tree** — the release contains no migration note, deprecation notice, or MUST/MUST
NOT sentence about them; they are simply structurally absent. Per the mission's
own instruction, this is a **structural deletion with no normative text to
extract**, so no rows were created for it. This is a finding, not a gap.

## Identified but deliberately out of scope for this pass

Walking the full 08-25 diff surfaced several other large new/heavily-changed
areas not in the mission's named target list and not swept in (to keep this
pass's scope bounded and because none were flagged as priorities):

- **Signature Algorithms / Key Format / WBA Interop** (`signatures.md`): EdDSA
  (Ed25519) support alongside ECDSA, JWK `OKP` key shape, and a whole new
  "dual-audience" Web Bot Auth signature mode (`Signature-Agent` header,
  `tag="web-bot-auth"`, RFC 7638 thumbprint `kid`). Easily 15-25 more rows on
  its own; only its narrow Replay Protection intersection was extracted (REPLAY
  area) per the explicit mission instruction.
- **`## Policies`** (new top-level section in `overview/index.md`, ~166 lines):
  a business-policy-surfacing mechanism (types/targeting/precedence/presentation)
  with no 04-08 counterpart.
- **`## Quantities and units`** (new top-level section in `overview/index.md`):
  the integer-range/unit-vocabulary/ordering-increment model for quantity
  representations.
- **Common-primitives relocation** (#736/#741): dozens of `shopping/types/*.json`
  files moved verbatim to `common/types/*.json` (e.g. `total.json`, `price.json`,
  `payment_instrument.json`, `postal_address.json`). These are **not** new
  normative text — the content is unchanged, only the path/`$id` moved — so they
  are correctly P1's carry-forward job, not P2's. Spot-checked several
  (`total.json`, `postal_address.json`) to confirm no substantive content change
  beyond the path.
- **Transport schema split** (`transports/jsonrpc.json`, `mcp_tool_call.json`,
  `a2a_message.json`, `embedded_message.json`): appear to be extractions of
  envelope shapes previously defined inline in the OpenRPC/OpenAPI service
  documents rather than new normative content; not deeply investigated given
  time budget — flagged for whoever picks up a P2b pass.

## Filing conventions for the merge step

- New-area files (RC, ACT, PAUTH, TDS, DDC, SPL, LOC, LOY, PERM, REPLAY) use
  fresh area names with no 04-08 counterpart, so they can be dropped into the
  merged `conformance/requirements/2026-08-25/` directory as-is alongside P1's
  carry-forward files with no name collision.
- **CNST** (`buyer-consent-v2.json`) and **CAP**
  (`capability-namespace-authority.json`) extend capabilities that already have
  a same-named 04-08 area file (`discounts-consent.json`, `discovery.json`).
  They are deliberately filed under **different filenames and ID prefixes**
  (`CNST-*`, `CAP-*`, not `DSC-*`/`DISC-*`) so they don't collide with or
  silently overwrite P1's carry-forward of those files. Each file's `_note`
  field states this explicitly. The person doing the final merge should decide
  whether to fold `CNST-*`/`CAP-*` into the corresponding P1 file (renumbering
  to continue that area's sequence) or keep them as satellite files — both are
  functionally equivalent for the tester, since the build step merges all
  `*.json` files in a version directory regardless of filename.
- All new rows cite sources as `ucp-2026-08-25:<path>#L<n>` (using the vendor
  **directory name** as the repo alias directly), not `ucp:<path>#L<n>`. This
  matters for tooling: `conformance/selfcheck/verify_register.py`'s
  `VERSION_TREE` dict only maps `ucp` → the 04-08 tree for versions
  `2026-04-08`/`2026-01-23`/`2026-01-11`; it has no `2026-08-25` entry, so any
  row citing `ucp:...` for this version would resolve against the **wrong**
  (04-08) vendor tree and false-FILE_MISSING or false-QUOTE_NOT_FOUND. Using the
  `ucp-2026-08-25:` alias bypasses `VERSION_TREE` entirely (the checker's
  `load_file` treats any repo name other than the literal string `"ucp"` as the
  vendor directory name directly), so **no code change to `verify_register.py`
  was needed or made** — this is purely a citation-format choice, verified
  working end-to-end (219/219 pass) with the existing checker.
  **Superseded at landing (2026-08-30):** P1's own report flagged that both
  aliases "resolve correctly today... but only by coincidence of the local
  vendor folder's name" and recommended standardizing on `ucp:` when the lanes
  merge. The landing-normalization commit did exactly that — added `"2026-08-25":
  "ucp-2026-08-25"` to `verify_register.py`'s `VERSION_TREE` and rewrote all 12
  of this lane's files from the `ucp-2026-08-25:` alias to `ucp:`, so 2026-08-25
  now resolves the same deliberate way every other version does, not by
  coincidence. Re-verified 697/697 immediately after the rewrite, before the
  de-dup above dropped the register to 694/694.

## Verification

```
$ python3 conformance/selfcheck/verify_register.py 2026-08-25
register quote-check: 219/219 verified, 0 line-warnings, 0 FAILED
```

(Run from the worktree with `conformance/.vendor` symlinked to the main
checkout's vendor cache — the `.vendor` trees are gitignored/untracked so a
fresh `git worktree add` doesn't materialize them; the symlink itself is also
gitignored and was not committed.)

## The 10 most consequential new MUSTs

1. **CAP-004/005/006/007 — the Authority Binding derivation algorithm.** Closes
   a real spoofing vector: a naive "does the schema URL contain the namespace
   string" check accepts `https://ucp.dev@evil.example/x.json` (host is
   `evil.example`) or `com.examplecorp.*` matching a `com.example` prefix. The
   new algorithm mandates WHATWG URL parsing, no-userinfo, label-reversal, and
   an *exact-or-label-aligned-prefix* match — both worked "reject" cases are
   spelled out in the spec's own table.
2. **CAP-009/010 — platforms MUST NOT fetch a mismatched `schema` URL and MUST
   NOT follow redirects when fetching it.** Turns the derivation algorithm from
   documentation into an enforceable SSRF/spoofing gate; the no-redirect rule
   specifically closes a same-origin-looking-URL-that-3xx's-elsewhere bypass.
3. **PERM-010/011 — `continue_to` open-redirect validation + `Location` header
   injection prevention.** A five-step mandatory validation (single
   percent-decode, same-origin-after-resolution, no CR/LF in the emitted
   header) on a field that is, by construction, unauthenticated attacker-
   controlled input on every permalink click.
4. **PERM-020 — a permalink URL MUST NOT contain payment credentials, tokens,
   session cookies, AP2 mandates, API keys, or one-time secrets.** Permalinks
   are logged, cached, screenshotted, and forwarded through referrers by
   design; this is the one line standing between the new capability and a
   credential-leak-by-design defect.
5. **REPLAY-001/002/003 — mismatched-payload idempotency-key handling.** At
   04-08, replaying an idempotency key always returned the cached response
   regardless of body content. 08-25 requires businesses to hash-compare the
   body and **reject** (409/`-32000`) a same-key-different-body request, and
   requires platforms to mint a fresh key on any payload change — closing a
   request-smuggling-adjacent class of bug where a modified retry could
   silently execute under a stale idempotency guarantee.
6. **SPL-008/009 — split-payment atomicity.** "the business MUST void or
   reverse any authorizations it made" + "the buyer MUST NOT remain charged for
   an incomplete split" is the entire financial-correctness guarantee for a
   brand-new multi-instrument payment capability; a violation here is a direct
   consumer-harm bug (double-charge / unrefunded partial authorization).
7. **RC-020 — a Business that emits `ucp.request_constraints` MUST enforce
   every constraint it emits against the next request.** This is what makes
   Request Constraints a binding contract rather than a decorative hint —
   without this MUST, a platform could reasonably treat advertised constraints
   as optional.
8. **DDC-008/009 — the device-data-collection surface MUST NOT receive
   Platform credentials or access Platform storage, and provider payloads MUST
   NOT be copied into the Action, notification params, Checkout, or payment
   instrument.** A third-party (provider-operated) origin is mounted inside
   the Platform's own checkout surface by design; these two MUSTs are the
   entire isolation boundary preventing that origin from exfiltrating Platform
   state or injecting device-fingerprint data into UCP's protocol objects.
9. **CNST-013 — "Businesses MUST ignore purposes and segments in a request
   that were not advertised" / "Platforms MUST NOT prompt for or transmit
   purposes or segments the business did not advertise."** The authoritative-
   set boundary that keeps the new consent model's privacy semantics sound —
   without it, a platform could invent a consent purpose the business never
   disclosed, or a business could silently honor an unadvertised opt-out
   signal it never committed to enforcing.
10. **PAUTH-006/007 — while `complete_in_progress` the Platform MUST NOT start
    another Update/Complete operation, and a Payment Authentication Action
    occurrence is single-use (MUST NOT be processed twice, including after
    `action.done`).** This is the concurrency/replay guard for the entire new
    3DS-challenge / device-data-collection flow sitting directly on the payment
    path; without it a double-submit or duplicate notification could double-
    trigger a payment attempt.
