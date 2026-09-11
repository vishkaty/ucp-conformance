# CI / TDD infrastructure — "the test suite for the test suite"

A conformance suite that can silently mis-grade is worse than none. So this suite
tests *itself*: on every change we bring up a known-good golden server and run a set
of gates that fail loudly if a check, the register, or the engine loses soundness.

## The gates (`run_suite.py`)

| gate | what it proves | anchor (not written by us) |
|---|---|---|
| `register` | every register row quotes the pinned spec **verbatim** | official spec text |
| `register-selftest` | the `register` gate's duplicate-pair (same quote + source + keyword) and manual-but-CHECK detectors hold on synthetic rows (D2-02) | — |
| `merchant-checks-selftest` | every MCheck id is unique across the whole merchant check set (reach_report / probe-hygiene key by check id) | — |
| `carry-forward` / `carry-forward-selftest` | every 2026-04-08 register row is accounted exactly once in 2026-08-25 (`requirements/2026-08-25/carry_forward.json`: carried / renamed / reworded / dead with a mechanical dead-proof by search term / merged into a surviving row / downgraded); a carried row's quote is normalized-identical or carries `lineage.drift_note`; the D2-15 LOY backport is checked in reverse (D2-09) | pinned spec trees |
| `should-census` / `should-census-selftest` | SHOULD / SHOULD NOT / RECOMMENDED / NOT RECOMMENDED occurrences in the pinned prose, and how many of those lines sit under a register row's verbatim quote — REPORT-ONLY (decision 8: the airtight claim is MUST-class only); the totals feed `coverage.json` `surface.should`, byte-compared by `coverage` and pinned by `evidence-class` (D2-10) | pinned spec text |
| `done2-selftest` | `coverage/done2_status.py` (the DONE-2 definition, PLAN-v3 §1 items 1–12, as one report — a program-review artefact, not a gate) provably reds items 2 and 8 on scratch inputs (D2-16) | — |
| `roles-selftest` | the register `role` seed/heuristic (`requirements/tools/assign_roles.py`) reproduces its 7-row fixture: agent-lock / not-agent-bound / client-bound / subject / direction resolve, handler and platform-but-unlocked rows queue (D2-08); the roles themselves — field, enum, agent-lock consistency both ways, empty review queue, one-lane rule, `_area_capabilities.json` as register data — are gated by `register` | — |
| `expiry-clocks` / `expiry-clocks-selftest` | every entry of every clocked register (expiry_registers.json) carries `review_by` (not past) and `spec_pin` (matches the lock); a re-pin invalidates every review made against the old pin (D2-04/D2-19) | SOURCES.lock.json |
| `verdict` | the no-false-green verdict gate's own unit tests hold | — |
| `schema` | our schema checks agree with the official validator | official `ucp-schema` binary |
| `dual-oracle` | every schema check runs the Rust oracle **and** an independent Python jsonschema referee (full `$id` registry over all 78 schemas); verdict divergence alarms. Known oracle bugs (ucp-schema#43) are acknowledged + self-expiring | independent `jsonschema` engine |
| `dual-oracle-killtest` | the divergence detector provably catches a **planted** divergence + a **stale** acknowledgement; the referee's lifecycle filter matches the official resolver | — |
| `dual-oracle-0825` | the dual-oracle gate at spec 2026-08-25: 116-schema referee base; corpus = responses captured in-process from the pinned golden-0825 + the #43 boundary rebuilt on the captured completed checkout + the `--def` self-root path where the pinned Rust oracle **aborts** (third verdict state `crash`, acknowledged by `ucp-schema-45-selfroot-def-crash`); every acknowledgement expires on `schema_validator_pin_not` | independent `jsonschema` engine + captured golden responses |
| `dual-oracle-0825-killtest` | cases 1–4 as at 04-08 plus case 5 (the real pinned oracle: #43 verdict ×3 and the `--def` crash ×1 acknowledged, nothing new) and case 6 (a moved oracle pin flags every acknowledgement `STALE (pin moved)`) | — |
| `suite-04-08` | 2026-04-08 fixture checks pass, no false green | official schemas |
| `fixture` | our controlled merchant's profile + responses are schema-valid | official `ucp.json` / catalog schemas |
| `merchant` | every merchant check is **clean-pass + kill-safe** on the Flower Shop golden | independent golden server |
| `merchant-catalog` | catalog checks are clean-pass + kill-safe on our controlled fixture | fixture (schema-anchored) |
| `suite-01-23` | the 2026-01-23 suite vs a live golden, no false green | independent golden server |
| `attribution-hook` | no AI/bot author, co-author or generated-with line on any commit since 2026-09-10 (decision 16, forward-only), and this clone's active commit-msg hook is the tracked `ops/tools/hooks/commit-msg` (install: `bash ops/tools/install_hooks.sh`); `attribution-selftest` plants a trailer, a bot author and a missing hook to prove the gate can go red | git history + the tracked hook |
| `filing-lint` | the branch-level attribution net `ops/tools/filing_lint.py` (unpushed range of every local branch of every repo in `ops/tools/repos.json`; `ops/filings/` drafts) provably catches planted trailers; SKIP when ops/ is not mounted | scratch repos with planted trailers |
| `reach-selftest` | the CI reach-report drift step (`gen_reach_report.py --check`) provably catches a planted graded-status flip (1 drift), stays quiet on an unchanged rerun and on reason-text changes, and round-trips the committed report without drift; regenerated labels are published only by an owner commit (decision 6) | committed reach report (data we did not grade this run) |
| `proxy-demo` | the mutation-proxy demo: injected wire defects are caught (2 real checks + the noop canary); the per-check kill-rate proof is the `merchant*` gates | mutation harness |
| `run-suite-only` | `--only <gate>` runs exactly the named gates and boots only their fixtures (every acceptance command is runnable as written) | — |
| `wire-shapes` | version-keyed request shapes: 08-25 delta applied, older versions frozen byte-for-byte, unreviewed version fails closed | official 08-25 schemas |
| `cli-summary` | the CLI denominator is capability/transport-aware from `_area_capabilities.json` (fail-closed); no coverage number for unreviewed/REST-less servers | — |
| `killset-lock` / `killset-lock-selftest` | every kill set hashed in `killset_lock.json`; a silent shrink or drift is red, named | — |
| `dormancy` | every merchant check runs on some golden or is named in `dormancy_exemptions.json` (floor 13; partial union is red) | the four goldens |
| `battery-freshness` | the R11 golden-0825 mutant battery ran, recently, and passed (in-run in CI) | own golden-0825 |
| `probe-shape-0825` | the CLI vs golden-0825 (booted on :8197) shows 0 deviations in both probe shapes, >= 29 checks run | own golden-0825 |
| `package-bundle` | the pip bundle carries every module + data file the runner imports (isolated-interpreter import) | — |
| `golden-0825-unit` | golden-0825's own unit + smoke tests (`server/*_test.py`, `smoke/`) are **executed** under `uv` on every run, so a red failing-first test can never sit unnoticed in the tree; `uv` absent = honest skip (rc 2), FAIL under `--require-server`. `golden-0825-unit-selftest` plants a failing test in a scratch copy (must be red) and hides `uv` (must be rc 2) | — |
| `site-docclaims` | non-page copy (README, this file, packaging README, docs, `functions/**/*.js`) advertises only live counts; registered doc claims hold; every gate row in this table names a real `run_suite.py` gate | — |
| `known-issues` | `conformance/ci/known_issues.json` (the single KNOWN ISSUES source) has no refuted, stale (`re_verified` > 30 d), unevidenced-`fixed` or unanchored row; ledger cross-ref when `ops/` is mounted (else honest SKIP) | ledger + upstream threads |
| `deploy-guards` | `packaging/deploy.sh --selftest`: the only deploy path refuses a dirty tree, HEAD≠origin/main, a failed `selftest` check-run, a stale site export or a red gate (exit 3), and deploys `preview-<sha7>` before `main` — synthetic repo + stub gh/wrangler | — |
| `ports-registry` | every literal port the harness binds is registered in `conformance/ci/ports.json` (the single source `selftest.sh` sweeps from) and no two names claim one port; hermetic kill-tests plant an unregistered literal + a collision | — |

The controlled merchant fixture (`conformance/fixtures/merchant/`) is a dependency-free
stdlib server that `run_suite.py` auto-boots. It exists to cover capabilities the
official samples don't implement (catalog now; cart next). It is not a substitute
oracle: every artifact it serves is validated against the official schemas by the
`fixture` gate, so a catalog check that passes here is anchored to the official
validator, not to our own checks.

Green requires every *run* gate to pass. Gates auto-skip (not fail) when their
prerequisite is absent — no golden server, or the Rust `ucp-schema` oracle not built —
so a partial environment is honest rather than falsely red or falsely green.

## Why this isn't circular

The golden is an **independent implementation** (the official Flower Shop), and every
check is additionally anchored to things we didn't author: the official spec text
(register quotes), the official schema validator, and the mutation kill-rate (a check
must catch defects regardless of who wrote the target). A merchant we build ourselves
can extend coverage, but its responses are vouched for by the *independent* schema
oracle — not by our own checks.

## Run it

```bash
# one-time / CI: materialize pinned upstreams into .vendor (gitignored)
conformance/ci/fetch_sources.sh

# recommended locally: one self-cleaning command — brings up the golden, runs
# every gate, and ALWAYS tears everything down (even on Ctrl-C or error), and
# pre-cleans stray servers so it's self-healing after a hard kill. No orphans.
conformance/ci/selftest.sh            # add --verbose or any run_suite.py args
```

`selftest.sh` is the way to run locally — you never have to hunt down leftover
servers: its pre-clean sweep is derived from `conformance/ci/ports.json` (the ports registry).

<details><summary>Manual steps (what the wrapper does)</summary>

```bash
PORT=8182 conformance/ci/serve_golden.sh
python3 conformance/ci/run_suite.py --server http://localhost:8182 --require-server
conformance/ci/stop_golden.sh
```

With the golden already up, just: `python3 conformance/ci/run_suite.py`. Note that
`run_suite.py` cleans up the fixture/proxy it spawns on normal exit, but the golden
(started detached by `serve_golden.sh`) must be stopped with `stop_golden.sh` — which
is exactly why `selftest.sh` exists.
</details>

## CI

`.github/workflows/conformance.yml` runs the above on every push/PR that touches
`conformance/**`: fetch pinned sources → serve golden → run gates → tear down. A red
run means a change broke the suite's soundness. Treat it like any failing test:
the change is wrong until the gate is green (or the gate itself is corrected with
justification).

This is the TDD loop: **change a check → run the suite → it must stay green.** Proven
to catch regressions — weakening a check until it is no longer kill-safe turns the
`merchant` gate red.
