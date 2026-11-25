# WAVES: the W0 to W3 plan, ARCHIVED 2026-11-25

ARCHIVED 2026-11-25. Reason: W0 and W1 closed on 2026-09-11 (ops/PROGRAM-STATE-2026-10-02.md
line 44; memory ucp-wave-state), and the program moved to the posture of 2026-11-24 (serve the
open upstream PRs, keep the honesty gates green, aim at the maintainers tier) without a recorded
decision; DECISIONS.md row 40 records it now. W2 and W3 are PARKED, not closed: their exits are
still the right shape for the 08-25 agent sandbox, the signed golden and `1.0.0`. Where the
ideas went: `WORKSTREAMS.md` carries the live registry; the parked rows name this file as the
resume point. The text below is the table as it stood in WORKSTREAMS.md on 2026-09-23, verbatim,
including its own staleness note, so the W1 row reads as it did then and not as it is.

The goal this plan served is unchanged and is restated at the top of WORKSTREAMS.md.

## Waves

| Wave | Status | Focus | Exit |
|---|---|---|---|
| W0 | **closed 2026-09-11** — merged to `main`, deployed, released as `0.4.0` (rc1 first, clean-venv acceptance vs golden-0825) | The honest baseline: 2026-08-25 register shipped; rule R-a publication states (`live` / `converting`); evidence-class split; agent 08-25 attribution suppressed until a sandbox exists; ports registry; deploy/release guards; the single known-issues source; attribution hook; expiry clocks on every waiver | W0-1..W0-18 re-derived by an independent session |
| W1 | **in flight** (five lanes: engine/CLI, register/accounting, golden-0825, ops/nightly/filings, site/release) | Pinned skips + MCP-only lane; wave targets; roles and per-role denominators; SHOULD census (report-only); the six evidence classes; per-version oracle; MCP bridge + harness; sequence fuzz; nightly cross-checker; discovery-live sampler (owner's machine only, discovery-only); known-issues page; preview parity; steward docs; number flows | W1-1..W1-13; the W1-exit throughput checkpoint (decision 14) |
| W2 | planned | Embedded-transport rows; OAuth/identity goldens; signed golden; webhook reference; emission checks; the 08-25 sandbox for the agent lane; normative-basis publication | W2-1..W2-10 |
| W3 | planned | The long tail: remaining receiver rows, WBA-shape signatures, `1.0.0` (rc first) at DONE-2 | DONE-2 items 1–12 |

## How to read a wave's exit

Each exit criterion is a command with an expected output (the plan's `W<n>-<k>` rows). A wave is
closed when an independent session re-derives every row, the full `conformance/ci/selftest.sh` is
green, `packaging/preflight.sh` passes, and the published numbers equal the computed ones.
