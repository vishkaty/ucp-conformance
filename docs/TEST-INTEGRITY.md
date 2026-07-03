# Test integrity policy

Why you can trust a green result from this suite over time — and the rules that keep it
that way. This is the contract the project holds itself to; most of it is enforced by a
CI gate, not by good intentions.

## The principle: pinned specs are immutable, so tests are permanent

Every spec version this suite targets is **pinned by commit SHA**
(`conformance/SOURCES.lock.json`) — `2026-04-08`, `2026-01-23`, `2026-01-11` are frozen
documents that will never change. It follows that:

> **A check that correctly tests a normative MUST of a pinned spec version is correct
> for the life of that version. The requirement never disappears, so the test never
> disappears.**

Coverage is a ratchet. It goes up. It does not come down because a test became
inconvenient.

## When a test may change — the only three legitimate reasons

A test for a pinned version may be removed, replaced, or downgraded **only** for one of
these, and each must be recorded in `conformance/coverage/retirements.json` with a
reason grounded in the pinned spec (with a `spec_source`) **before** the build will
accept it:

| Class | Meaning | Evidence required |
|---|---|---|
| `unsound-check` | The check tested something the spec does **not** require, or something no conformant server can trigger, or tested it incorrectly. This is a **bug fix** — the check was wrong, not the server. | The spec clause that shows the check was testing the wrong thing. |
| `superseded` | Replaced by a check that covers the same requirement id **at least as strictly**. Coverage is not lost, only relocated. | Name the replacing check; it must still cover the id. |
| `spec-defect` | The requirement is **unsatisfiable under the pinned spec's own schema** (e.g. an enum value the prose demands but the schema forbids). Left as a GAP and reported upstream. | The contradiction, and the upstream report. |

**Not acceptable, ever:** "flaky", "hard to fix", "failing and we're not sure why",
"slows CI down". Weakening or deleting a test to make a red build green is the one thing
this policy exists to forbid — it is how a conformance tool silently becomes a
rubber stamp.

Relaxing a predicate (e.g. accepting `403` as well as `401` where the spec pins no
status) is legitimate **only** when the stricter reading is not actually in the spec —
and when it removes a now-invalid kill-mutant, that removal rides the same spec citation.
If relaxing a predicate would let a genuinely non-conformant server pass, it is wrong.

## How it's enforced (not just promised)

- **`coverage_lock.json`** records the exact set of requirement ids that are CHECK or
  EXEMPT, per version. It is **add-only** — `gen_coverage_lock.py` refuses to drop an id
  it already holds unless that id is in `retirements.json`.
- **`verify_coverage_lock.py`** (the `coverage-lock` CI gate) fails the build if any
  locked CHECK id is no longer a CHECK, or any locked EXEMPT id falls back to GAP, unless
  a **valid** retirement covers it. A retirement with a bogus class or a thin reason is
  itself rejected. Both directions are kill-tested.
- **The kill-rate gate** independently requires every shipped check to clean-pass on a
  known-good golden **and** catch every one of its injected defects — a check that cannot
  fail is not a check.
- **The ratchet, citation-soundness, register-quote, and copy-freshness gates** stop the
  accounted count regressing, an id being graded against the wrong spec version, a quote
  drifting from the pinned text, and the advertised numbers going stale.
- **`verify_register_completeness.py`** (the `register-completeness` gate) proves the
  *denominator* is complete: every mandatory RFC-2119 keyword in the pinned prose spec is
  either a register row or a documented reconciliation entry (a scope exclusion for
  structurally-unobservable files like the browser-embedded MessagePort UI, or a per-line
  waiver that is a duplicate / non-normative / schema-enforced). A missed normative clause
  fails the build — a coverage percentage can't be a fraction of an assumed denominator.
- **`verify_review_signoffs.py`** (the `review-signoff` gate) makes coverage *expansion*
  as governed as removal: every locked CHECK id must carry an adversarial-review sign-off
  (an independent reviewer re-read the pinned clause and confirmed subject / citation /
  strictness). Coverage cannot grow while skipping the review that keeps it honest.
- **`differential.py`** (the `differential` gate) runs the suite against an independent,
  known-conformant server (the official Flower Shop). A check that passes our own fixture
  but flags an independently-conformant target is a differential finding — the antidote to
  a check and its fixture jointly encoding the same misreading.

## Adding a test (the normal path)

Read the register row → read the pinned spec at its source → write the check *and* the
mutants that must kill it → reference-gate it (clean-pass + kill-safe on the golden) →
`python3 conformance/coverage/gen_coverage_lock.py` to extend the lock →
`bash packaging/preflight.sh`. Coverage only grows.

## Both lanes, same policy

This policy applies identically to the **merchant** lane and the **agent** lane. The agent
lane mirrors every mechanism here — reference-gate + kill-rate (against a reference agent +
mutation agents), coverage-lock, review-signoff, completeness, ratchet — via
`conformance/agent/agent_governance.py`. The two lanes cannot break each other: the agent
tree is structurally invisible to the merchant machinery, both lanes run on every change,
and the `merchant-stability` + `shared-api` gates pin the contracts they share. See
[TWO-LANE.md](TWO-LANE.md) for the full architecture and maintenance rules.
