# DECISIONS — the closed owner decisions behind the program (PLAN-v3 §4)

last-reviewed: 2026-09-11

Every numbered decision of PLAN-v3 (1–34) plus 4b is CLOSED. One row each: the closed answer, the
date it closed, who closed it, and the task ids that carry it (the task board lives in the private
ops repo; the public trace is the gate or file the row names). This file is add-only: a reopened
decision gets a new row referencing the old number, never an edit. Validated by
`conformance/ci/validate_steward_docs.py` (run_suite gate `docs-steward`): every id 1–34 and 4b
must be present, closed and dated.

| # | Topic | Closed answer | Date | Who | Carried by |
|---|---|---|---|---|---|
| 1 | REPLAY-002 byte fidelity | Implement literally: `seq_invariants.I10` = `enforce` in the golden gates; `report` (with the spec citation + KI-009 link) in user-facing CLI/Action runs until F3-f is answered or 60 days (2026-11-09), then `enforce`; F3-f filed as a question | 2026-09-10 | owner (Vishal Katyal) | D4-07, D1-16b, D3-23 (`replay_mode.json`) |
| 2 | Embedded transport (137 obligations) | 2a: extract as rows (`transport: embedded`) in W2, EXEMPT `needs-harness-browser` with expiry — yes. 2b: the +4 lane-day Playwright harness is decided at the W1-exit checkpoint | 2026-09-10 | owner (Vishal Katyal) | D2-12; D3-22 (2b) |
| 3 | 04-08 tag re-point | Acknowledge in W0 (`known_tag_moves.json`), re-pin to a25a4a24 in W1 (364 → 385); the 19 LOY rows enter as EXEMPT `needs-target-capability` (`converts_when: D3-26b`, `clock_tier: pin-only`) so 04-08 stays `live`; F3-g stays a question | 2026-09-10 | owner (Vishal Katyal) | D2-17, D2-15 |
| 4 | Third-party sampling terms | Discovery-only (GET `/.well-known/ucp`); runs on the owner's machine, never in Actions; captures private, 90-day prune, never Actions artifacts; named UA; ≤1/s, ≤1/store/day, ≤50/day; one policy file (`probe_policy.json`) shared with the monthly tracker | 2026-09-10 | owner (Vishal Katyal) | D4-08, D4-09, D5-13 |
| 4b | Catalog reads on third-party stores | NOT NOW. Reopen only with (i) decision 11 live, (ii) an opt-in list of consenting stores, (iii) a separate POST denylist and rate policy | 2026-09-10 | owner (Vishal Katyal) | none (recorded) |
| 5 | Upstream filings | Order a (an upstream thread evidence) → b (release asks) → d (Node start path) → c (js-sdk family after re-probe) → i (an upstream thread rebase, last); (e)/(h) held; each packet: Phase 5.5 items 1–11, 24 h T-0 re-probe, independent refutation, owner posts personally | 2026-09-10 | owner (Vishal Katyal) | D4-14, D4-15 |
| 6 | Honest counts go down | Publish now: 08-25 CHECK 70 → 32 with the six-class split; agent 51 → 0; site count 227 → 228; role-split denominators; B3 labels — in ONE deploy with denominators and a dated note, before `0.4.0rc1` and before any filing | 2026-09-10 | owner (Vishal Katyal) | D2-02, D5-16, D5-05, D4-02 |
| 7 | Oracle pin policy | Per-version oracle from merged SHAs; verdict-diff on both layouts; boot guard on SHA | 2026-09-10 | owner (Vishal Katyal) | D4-04 |
| 8 | SHOULD scope | Publish "airtight = MUST / MUST NOT / REQUIRED / SHALL at pin cd78fb38"; SHOULD census report-only | 2026-09-10 | owner (Vishal Katyal) | D2-10, D5-13 |
| 9 | `live` semantics | Add `converting` (rule fixed by 25) | 2026-09-10 | owner (Vishal Katyal) | D2-06, D5-03 |
| 10 | Agent 08-25 attribution | Suppress 51 → 0 until the 08-25 sandbox exists; D3-11 scheduled to start in W1 to shorten the zero window | 2026-09-10 | owner (Vishal Katyal) | D5-04, D3-11 |
| 11 | CLI signing identity | Yes: `https://spck.dev/.well-known/ucp` platform profile with `keys[]` (kid = thumbprint) when D3-23 lands; private key on the owner's machine, never in CI or any repo; rotation runbook in ops; profile validated by all four oracles before deploy | 2026-09-10 | owner (Vishal Katyal) | D3-23 |
| 12 | Branch protection | `enforce_admins` on; no bot path or bypass; the on/off commands written in HANDOFF | 2026-09-10 | owner (Vishal Katyal) | D5-09, D5-14 |
| 13 | Manual-row exemption policy (A10) | Yes: six new classes, the three-marker reason, sample = max(10%, 10 entries) per batch of ≤40, `human_sample: true` recorded, seed batch included | 2026-09-10 | owner (Vishal Katyal) | D2-13, D2-04 |
| 14 | Resourcing | ≈200 lane-days approved (board 211.65 after the decision fold-in) with a hard W1-exit checkpoint: measured throughput ≥6 rows/lane-day from D1-15/D1-16a, else re-scope to partial ≈80; owner confirms personally at the checkpoint | 2026-09-10 | owner (Vishal Katyal) | W1-14 |
| 15 | Shopify ucp-cli | npm, pinned `@shopify/ucp-cli@0.8.0` (MIT); no vendoring | 2026-09-10 | owner (Vishal Katyal) | D3-16 |
| 16 | DONE-2 redefinition | Adopt §1 items 1–11 and add item 12: attribution gate green on every branch; no AI/bot author from 2026-09-10, forward-only, no history rewrite of the 229 earlier trailers | 2026-09-10 | owner (Vishal Katyal) | D4-16, D4-15, D2-16 |
| 17 | PyPI releases | `0.4.0rc1` first (clean-venv acceptance), then `0.4.0`; `1.0.0` at DONE-2 (rc first too); `git+main` between; README/packaging examples pinned `@v0.4.0` in the same commit as the Action install-source change | 2026-09-10 | owner (Vishal Katyal) | D5-21, D5-15 |
| 18 | Version projection vs second server | Projection (one codebase, three deltas) with its own mutants and the leaf differential | 2026-09-10 | owner (Vishal Katyal) | D3-03 |
| 19 | Behaviour-mutant guard doctrine | `behavior_armed(key)` guards; rows stay data; `LOADER-BROKEN` on an unwired key | 2026-09-10 | owner (Vishal Katyal) | D3-01 |
| 20 | Error-code casing | Lowercase — a golden bug fix (the spec writes `version_unsupported`); no AMBIGUITIES row, no F3 note | 2026-09-10 | owner (Vishal Katyal) | D3-02 |
| 21 | Absent `version=` in UCP-Agent | Read the platform profile's `version`; absent both → assume ours and record the ambiguity | 2026-09-10 | owner (Vishal Katyal) | D3-24 |
| 22 | `external-agent` evidence class | Report-only forever; divergences into `known_external_agent_divergences.json` with expiry on the ucp-cli version | 2026-09-10 | owner (Vishal Katyal) | D3-15, D3-16 |
| 23 | `review_by` horizon | Two-tier: 30 days where truth depends on moving upstream state (pins, goldens, external tools, known-issues re_verified); 90 days where it depends only on the spec pin; seed dates staggered by register (never a cliff) | 2026-09-10 | owner (Vishal Katyal) | D2-19, D2-04 |
| 24 | CI write policy (revised) | Artifacts + in-run freshness; NO bot identity anywhere; owner commits `LAST_RUN` files at release time; nightly feeds are artifacts pulled by a local `ops/tools/pull_feeds.py` and committed by the owner | 2026-09-10 | owner (Vishal Katyal) | D1-09, D4-07, D4-10, D4-17 |
| 25 | Converting rule (refines 9) | Rule R-a (`live` ⇔ no testable/needs-receiver/needs-oauth GAP); 01-11/01-23 read `converting` with a data-driven line "converting — no further work planned; N webhook-receiver rows ungraded" | 2026-09-10 | owner (Vishal Katyal) | D2-06, D5-03 |
| 26 | IdP-bound rows and PAUTH-012 | IDL-079 and IDL-080 graded `reference-impl` against the mock IdP; PAUTH-012 EXEMPT `needs-target-capability`, `converts_when: never-in-program`, review_by | 2026-09-10 | owner (Vishal Katyal) | D3-07, D3-08, D3-14, D2-13 |
| 27 | Lane of host/handler/spec-author rows | host → platform lane; handler → merchant lane with `reference-impl` evidence from our stubs; spec-author → speclint `register-selfcheck` | 2026-09-10 | owner (Vishal Katyal) | D2-08, D2-12, D2-18 |
| 28 | Action install source | Folded into 17: `action.yml` installs from its own checkout (`source: checkout` default); README pinned `@v0.4.0` in the same commit; `source: pypi` fallback documented | 2026-09-10 | owner (Vishal Katyal) | D5-06, D5-21 |
| 29 | Spec tables / pseudocode normativity | Keep as MUST with `normative_basis: table / pseudocode`; downgrades only with a signed-off justification; spec question held as an F3 candidate, question-only, after (f) | 2026-09-10 | owner (Vishal Katyal) | D2-11a, D2-11b |
| 30 | D3-28 + D3-26 split sizing | +4.5 lane-days approved | 2026-09-10 | owner (Vishal Katyal) | D3-28, D3-26a/b |
| 31 | WBA-shape SIG-044..053 | EXEMPT `needs-wba-verifier` now with review_by; D3-32 (2 lane-days) scheduled in W3 by default, dropped only if the W1-exit checkpoint shows no slack | 2026-09-10 | owner (Vishal Katyal) | D2-13, D3-32 |
| 32 | Relabel 31 + convert ≈35 | Relabel the 31 manual-but-CHECK rows `testable`; convert the ≈35 machine-testable rows (D3-30/D3-31/D2-18); ≈100 stay manual and are exempted under 13 | 2026-09-10 | owner (Vishal Katyal) | D2-02, D3-30, D3-31, D2-18 |
| 33 | Decision 3 inside W0 or the tag-moves file | Acknowledge in W0 via `known_tag_moves.json` (fail-noisy); re-pin in W1 | 2026-09-10 | owner (Vishal Katyal) | D2-17, D2-15 |
| 34 | B3 as decision-6-gated | Regenerated reach labels are gated like any public number (decision 6), never auto-applied | 2026-09-10 | owner (Vishal Katyal) | D4-02 |

## Standing rule — attribution (dated 2026-09-10, forward-only; decision 16)

No AI or bot author or co-author in ANY repo, own or upstream: no `Co-Authored-By` trailer, no
"Generated with" line, no bot commit identity. Every commit is authored by the owner (Vishal
Katyal; `vishkaty` on UCP/personal repos). Enforced at commit time by the tracked `commit-msg`
hook (install: `bash ops/tools/install_hooks.sh`, verify: `--check`), by the run_suite gate
`attribution-hook`, and pre-push by `python3 ops/tools/filing_lint.py --branches`. Forward-only: the
earlier trailers on `main` stay (no history rewrite; force-pushes are blocked).

## Branch protection (decision 12) — the exact commands

`main` requires the `selftest` check-run (strict) and `enforce_admins` is ON (no bypass, no bot path):

```bash
# turn on (PNR-1, run by the owner)
gh api -X POST repos/vishkaty/ucp-conformance/branches/main/protection/enforce_admins
# verify
gh api repos/vishkaty/ucp-conformance/branches/main/protection --jq '{enforce_admins: .enforce_admins.enabled, contexts: .required_status_checks.contexts, strict: .required_status_checks.strict}'
# turn off (only for a deliberate, logged maintenance window)
gh api -X DELETE repos/vishkaty/ucp-conformance/branches/main/protection/enforce_admins
```

## Points of no return (owner's personal go, in order)

PNR-0 attribution hook in every repo → PNR-1 `enforce_admins` → PNR-2 honest-numbers deploy →
PNR-3 `0.4.0rc1` then `0.4.0` (done 2026-09-10/11) → PNR-4 Action install-source switch + README pin
(done with 0.4.0) → PNR-5 spck.dev platform keys → PNR-6 first sampler run (discovery-only, local;
catalog OFF) → PNR-7 each upstream filing packet → PNR-8 rebase push → PNR-9 re-pin 04-08 → PNR-10
embedded extraction publish → PNR-11 `1.0.0`.
