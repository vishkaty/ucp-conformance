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

## Where we are (2026-07-02)

47 kill-rate-validated merchant checks + schema-oracle and version-suite checks.
Two goldens: official Flower Shop (2026-01-23) and the controlled fixture
(oracle-validated; serves 2026-04-08 AND 2026-01-23, full checkout/order/payment/
discount lifecycle incl. automatic + item-level discounts and AP2 fields).

| Version | MUSTs | CHECK | EXEMPT | GAP (testable / needs-harness / manual) |
|---|---|---|---|---|
| 2026-01-11 | 172 | 57 | 0 | 115 (40 / 45 / 30) |
| 2026-01-23 | 181 | 70 | 0 | 111 (35 / 45 / 31) |
| 2026-04-08 | 322 | 35 | 0 | 287 (143 / 105 / 39) |

(Live numbers: [spck.dev/coverage](https://spck.dev/coverage) — this table is a
snapshot; the page and `public/coverage.json` are the enforced source of truth.)

Largest 04-08 gap areas: identity-linking 55 (47 need OAuth), catalog 24 testable,
signatures 31, discounts 18 testable, payment/checkout/order ~15 testable each,
error-envelope 25.

## Phases (each ends by raising the ratchet + a matrix milestone)

### Phase 1 — 2026-04-08 testable grind (~143 MUSTs, biggest single win)
The controlled fixture already serves 04-08 with catalog/cart/checkout/order/discount,
so most of these need only check implementation, not new infrastructure:
catalog (24) · discounts (18) · signatures-testable (16) · payment (15) · checkout (14)
· error-envelope (12; 04-08 error schemas are leaf schemas the oracle resolves as-is —
unlike the 01-23 root-schema blocker) · order (11) · identity-testable (8) · totals (6)
· cart (5) · discovery (5) · version-negotiation (4) · signals-testable (4).
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
Classify the manual residue (31/33/40 rows) into `coverage/exemptions.json` with a
written why-unprovable per row (`class`: real-world-act / human-perception /
subjective-judgment / out-of-band-legal). The coverage gate already machine-validates
them. Anything that turns out testable during classification goes back to Phase 1-3.
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

## Maintenance invariants (standing)
- Every change: full local `conformance/ci/selftest.sh` GREEN before commit; CI GREEN
  after push; bundle re-synced (`packaging/sync_bundle.sh`, CI-enforced).
- Coverage artifacts regenerated together:
  `python3 conformance/coverage/matrix.py --json public/coverage.json --md docs/spec-coverage-matrix.md`
  (the coverage gate fails if you forget).
- Ratchet floors only go up (`conformance/coverage/ratchet.json`).
- No giant unsupervised automation; supervised, bounded, gated increments.
