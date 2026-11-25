# WORKSTREAMS: the registry of what is live, where each one stopped, and the one next step

last-reviewed: 2026-11-25

Read this first for "what now". Statuses: ACTIVE, BLOCKED (on a named person or event),
PAUSED, PARKED (with a resume condition), DONE, ARCHIVED. Every row names how it fits the
central idea and carries its evidence. Detail lives where the row points; this page does not
repeat it. The private ops register (`ops/pending-upstream-actions.md`) is the state of record
for upstream threads; `ops/BACKLOG.md` ranks the work; `DECISIONS.md` holds the why.

## The central idea, unchanged

Every normative MUST of every pinned UCP specification version (2026-01-11, 2026-01-23,
2026-04-08, 2026-08-25; merchant and agent roles) is exactly one of CHECK (kill-rate
validated), EXEMPT (documented, expiring) or GAP (tracked, ratcheted), with the evidence class
of every CHECK published and every public number traceable to a source. Live status:
https://spck.dev/coverage. The seven rules every change is held to are in `PRINCIPLES.md`.

## The posture, since 2026-11-24 (decisions 39 and 40)

The work is done as an unaffiliated individual aiming at the maintainers tier of the
Universal-Commerce-Protocol org, which is granted by a domain Tech Council on contribution.
Merged conformance work reviewed by the people who grant that tier is the qualification. So:
stop filing new upstream items; service the open pull requests; keep our own honesty gates
green before anything else. Measured 2026-11-25: 88 merged pull requests under `vishkaty`,
tied first among humans with FanouZeng-TT at 88; 18 open in the org, all mergeable, all
waiting on maintainer review, none carrying an unanswered ask aimed at us.

## The wave plan is PARKED

The W0 to W3 wave table that stood here from 2026-09-11 is at `docs/archive/WAVES.md` with
its exit criteria. W0 and W1 closed 2026-09-11; the program then moved to the posture above
without a recorded decision, which `DECISIONS.md` row 40 now records. W2 (the 08-25 agent
sandbox, the signed golden, OAuth goldens, webhook reference, emission checks) and W3 (the
long tail and `1.0.0`) are PARKED. Resume condition: an owner decision to re-open W2, at which
point this file gains a row per lane again.

## Registry

| id | workstream | status | how it fits | where it stopped | the ONE next step | who |
|---|---|---|---|---|---|---|
| S1 | Service the 18 open upstream PRs (conformance #127 #145 #146 #152 #153 #154 #156; ucp #625 #660 #661 #662 #669 #782 #839 #850 #912 #917 #934) | ACTIVE | the qualification for the maintainers tier is merged, reviewed conformance work | 2026-11-25: all MERGEABLE, all blocked on the governance gate; four approved but unmerged (#127 approval predates our 11-23 force push so it needs re-approval; #660 two approvals waiting on jingyli since 10-19; #669 gsmith85; #912 karangoel16); conformance #145 is the exact fix for upstream main being red since 2026-11-23 (our #108 landed 56 minutes before #124 made the marker check strict) | answer any review within one day, reproducing with the planter named in the register block first; nothing to post today; the watcher (`ops/tools/trigger_watch.py`) is the signal | session; owner for any nudge (recommendation: none this week) |
| S2 | Two human sample reviews due 2026-12-02 | BLOCKED on owner | decision 13: the human sample is the only control for a check grading the wrong clause | sheet ready at `ops/notes/2026-11/2026-11-24-human-sample-review-sheet.md`, eleven rows, every anchor pre-resolved; `verify_review_signoffs.py` reds main on 2026-12-03 | owner reads the sheet and sets `sample.human_review.status` to `recorded` in `conformance/coverage/review_signoffs.json` for both batches, splicing as text | owner |
| S3 | December clock re-read | ACTIVE, not started | P-2: every waiver dies loudly when stale; a re-clock is a re-read, never a re-date | 0 expired today; 78 by 2026-12-09, 142 by 12-09 counting the exemptions and site claims the validator and its sibling gates red, 176 by 12-19; per-file method in `ops/notes/2026-11/D-limitations` section 3.3 | own branch, in the order the gates red: the 2026-04-08 normative basis queue (12-04, a sample batch), wave1 (12-05, retire if complete), wave1b and the two scope exclusions (12-06), the 51 completeness waivers at pin cd78fb38 (12-07), known issues and site claims (12-08) | session |
| S4 | docs-steward re-review (this rewrite) | ACTIVE | the house artifacts carry a 90 day review clock inside the required `selftest` | `validate_steward_docs.py` reds main on 2026-12-11 for PRINCIPLES, ARCHITECTURE, DECISIONS and ROADMAP, 12-23 for this file; the rewrite is on branch `house/2026-11-25` | owner go to merge the house PR | owner |
| S5 | Evidence honesty gaps | ACTIVE | P-4: evidence classes are published, never conflated | measured 2026-11-25: nine shipped merchant checks are `never_kill_tested` in `dormancy_exemptions.json` (3 AP2, 6 TLS) while README and index CLAIM-017 say every check is kill-rate tested; README carries an unregistered "~99% of UCP stores pass" sentence and names three versions where the package bundles four; DECISIONS rows 2, 26 and 31 record EXEMPT states the data never took (embedded is a scope exclusion; SIG-044..053, PAUTH-012, IDL-079, IDL-080 are GAP); index CLAIM-003 "two reference servers" dies at the next samples re-pin because samples#297 deleted rest/nodejs on 2026-11-24 | owner decides the nine: land the two named gates (D3-21 for AP2, a TLS variant gate) or correct the copy; the session then fixes README in the same change | owner, then session |
| S6 | Deploy main to spck.dev | BLOCKED on owner go | the public site is a projection and must equal the tree | last deploy 2026-10-17 (`ops/DEPLOY_LOG.md`); the live known-issues page says re-verified 2026-10-16, the tree says 2026-11-17 | `bash packaging/preflight.sh` on the exact SHA of origin/main, then `packaging/deploy.sh` on a go | owner |
| S7 | Own PR #44, the CHK-017 relabel and taxonomy | BLOCKED on owner go | decision 29 and 13 | MERGEABLE, selftest green, seven commits, two adversarial passes; local main sits at its head, seven ahead of origin, so every "before" control reads origin/main until it merges | merge on a go, then realign local main | owner |
| S8 | Register and ops hygiene | ACTIVE | the register is the state of record; a session that reads the top must find today | 2026-11-25 audit: newest state was at the bottom of a 5680 line file, 86 of 87 live triggers in pre-November blocks, the dead list in four places, `trigger_watch.py` reports the first comment after a cutoff rather than the latest (hid a maintainer comment on ucp#839 for 15 days) | land the STATE header, `ops/DEAD-LIST.md`, the retire-or-rehome pass and the split with the before-and-after trigger count as the control; fix the matcher with a failing test first | session |
| S9 | Pins | PARKED, owner decisions | pins only at merged SHAs or tags, changed only with a revalidation | samples 52319dff (main 3a104e5 has 7 wire changes and samples#297 removed rest/nodejs); ucp-schema 1.4.0 (2.0.0 differs on 3 boundary items now correct, 2.1.0 in release with format assertions); spec tags moved (04-08 to 9c9d6fce, 08-25 to 87dd4b1f, both normative; a re-pin to ucp main would break two published check classes) and acknowledged to 2026-12-12 | owner picks per `ops/notes/2026-11/UPSTREAM-STATE-2026-11-25.md` section 5; nothing moves without a go | owner |
| S10 | Commit identity, Route B | BLOCKED on owner | decision 37 | ucp#782 ten commits still attribute to `katyalai`; the global rules line still names the other address | owner adds and verifies `vishal@katyal.ai` on vishkaty, or declines and the row closes | owner |
| S11 | Our own tooling honesty | ACTIVE, queued behind S3 | P-1: a gate that cannot fail is decoration | `serve_golden.sh` hides a boot failure cause (lines 67 to 73); `conformance/ci/selftest.sh` does not wire seqfuzz-freshness or probe-shape-0825-selftest and `nightly.yml:127` runs `--record --check` in one step; FAST-FOLLOW F2 and F5 never re-measured | OWN-1 first, failing test first (plant `uv` absent, then a seed failure, then the pin guard; the FATAL text must name each) | session |
| S12 | State of UCP edition 6 | PAUSED | the public series makes dated claims (SOU-019, 020, 026) | edition 5 stands; due about 2026-12-11 | owner says whether the tracker still runs; if yes, run the refresh per the runbook now in `ops/tools/` | owner, then session |
| S13 | The Summit record | BLOCKED on owner | who grants the tier was in the room on 2026-11-11 and 11-12 | no post-summit record exists anywhere in the tree; the four preparation files are archived as preparation | owner answers the question list in `ops/notes/2026-11/SUMMIT-RECORD-QUESTIONS.md` | owner |
| S14 | Held upstream drafts | PARKED | decision 40 | six items drafted and gated, three with no register line until today; the Node platform_reference packet is dead with rest/nodejs | resume when the posture lifts; the register carries each with its gate | none |
| S15 | AP2 | PARKED by decision | AP2 is dormant since 2026-04 | four open PRs, never touched by a human, three with a red lint that #311 clears | nothing; resume on a FIDO reply or mid 2027 | none |
| S16 | Fulfillment coverage in the official suite | DONE for the reachable pool | lens (a) | the 2026-11-24 ceiling analysis: 98 rules, 70 uncovered, 5 reachable, all 5 filed (#153, #154, #156); the 52/11/17 figure that stood in the register was retracted 2026-11-24 for ignoring testability; 56 are blocked on the reference, 4 need a rig | watch triggers on ucp#1149 (Sample Store), ucp#909 and ucp#1090; nothing to build until one moves | none |

## Known reds and dates, in order

| date | what | gate | row |
|---|---|---|---|
| 2026-11-26 | the 14 fork CI runs on ucp#839 expire unapproved (created 2026-10-27) | upstream, not ours | S1 |
| 2026-12-02 | two human samples due | `verify_review_signoffs` reds 12-03 | S2 |
| 2026-12-04 to 12-19 | 176 expiry clocks | `expiry-clocks`, `site-docclaims`, `dormancy` | S3 |
| 2026-12-11 | stable docs pass 90 days | `docs-steward` | S4 |
| 2026-12-12 | the two acknowledged tag moves expire | `sources-age` | S9 |
| 2026-12-15 | decision 1c checkpoint on `replay_mode.json` | `expiry-clocks` | S11 |
| 2026-12-31 | decision 1c `report_until`, tied to ucp#782 | `seqfuzz` | S1, S11 |

## Standing rules that apply to every row

- Honest counts go down before they go up (decision 6): a drop in a public number ships in one
  deploy with its denominator and a dated note.
- Nothing posts upstream without the owner (decision 5): packets are prepared, re-probed,
  refuted independently, and posted personally.
- No AI attribution anywhere, from 2026-09-10 (decision 16).
- Every durable change is TDD with a kill-proof; every measurement names the heads it ran at.
