# PRINCIPLES — the seven rules every durable change is held to

last-reviewed: 2026-09-11

These are pointers, not prose: each principle names the mechanism in THIS repository that
enforces it, so a reader can go from the rule to the red build it produces. The full statements
(with the design rationale) are §Principles of the program plan in the private ops repo
(`ops/PLAN-0825-conformance-master-2026-08-30.md`); PLAN-v3 restates P-1 and P-2 as standing rules.

| # | Principle | What it means here | Enforced by |
|---|---|---|---|
| P-1 | **No vacuous green.** A check that cannot fail is not a check. | Every check ships with planted-mutant kill-tests; every gate ships with a selftest that plants a violation and requires red; every task follows failing-first → implement → kill-proof. | `merchant*` kill-rate gates, `killset-lock`, `*-selftest` gates, `run_suite.py` |
| P-2 | **Fail-noisy self-expiry.** Every acknowledgement, allowlist, skip, waiver or intermediate mode dies loudly when stale. | Every such entry carries `review_by` (30 days where truth depends on moving upstream state, 90 where it depends only on the spec pin) and, where applicable, `spec_pin`; the clocks are registered and swept. | `expiry-clocks`, `known-issues`, `site-claims`, `sources-age`, `dormancy` |
| P-3 | **Verbatim anchored citations.** Every requirement row quotes the released text at a read-verified `path#line` at a pinned SHA. | The requirement registers under `conformance/requirements/<version>/` are quote-verified against the vendored pin; a moved quote is a red build. | `register`, `register-selftest`, `citations`, `SOURCES.lock.json` |
| P-4 | **Evidence honesty.** Evidence classes are published, never conflated. | Each check's class is derived mechanically from how it runs and what it reached; the site cannot render a claim the coverage data does not carry. | `evidence-class`, `coverage`, `site-freshness`, `site-claims` |
| P-5 | **Fixture-circularity defense.** Checks are validated against independently-authored targets; where none exists the ceiling is named, not papered over. | Differential runs against the official samples; self-referenced checks are labeled as such; the preview is graded 1:1 against the engine on frozen captures. | `differential`, `reach-selftest`, `preview-parity`, `merchant` |
| P-6 | **Deterministic where it matters; bounded honest exits everywhere.** A lane that cannot complete lands its largest honest subset with a truthful status. | Verdict vocabulary stays deviation / advisory / not-tested / inconclusive — a timeout never becomes a pass or a fail; every gate exits 0/1/2 with 2 = honest skip. | `verdict`, `cli-summary`, `run-suite-only` |
| P-7 | **Simplicity — fewest moving parts, config over code.** | One source per number; projections are generated and byte-compared, never hand-edited (see ARCHITECTURE.md "projection principle"); registers are data. | `coverage`, `site-checkdocs`, `known-issues-page`, `site-claims-sync`, `package-bundle` |

Two standing rules sit beside the seven: **TDD + kill-proof per durable change** (P-1 applied to
process) and **pins only at merged SHAs or tags** (the source lock). Both are checked, not trusted:
the first by the failing-first commits in every lane's landing note, the second by `sources-age`.
