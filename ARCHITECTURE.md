# ARCHITECTURE — code map, the projection principle, and the deploy/release order

last-reviewed: 2026-09-11

## Code map (what lives where, and what may write what)

| Path | What it is | Writes |
|---|---|---|
| `conformance/requirements/<version>/` | The requirement registers: one row per normative MUST/MUST NOT of a pinned spec version, quoted verbatim at `path#line`. Data, add-only. | humans (reviewed like code) |
| `conformance/common/` | Version map (`spec_versions.VERSIONS`), shared helpers. The ONE list of supported versions. | humans |
| `conformance/checks/` | The engine: `MCheck` rows (`merchant_checks*.py`, `area_*.py`), `engine.py` (fetch, kill-by-mutation, served-version scoping), `merchant.py` (the CLI / Action entry point), the 08-25 struct/golden checks. | humans |
| `conformance/coverage/` | Accounting: `matrix.py` (CHECK / EXEMPT / GAP per version → `public/coverage.json`), `evidence.py` (evidence classes, fail-closed), `coverage_gate.py`, locks and ratchets. | `matrix.py` → `public/coverage.json`, `docs/spec-coverage-matrix.md` |
| `conformance/agent/` | The agent lane: agent checks, reference agent with modeled defects, `agent_matrix.py` → `public/agent-coverage.json`, governance. | `agent_matrix.py`, `run_agent.py --record` |
| `conformance/checks-pending/<topic>/` | Reference test systems for normative text proposed upstream but not yet merged (mock actors, a reference implementation with a defect switch, checks, a kill test). Invisible to both lanes and to every published number; register rows for them sit in `requirements-drafts/next/` marked proposed. Promotion protocol in its README. | humans |
| `conformance/selfcheck/` | Gates over the engine itself (register verification, evidence classes, kill sets, dormancy, oracle divergence, mutation proxy). | gate outputs only |
| `conformance/fixtures/merchant/` | The controlled fixture (three versions), the known-good server the kill-rate proofs run against. | — |
| `conformance/testbed/golden-0825/` | Our own 2026-08-25 golden with a defect-injection seam and battery. | `battery/LAST_RUN.json` only via `--record` |
| `conformance/ci/` | `run_suite.py` (the gate table — the single list of what CI runs), `selftest.sh`, site gates (`site_gates.py`), validators, `ports.json` (every port the harness may bind), `known_issues.json` (the single KNOWN ISSUES source). | gate outputs; generated pages via `conformance/web/gen_*.py` |
| `conformance/web/` | Site generators and registers: `site_requirements.json` (what the site must do, add-only), `doc_claims.json`, `preview_parity.json` (the preview id map) + captures, `gen_check_docs.py`, `gen_known_issues.py`, `gen_preview_ids.py`, `sync_site_claims.py`. | `public/checks/`, `public/known-issues.*`, `functions/api/preview_ids.js`, `public/site_claims.json` derived blocks |
| `public/` | The static site (hand-authored pages + generated pages + JSON exports). Every number on a page is bound with `data-live` to an export or registered in `site_claims.json`. | generators above; humans for hand-authored copy |
| `functions/api/` | Cloudflare Pages Functions: the instant check (`conformance.js`), the badge, save/track. The preview is mapped 1:1 to engine checks by the id map. | `gen_preview_ids.py` (projection) |
| `packaging/` | The pip package (`spck_conformance/_bundle` is a synced copy of the engine), `deploy.sh` (the ONLY deploy path), `release_rc.sh`, `release_guards.sh`, `preflight.sh`, kill-tests for all of them. | `sync_bundle.sh` → `_bundle/` |
| `tests/web/` | node:test suites for the Pages functions and the DOM behaviour of the static pages (no dependencies) + the browser smoke. | — |
| `docs/` | Hand-authored methodology docs; `docs/archive/` holds superseded documents with dated headings. | humans |
| `ops/` (symlink, private repo) | Ledgers, plans, filings, the deploy log, HANDOFF. Never required by any public gate: a gate that needs it reports an honest SKIP (exit 2) when it is not mounted. | owner |

## The projection principle

One source per fact; everything else is a **projection** that is regenerated in memory and
byte-compared in CI, never hand-edited:

| Source | Projection | Byte-compare gate |
|---|---|---|
| registers + engine | `public/coverage.json`, `docs/spec-coverage-matrix.md` | `coverage` |
| engine (`MCheck` rows) | `public/site_claims.json` `manifest` + `evidence.per_version` | `site-claims-sync` |
| `public/coverage.json` | `public/checks/*.html` | `site-checkdocs` |
| `conformance/ci/known_issues.json` | `public/known-issues.json`, `public/known-issues.html`, `KI-*` claims | `known-issues-page`, `site-checkdocs` |
| `conformance/web/preview_parity.json` | `functions/api/preview_ids.js` | `preview-parity` |
| engine source tree | `packaging/spck_conformance/_bundle/` | `package-bundle`, `deploy.sh` step 1 |
| a run (captures, verdicts) | `expected.json`, `LAST_RUN.json`, `agent_run_evidence.json` | frozen only by an explicit `--record`; CI proves freshness in-run and never rewrites a tracked file (decision 24) |

A public number that changes outside its gate is a defect (decision 6 / 34): the site gates
(`site-claims`, `site-freshness`, `docclaims`, `voice`) red on any sentence or count they cannot
trace to a source or a registered, dated, reviewed claim.

## Deploy ordering (`packaging/deploy.sh`, the only path)

1. **tree** — clean working tree; pip bundle in sync.
2. **exports** — every projection above regenerates byte-identical (`git diff --exit-code public/`).
3. **gates** — coverage, evidence-class, agent-governance, every `site_gates.py` mode, known-issues, preview-parity.
4. **provenance** — `HEAD == origin/main` and a completed-success `selftest` check-run exists for that SHA (any run for the SHA; a newer in-progress run never masks a green one — `packaging/check_run_verdict.py`).
5. **preview** — `wrangler pages deploy --branch=preview-<sha7>` and a smoke fetch of `/coverage.json`.
6. **main** — deploy `main`, append `ops/DEPLOY_LOG.md`.

Any refusal exits 3 and nothing is deployed; `--dry-run` runs 1–4 for real. The release path
(rc first, clean-venv acceptance, CHANGELOG, `--final`) is documented in README "Releasing" and
guarded by `release_guards.sh` in `release.yml`.

## Gate table

`conformance/ci/run_suite.py` is the single list of gates CI runs (`python3 conformance/ci/run_suite.py --only <name>`
runs one; `conformance/ci/selftest.sh` runs them all with the goldens booted). Every row of
`conformance/ci/README.md` must name a gate in that table (checked by `site-docclaims`), so the README
never describes a gate that does not exist.
