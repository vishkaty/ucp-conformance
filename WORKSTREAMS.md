# WORKSTREAMS — where the program is, wave by wave

last-reviewed: 2026-09-11

The goal (unchanged): every normative MUST of every pinned UCP spec version — 2026-01-11,
2026-01-23, 2026-04-08 and 2026-08-25, merchant and agent roles — is exactly one of CHECK
(kill-rate-validated), EXEMPT (documented, expiring) or GAP (tracked, ratcheted), with the evidence
class of every CHECK published. Live status: https://spck.dev/coverage. The detailed task board
(109 blocks with failing-first tests, kill-proofs and acceptance commands) is in the private ops
repo (`ops/analysis/deep-2026-09-09/TASKS-v3.md`); this page is the public shape of it.

## Waves

| Wave | Status | Focus | Exit |
|---|---|---|---|
| W0 | **closed 2026-09-11** — merged to `main`, deployed, released as `0.4.0` (rc1 first, clean-venv acceptance vs golden-0825) | The honest baseline: 2026-08-25 register shipped; rule R-a publication states (`live` / `converting`); evidence-class split; agent 08-25 attribution suppressed until a sandbox exists; ports registry; deploy/release guards; the single known-issues source; attribution hook; expiry clocks on every waiver | W0-1..W0-18 re-derived by an independent session |
| W1 | **in flight** (five lanes: engine/CLI, register/accounting, golden-0825, ops/nightly/filings, site/release) | Pinned skips + MCP-only lane; wave targets; roles and per-role denominators; SHOULD census (report-only); the six evidence classes; per-version oracle; MCP bridge + harness; sequence fuzz; nightly cross-checker; discovery-live sampler (owner's machine only, discovery-only); known-issues page; preview parity; steward docs; number flows | W1-1..W1-13; the W1-exit throughput checkpoint (decision 14) |
| W2 | planned | Embedded-transport rows; OAuth/identity goldens; signed golden; webhook reference; emission checks; the 08-25 sandbox for the agent lane; normative-basis publication | W2-1..W2-10 |
| W3 | planned | The long tail: remaining receiver rows, WBA-shape signatures, `1.0.0` (rc first) at DONE-2 | DONE-2 items 1–12 |

## Standing rules that apply to every wave

- **Honest counts go down before they go up** (decision 6): a drop in a public number ships in one
  deploy with its denominator and a dated note, never silently and never blended.
- **Nothing posts upstream without the owner** (decision 5): filings are prepared as packets and
  posted personally, after a re-probe and an independent refutation.
- **No AI attribution anywhere, from 2026-09-10** (decision 16) — see DECISIONS.md.
- **Every task is TDD** — a failing-first test, the implementation, a kill-proof (the planted
  violation must red the gate), the acceptance command verbatim, regression coverage.

## How to read a wave's exit

Each exit criterion is a command with an expected output (the plan's `W<n>-<k>` rows). A wave is
closed when an independent session re-derives every row, the full `conformance/ci/selftest.sh` is
green, `packaging/preflight.sh` passes, and the published numbers equal the computed ones.
