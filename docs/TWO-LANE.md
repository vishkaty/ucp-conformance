# Two lanes, one engine — the merchant & agent conformance loops

UCP is two-sided. This project runs **two independent conformance loops** — one for the
**merchant/business** (the server) and one for the **platform/agent** (the client) — on a
shared engine, with hard guarantees that **neither can break the other**, both are always
tested, and requirements + docs are tracked on both sides with equal rigor.

This is the contract. If you change anything, keep it true.

## The two loops

| | **Merchant lane** (shipped) | **Agent lane** (building) |
|---|---|---|
| Under test | a merchant UCP server (its responses) | a platform/agent (its client-side behavior) |
| Golden | controlled merchant fixture (`conformance/fixtures/merchant/`) | reference agent (`conformance/agent/reference_agent.py`) |
| Defect injection | mutate captured responses | mutation agents (`defect=` on the reference agent) |
| Kill-rate | check must fail its mutated response | check must fail its `kill_mutation` agent |
| Checks | `conformance/checks/*.py` (193) | `conformance/agent/agent_checks.py` |
| Coverage axis | `coverage/matrix.py` → 87/87/87 | `agent/agent_matrix.py` → separate % |
| Governance | coverage-lock, review-signoff, completeness, ratchet | `agent/agent_governance.py` (same four) |
| Runner/lane | the 30 merchant gates in `run_suite.py` | `agent/run_agent.py` (the `agent-lane` gate) |

Both loops enforce the **same TDD discipline**: trace to a verbatim spec clause →
independent adversarial review → reference-gate (clean-pass on the golden + kill-safe on
the defect) → coverage-lock (tests are permanent) → completeness (proven denominator) →
ratchet (no regression).

## Bidirectional isolation — neither side can break the other

**1. Structural (by construction).** The agent lane lives in `conformance/agent/`. The
merchant coverage engine globs `conformance/checks/*.py` **non-recursively**, and the
merchant collectors glob `area_*.py` — so the agent tree is **invisible** to all merchant
machinery. Adding/removing agent checks *cannot* move the merchant coverage numbers.
(Proven: merchant scans 69 modules, zero from `agent/`; merchant stays 87/87/87.)

**2. Both lanes always run (the primary cross-side catch).** CI triggers on
`conformance/**`, and `run_suite` runs **every** gate on **every** change — both lanes,
regardless of which side you touched. So a merchant change that breaks the agent flow turns
the `agent-lane` gate red, and an agent change that breaks merchant turns a merchant gate
red. Neither can regress silently.

**3. Contract gates (belt-and-suspenders).**
- **`merchant-stability`** — snapshots the merchant fixture's canonical responses; fails if
  agent work drifts any merchant-visible byte. Protects the *merchant output* and the
  *sandbox contract* the agent lane depends on.
- **`shared-api`** — pins the required-arg surface of the `engine` primitives both lanes
  share; a refactor that removes/renames/makes-required a shared function fails the build.
  Additive-only (new optional params / new functions) is allowed.

## Requirements are tracked on both axes

- Merchant: the register (`requirements/`) + `coverage/matrix.py` + `coverage-lock` +
  `register-completeness` → every merchant MUST is check / exempt / gap.
- Agent: the SAME register, filtered to platform/agent-subject rows, accounted by
  `agent/agent_matrix.py` (denominator 48/51/95 @ the three versions) →
  `agent_coverage.json` + `agent_ratchet.json` + `agent_coverage_lock.json` +
  `agent_review_signoffs.json`, all enforced by the `agent-governance` gate.

A single register row can be **merchant-EXEMPT (client-bound) AND agent-CHECK** — different
axes, no conflict.

## Maintenance rules (do these or CI stops you)

- **Change a merchant response on purpose?** Re-record the snapshot
  (`merchant_stability.py --server … --record`) — a deliberate act — AND check whether the
  agent lane needs to adapt (the `agent-lane` gate will tell you if it does).
- **Change shared `engine` code?** Additive-only. If you must change a shared signature,
  fix callers on **both** lanes and update `PINNED` in `shared_api_guard.py`.
- **Add an agent check?** Same rigor as a merchant check: reference-gate + kill-rate +
  independent review recorded in `agent_review_signoffs.json`; extend the lock via the agent
  governance; regenerate `agent_coverage.json`.
- **Docs stay in lockstep:** merchant numbers in README/ROADMAP (copy-freshness gate); agent
  numbers in `agent_coverage.json` (agent-governance freshness); this doc + ROADMAP describe
  both. Update all when either side moves.
- **Never** remove/weaken a test on either side except via the documented retirement path
  (see `TEST-INTEGRITY.md`) — pinned specs are immutable, so tests are permanent on both
  lanes.

See also: `TEST-INTEGRITY.md` (the immutability/retirement policy, both lanes),
`ROADMAP.md` (the agent workstream phases), `ops/agent-conformance-plan.md` (the full plan).
