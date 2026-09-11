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
| `expiry-clocks` / `expiry-clocks-selftest` | every entry of every clocked register (expiry_registers.json) carries `review_by` (not past) and `spec_pin` (matches the lock); a re-pin invalidates every review made against the old pin (D2-04/D2-19) | SOURCES.lock.json |
| `verdict` | the no-false-green verdict gate's own unit tests hold | — |
| `schema` | our schema checks agree with the official validator | official `ucp-schema` binary |
| `dual-oracle` | every schema check runs the Rust oracle **and** an independent Python jsonschema referee (full `$id` registry over all 78 schemas); verdict divergence alarms. Known oracle bugs (ucp-schema#43) are acknowledged + self-expiring | independent `jsonschema` engine |
| `dual-oracle-killtest` | the divergence detector provably catches a **planted** divergence + a **stale** acknowledgement; the referee's lifecycle filter matches the official resolver | — |
| `dual-oracle-0825` | the dual-oracle gate at spec 2026-08-25: 116-schema referee base; corpus = responses captured in-process from the pinned golden-0825 + the #43 boundary rebuilt on the captured completed checkout + the `--def` self-root path — run on the PER-VERSION oracle (D4-04: the merged-main b52518f5 build, which contains #66), so the boundary agrees and the self-root path validates; both are kept as regression watches (a crash is a NEW divergence); every acknowledgement expires on the build serving its version (`schema_validator_pin_not`) | independent `jsonschema` engine + captured golden responses |
| `dual-oracle-0825-killtest` | cases 1–4 as at 04-08 plus case 5 (the per-version build: #43 boundary agrees ×3, `--def selected_payment_instrument` validates, 0 crash, 0 acknowledgements live at 08-25) and case 6 (a moved pin flags every register entry `STALE (pin moved)`; the real per-version pins flag none) | — |
| `dual-oracle-0825-pydantic` / `dual-oracle-0825-pydantic-killtest` | the PYDANTIC third leg (D4-05): the same 08-25 corpus plus the L4 probe families through the python-sdk generated models — `ucp-sdk==0.5.0` (the PyPI tag cut) is GATED: every tag-vs-referee disagreement must be an entry of `known_sdk_drops.json` (7 today: JWK crv/alg conditionals, C62 scale, dependentRequired, maxProperties, destination discriminator ×2), self-expiring the moment the tag venv resolves a ucp-sdk `> pypi_ucp_sdk_gt`; python-sdk main (836b0228) and the js-sdk zod leg are REPORT-ONLY (`REPORT` lines; rows to `ops/feeds/zod_divergences.json`); the killtest proves the tag leg alone rejects the #43 payload, an acknowledged drop, a deleted entry (red), an offline venv (rc 2, never green), the 0.4.9 expiry mutant and the controls | two SDK cuts in their own venvs + the referee |
| `seqfuzz-selftest` | the stateful sequence-fuzz oracle (`seq_invariants.py` I1..I9 + I10 = REPLAY-002, the single implementation; `replay_mode.json`: enforce in golden gates, report in user-facing runs until 2026-11-09 — decision 1) provably catches four planted lifecycle violations (in_progress+order → I3, terminal left → I2, 500 on a quantity change → I8, re-serialized replay cached → I10) on an in-process stub and stays quiet on a clean one; the live 120 s + 8-race run against a disposable golden is nightly, its `seqfuzz/LAST_RUN.json` a 14-day report line | planted stubs (the oracle's own kill-test) |
| `crosscheck-selftest` | the OFFICIAL conformance suite runs nightly against our goldens (:8382 flower-04-08 gated, :8398 golden-0825 report-only; per-pin venv with the suite's own python-sdk tag; `official_crosscheck.py`); its allowlist (`official_crosscheck_allowlist.json`, samples#218 today) is self-expiring — USED when the test fails, STALE (red) when it passes, EXPIRED (red) on `samples_pin_not` / `or_pr_merged` / `or_date` — and this hermetic selftest proves every arm with a synthetic junit and a stubbed `gh`; unlisted official failures become `crosscheck_diffs` ledger candidates | the official suite (not written by us) |
| `nightly-workflow-selftest` | `.github/workflows/nightly.yml` (E1 crosscheck :8382/:8398, E3 seqfuzz on a disposable golden, B6/B5b oracles; artifacts only, pulled by `ops/tools/pull_feeds.py`) keeps its contract: the three jobs, `timeout-minutes`, ports registered in `ports.json` and disjoint from the push-gate sweep set, no `continue-on-error` on an assertion step, NO discovery-live job or sampler step (decision 4), no write under `ops/`; eight mutants prove each net | `ports.json` |
| `pull-feeds-selftest` | `ops/tools/pull_feeds.py` writes `ops/feeds/<feed>.json` with `{pulled_at, run_id, sha}` provenance from a stubbed `gh`, `--check` reports drift (rc 1), and a stub `git` on PATH is never invoked (decision 24: the owner commits); SKIP when ops/ is not mounted | stubbed gh + git |
| `discovery-live-selftest` | the discovery-live sampler (`discovery_live.py`, decision 4: GET `/.well-known/ucp` only, named UA, per-UA robots, ≤1/s, ≤1/store/day, ≤50/day, no redirects, owner's machine only — `probe_policy.json`) provably refuses every denylisted request, honours robots, rate-limits, grades captures OFFLINE (a socket opened during `--grade` fails the case), earns only the business-side rows (OVR-001/010, DISC-001/002b/003/005, SIG-007/008, CAP-001/003 — never CAP-004/006, DISC-004/007/008), classifies `discovery-live` only with ≥3 domains within 30 days (never live-wire), refuses under `GITHUB_ACTIONS`, and keeps `differential_targets.json` at 2 | stubbed network + the golden's captured profile |
| `ucpchecker-compare-selftest` | `ucpchecker_compare.py` (public `/status/{domain}` pages only, never `/api/`; 24 h cache; ≤50/week): the agreement matrix and the documented divergence classes (legacy `signing_keys`, followed redirects, `payment_handlers`, robots) on synthetic pages; anything else is a `candidate`, never auto-filed | synthetic pages |
| `oracle-manifest` / `oracle-manifest-selftest` | the per-version oracle manifest (`conformance/ci/oracle_manifest.json`, decision 7b): merged SHAs only, the 04-08 entry equals SOURCES.lock, each vendor dir at its commit with the binary's `--version` fingerprint, no 08-25 blind spot, `"$ref": "#"` self-root files 2/1/1/0 per layout, no `--def` call site on a blind spot the referee does not judge; the selftest kill-proves every net (not-merged, moved lock, alias cycle, planted blind spot, swapped binary → boot guard exit 3) | SOURCES.lock.json + `git rev-parse` + `<bin> --version` |
| `oracle-verdict-diff` | both ucp-schema builds × both schema layouts over the dual-oracle corpora: 0 crash / 0 rc2 on each build's own layout, cross cells recorded as the reason for the split (STALE when both agree everywhere); the tracked `oracle_verdict_diff.json` (owner `--record`, decision 24) must be ≤ 14 d old and cell-identical | two independently built binaries |
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
