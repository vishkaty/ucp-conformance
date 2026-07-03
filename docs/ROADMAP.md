# Roadmap — to 100% accounted UCP coverage, kept honest by machine

**Live status: [spck.dev/coverage](https://spck.dev/coverage)** (generated from
`public/coverage.json`; the `coverage` CI gate fails the build if that data goes stale,
if coverage regresses, or if the site's advertised numbers drift from reality).

## Definition of done

For each pinned spec version (2026-01-11, 2026-01-23, 2026-04-08), **every normative
MUST/MUST NOT is accounted** as exactly one of:

| Bucket | Meaning | Machine enforcement |
|---|---|---|
| **CHECK** | a shipped conformance check that clean-passes AND kills 100% of its injected defects on a known-good golden | reference gates + kill-rate gate |
| **EXEMPT** | irreducibly manual (real-world act / human perception / subjective judgment) with a written justification | coverage gate validates reason + class + no double-booking |
| **GAP** | not yet accounted — the number we drive to zero | ratchet forbids it growing |

Done = `matrix.py --require all` green for all three versions, at which point that
command joins CI permanently so coverage can never silently rot.

## The accuracy architecture (what "verified" means here)

Nothing counts unless it survives this chain — every link is a CI gate, red on failure:

1. **Pinned sources** — `SOURCES.lock.json` pins the official spec/schemas/samples by
   commit SHA. Never tested against moving branches.
2. **Quote-verified registers** — 896 requirement rows across 3 versions, each carrying
   a VERBATIM quote re-verified against the pinned spec on every run (`register` gate).
3. **Adversarial design** — each check spec is skeptic-reviewed before implementation
   (WF#1 vetted backlog pattern); ids that mean different things across versions are
   version-scoped (`MCheck(versions=…)`) so a check never grades the wrong spec.
4. **Reference-gated on known-good goldens** — a check must clean-pass on a golden
   (official Flower Shop sample, or our controlled fixture whose every response is
   validated by the official `ucp-schema` Rust oracle) AND be kill-safe (all injected
   mutants caught). Gates: `merchant`, `merchant-catalog`, `merchant-ctrl-01-23`,
   `killrate`, `fixture`, `schema`.
5. **Coverage accounting is itself gated** — the `coverage` gate enforces: published
   matrix data is fresh (byte-exact), accounted counts never decrease (ratchet),
   exemptions are real+justified+not double-booked, and site copy states the true
   check count.
6. **Regular re-verification** — full selftest on every push/PR touching
   `conformance/**` or `public/**`, plus a weekly scheduled run (Mon 06:17 UTC) to
   catch environmental rot, plus the pip-bundle drift guard.

## Where we are (2026-07-02, post catalog grind)

58 kill-rate-validated merchant checks + schema-oracle and version-suite checks.
Two goldens: official Flower Shop (2026-01-23) and the controlled fixture
(oracle-validated; serves 2026-04-08 AND 2026-01-23, full checkout/order/payment/
discount lifecycle incl. automatic + item-level discounts and AP2 fields, plus
catalog search/lookup/get_product with cursor pagination, dedup, batch cap and a
configurable product).

| Version | MUSTs | CHECK | EXEMPT | GAP | accounted |
|---|---|---|---|---|---|
| 2026-01-11 | 178 | 117 | 38 | 23 | **87%** |
| 2026-01-23 | 188 | 125 | 39 | 24 | **87%** |
| 2026-04-08 | 364 | 234 | 81 | 49 | **87%** |

(Live + enforced: [spck.dev/coverage](https://spck.dev/coverage) / `public/coverage.json`.)

> **Denominator correction (2026-07-03).** A new `register-completeness` gate reconciled
> every mandatory keyword in the pinned prose against the register and surfaced a genuinely
> incomplete denominator — most notably the **2026-04-08 ap2-mandates section had zero rows**,
> plus missing MCP/A2A transport bindings and several signature/identity/overview clauses.
> 51 rows were added (all quote-verified), so the MUST counts grew (323→364 for 04-08) and
> the honest percentage moved from 93/88/88 to **87/87/87**. Higher denominator, truer number.
> The newly-surfaced GAP is mostly a needs-receiver / MCP-A2A-transport tail (no harness yet).

**State of play (2026-07-03, after 3 parallel waves + 3 adversarial reviews + a
spec-truth citation gate + a completeness reconciliation).** 193 kill-rate-validated
checks across 3 controlled goldens + Flower Shop; 30 CI gates (incl. register-completeness,
review-signoff, and a differential harness vs the independent Flower Shop). The remaining
GAP is the categorized residue, honestly:
- **Documented spec bugs (won't fake):** ERR-008 / CHK-039 (`severity: escalation`
  not in the enum — fixed on upstream main #216) and FUL-017 (prose `total` vs
  schema `totals`). Drafts in `ops/upstream-reports.md`.
- **Genuinely impossible black-box:** IDL-025 (opaque-token aud/azp claims),
  SIG-009/023 (rotation-grace / retention time windows), SIG-024 (fail-closed on
  storage fault), SIG-022 (client key entropy) — no single observed response can
  prove these of a business under test.
- **needs-receiver scenario tail:** mostly the 01-era AP2 mandate-VERIFICATION
  family (PAY-022/024/025/028/030-034) needing an AP2-negotiation fixture, plus
  a handful of 04-08 rows (some schema-convertible: CART-030, ERR-034). Buildable,
  but high-effort / low marginal value.
(Live numbers: [spck.dev/coverage](https://spck.dev/coverage) — this table is a
snapshot; the page and `public/coverage.json` are the enforced source of truth.)

Largest 04-08 gap areas: identity-linking 55 (47 need OAuth), signatures 31,
error-envelope 25, payment/checkout/order ~15 testable each.
**Catalog and discounts-consent are fully accounted for the testable tier**
(27/32 and 23/29 CHECK; the remainder is manual/client-bound/needs-receiver).

## Phases (each ends by raising the ratchet + a matrix milestone)

> **Status 2026-07-03 — Phases 1–4 substantially COMPLETE.** Phase 1 (04-08 testable
> tier) is CLOSED and CI-guarded (`matrix --require testable --version 2026-04-08`).
> Phase 2 harnesses all built (TLS incl. sub-1.2 negative, webhook receiver + RFC 9421
> verification, RFC 9421 request/response signing, OAuth 2.0 + PKCE). Phase 3 (2026-01-11
> fixture mode) done — three controlled goldens run in CI. Phase 4 exemptions
> classified (version-scoped, multi-class). Result: **87 / 87 / 87% accounted** (against a
> now-completeness-gated denominator). What remains is the categorized residue in "State of
> play" above plus the newly-surfaced needs-receiver / MCP-A2A tail, not a phase.

### Phase 1 — 2026-04-08 testable grind (~143 MUSTs, biggest single win)
The controlled fixture already serves 04-08 with catalog/cart/checkout/order/discount,
so most of these need only check implementation, not new infrastructure:
~~catalog (24)~~ **DONE 2026-07-02** (fixture gained get_product + cursor pagination +
dedup/batch-cap/input-validation; 11 new MChecks + schema_check_04_08.py request-side
checks; CAT-034 reclassified client-bound) · ~~discounts (18)~~ **DONE 2026-07-02**
(merchant_checks_04_08.py version-locked behavioral checks + strict-subtree/resolver-
level schema checks; preceded by the per-version CITATION RECONCILIATION — req_ids_map
+ matrix introspection — after finding the 04-08 registers renumbered CHK/DSC/ORD/PAY
ids) · signatures-testable (16)
· payment (15) · checkout (14) · error-envelope (12; 04-08 error schemas are leaf
schemas the oracle resolves as-is — unlike the 01-23 root-schema blocker) · order (11)
· identity-testable (8) · totals (6) · cart (5) · discovery (5) · version-negotiation (4)
· signals-testable (4).
**Acceptance:** 04-08 accounted ≥ 55%; every new check reference-gated; ratchet raised.

### Phase 2 — harnesses (converts the needs-* tiers, all versions)
1. ~~**TLS 1.3 harness** (CHK-051)~~ **DONE 2026-07-02**: tls_proxy.py (1.3-only golden
   + 1.2-accepting negative, boot-minted cert) + version-scoped check counting for
   01-23 AND 01-11 + dedicated soundness gate. WF#1 backlog: 16/16 complete.
   Real-world pull for the next harness: production luma.gift answers 401
   agent_signature_required even for reads — a working RFC 9421 signer is both
   coverage AND the key to testing signature-gated merchants.
2. **Webhook receiver extension** (`webhook_harness.py` exists with ORD-012/013):
   order/payment/checkout event MUSTs — 35 @01-23/01-11, ~43 @04-08.
3. **RFC 9421 signature harness**: request-signature generation + verification checks
   (signatures area: 31 MUSTs @04-08, 15 of them need a receiver).
4. **OAuth identity-linking harness**: authorization-code flow driver against the
   fixture (extend fixture with the OAuth endpoints) — 10 @01-23/01-11, **47 @04-08**.
**Acceptance:** needs-receiver + needs-oauth gaps < 10 per version; ratchet raised.

### Phase 3 — 2026-01-11 fixture mode + long-tail testables
01-11 uses an older envelope generation (ucp.capabilities is an ARRAY; the profile def
is `discovery_profile`): add an 01-11 renderer to the fixture + `validate_profile`
support, boot it as a third golden, then close the remaining 01-11/01-23 testables
(~40 each, largely shared). Extend `matrix.py` attribution so one file can count for
both 01-11 and 01-23 where requirement text is identical (verified per id, like
FUL-026 was).
**Acceptance:** `matrix --require testable` green for 01-11 and 01-23.

### Phase 4 — documented exemptions + the 100% gate
Classify the manual residue into `coverage/exemptions.json` with a written
why-unprovable per row (`class`: real-world-act / human-perception /
subjective-judgment / out-of-band-legal / client-bound [binds the platform/agent,
unobservable from the business side] / spec-authoring [binds ecosystem document
authors]). The coverage gate machine-validates them. **First pass DONE 2026-07-02
(parallel wave 1): 34 exemptions (12/12/27 per version); 15 manual rows REFUSED as
actually machine-testable (returned to Phases 1-3); 18 more blocked on version-scoped
exemption support (the 04-08 renumbering makes id-keyed exemptions unsafe) — schema
extension pending.** Anything that turns out testable during classification goes back to Phase 1-3.
**Acceptance:** `matrix --require all` green per version → added to CI permanently.

### Known blockers (tracked, not forgotten)
- ~~**ERR-002/003/004**: root-schema validation~~ **RESOLVED 2026-07-02**: the oracle
  validates root schemas fine via `--schema` WITHOUT `--def` (the limitation only
  applied to `--def` mode). All three shipped as kill-safe checks
  (`schema_oracle.validate_root`). 01-11 replication lands with Phase 3.
- **Pre-04-08 extension composition**: oracle composes by `$defs[<capability name>]`
  (04-08 convention); 01-23 extension schemas use bare `checkout`. Workaround in use:
  extension subtrees anchored directly to their named $defs.

## Web layer (under the harness since 2026-07-02)
The website (Pages functions + /tool SPA) has its own committed regression net inside
the same selftest: `web-unit` (node:test vs the real modules, mocked KV/fetch) and
`web-browser` (headless Chromium driving /tool against the controlled fixture). Extend
these when touching the web layer — a red gate blocks the push like any engine change.

## Beyond coverage (product usefulness track)
- **MCP transport depth**: checkout/cart/order over MCP (catalog done); embedded
  transport survey.
- **Web front door**: keep the honest preview subset; consider a queued full-suite
  run service (Python engine server-side) — only with rate-limits + explicit consent.
- **Continuous monitoring** (operator tier): scheduled re-runs of a merchant's
  conformance + drift alerts — funds the free dev surface.
- **New-version playbook**: when UCP ships a new version — pin SHA in SOURCES.lock →
  extract + quote-verify register → matrix picks it up automatically (add to
  `VERSIONS`) → fixture version mode → grind. Target: first accounted matrix within
  days of a release.

## Agent-side conformance — the two-sided offering (NEW workstream)

UCP is two-sided: ~half the normative MUSTs bind the **business/merchant** (shipped:
193 checks, 87/87/87) and ~half bind the **platform/agent** (the client). The merchant
suite tests the merchant side; the agent side (95 platform/agent MUSTs at 2026-04-08,
~34 reverse-testable) is currently documented as exemptions. New workstream builds the
**reverse harness**: an agent points at our sandbox, we grade its client-side behavior
(sends `UCP-Agent`, verifies the business's RFC 9421 signature, validates `iss` for
mix-up, OAuth2/PKCE, follows `continue_url` on escalation…). Same kill-rate TDD loop,
against a **reference agent** golden + defect-injected mutation agents.

**Isolation is structural:** the agent tree (`conformance/agent/`) is invisible to the
merchant coverage_map (non-recursive glob) → merchant numbers cannot move; a
`merchant-stability` gate is the belt-and-suspenders. Separate coverage axis
(`agent_matrix.py`), separate lock/gates.

- **Phase A — DONE:** isolated tree, reference agent + mutation harness, agent coverage
  axis (denominator 48/51/95), `run_agent` reference-gate/kill-rate lane, and the
  `merchant-stability` safety net; wired into CI (32/32 green), merchant byte-identical.
- **Phase B (next):** rounded P0 slice — UCP-Agent + signing, business-signature
  verification, identity/OAuth/PKCE/`iss`-mix-up, escalation-follow. Then invite agents
  to test (build-first).
- **Phase B′:** reference implementations for dogfood + showcase — a Shopify store
  (Shopify provides UCP; cheap real differential target) + the reference agent hardened
  into a real "find & buy" agent. Stripe test mode; selling decoupled.
- **Phase C→D:** breadth (payment/AP2 mandates, order/webhooks, rendering) → 100% agent
  accounted, `agent_matrix --require all` in CI.
- **Phase E (demand-gated):** cross-protocol (ACP/AP2); revenue products if pull.

Positioning: *"the conformance layer for agentic commerce"* — both sides, proven. Full
plan + DD: `ops/agent-conformance-plan.md`, `ops/agent-conformance-dd.md`.

## Maintenance invariants (standing)
- Every change: full local `conformance/ci/selftest.sh` GREEN before commit; CI GREEN
  after push; bundle re-synced (`packaging/sync_bundle.sh`, CI-enforced).
- Coverage artifacts regenerated together:
  `python3 conformance/coverage/matrix.py --json public/coverage.json --md docs/spec-coverage-matrix.md`
  (the coverage gate fails if you forget).
- Ratchet floors only go up (`conformance/coverage/ratchet.json`).
- No giant unsupervised automation; supervised, bounded, gated increments.
