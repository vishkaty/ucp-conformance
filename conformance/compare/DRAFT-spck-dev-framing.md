# DRAFT — spck.dev publication framing for the kill-rate comparison. HELD for owner review.

Status: **NOT PUBLISHED. NOT FINAL.** Numbers below are from the recorded runs
in `results/`; re-verify against `results/results.json` at publish time.

---

## We ran our conformance suite and the official one through the same mutation harness. Here's everything we found — including where we lost.

Every check in the spck suite has to prove, in CI, that it *fails* when a
server is broken — we inject spec-cited defects and a check that stays green is
flagged unsafe and can never contribute to a verdict. That discipline is called
mutation testing. The official UCP conformance suite has no equivalent — so we
built one for it, and ran **both suites through the same harness, on the same
defects, against the same reference server.**

### The setup (designed to be attackable)

- 18 injected defects on the surface **both** suites test (REST checkout,
  order, discount, fulfillment, error envelope, discovery — against the
  official flower reference). Every defect cites the pinned-spec MUST it
  violates; the citations are machine-checked against our requirement register.
- Both suites hit the *same* mutated server through the same proxy. No
  cooperation, no filtered test lists: the official suite runs exactly as its
  README prescribes, at **current upstream main** (newer than our own pin —
  it competes at its best), with its own fixtures.
- A suite scores a catch only when its own normal report affirmatively flags
  the server — and only if it does so on *every* repeat run.
- Defects only we could plausibly catch (agent-lane, signatures, HTTP header
  contracts, cross-version) are **excluded from the head-to-head** and reported
  separately. Defect candidates whose MUST binds the platform rather than the
  server (e.g. totals-consistency verification is a platform MAY in the pinned
  spec) were excluded even though the official suite would likely have caught
  them.
- One command reproduces everything: `conformance/compare/reproduce.sh`. The
  results file records the SHA of every input.

### What happened: first, it caught *us*

Run 1, our suite as then shipped: **spck 12/18. Official 17/18. We lost.**

Our live merchant runner was missing the checkout-totals invariants — "exactly
one subtotal entry", "discount entries are negative", and friends existed only
in our cart-capability lane and our fixture lane, neither of which observes a
live checkout. The official suite caught five defects there that we missed,
plus a missing order-confirmation field. That is precisely the failure mode
mutation testing exists to expose, and it works on our suite too.

We closed the gap the same day the way we close every gap: five new
register-driven live checks, each proven clean-pass *and* kill-safe against the
reference before it may contribute to any verdict
(`merchant_checks_04_08_totals.py` — the file discloses this origin story in
its docstring).

Run 2, after the fix: **spck 18/18. Official 17/18.**

### The one defect the official suite still misses

A server that rejects a request with HTTP 4xx while stamping the error
envelope `ucp.status: "success"` — a self-contradictory body a platform can
machine-read as success — passes the full official suite. The root cause is a
one-line model-wiring issue in the official python-sdk (`ErrorResponse` uses
the generic ucp metadata type instead of the SDK's own error branch), which
the suite's error assertions inherit. We've drafted the fix for both layers to
offer upstream — the same way several of the official suite's newest catches
in this very comparison came from fixes we contributed earlier (#74–#81).

### Surfaces beyond the head-to-head

Two injected header-contract defects (profile served without `Cache-Control`,
responses served as `text/html`) sit on surfaces the official suite doesn't
attempt, so they're not counted against it: spck 2/2. The larger surfaces only
spck covers — RFC-9421 message signatures graded against a signing reference,
the agent-conformance lane, three spec versions, and the evidence-class label
on every check — can't be expressed as common mutants at all, which is exactly
the point of reporting them separately instead of blending them into a number.

### What we claim, and what we don't

- **Claim:** on the shared surface, with our suite's gaps closed by its own
  medicine, spck catches 18/18 vs the official suite's 17/18 — and unlike the
  official suite, every spck check carries a machine-enforced proof that it
  catches the defects it exists to catch.
- **Not claimed:** that the official suite is weak (it caught 17/18 — it is a
  strong, actively improving certification baseline, and it beat our shipped
  suite on first contact), or that this one mutant set is exhaustive, or that
  numbers on surfaces only we cover say anything about the official suite.
- The comparison harness, mutant set, and both suites' full results are in the
  repo. If you think the mutant set is rigged, run `reproduce.sh` and then add
  a mutant you think we'll miss — that's what it's for.

---

*(Publication notes for owner, not part of the copy: (1) the "same day" phrasing
assumes we publish run-1-then-run-2 as one story — if the fix commit lands
separately, adjust; (2) upstream filings in DRAFT-upstream-gaps.md should land
BEFORE this goes live so the sdk/conformance issue isn't disclosed publicly
here first; (3) keep the credits to upstream #74–#81 — they're verifiable in
their git log and they're the collaboration proof.)*
