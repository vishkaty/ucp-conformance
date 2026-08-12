# P2-13 — suite-vs-suite kill-rate comparison (spck vs official)

**Question answered:** when a merchant server has a real, spec-cited MUST-defect,
does each conformance suite — run exactly as a merchant would run it — catch it?

This is mutation testing applied to *conformance suites*: the discipline our own
CI applies to every one of our checks (`selfcheck/mutation_killrate.py`), pointed
for the first time at both our suite **and** the official one, on a **common
mutant set**, under rules designed so that the comparison is capable of making
**us** look worse.

## How it works

```
                    ┌────────────────────────────────┐
   spck merchant.py │                                │   pinned flower golden
   ────────────────►│  sticky_proxy.py (one defect,  │──►(SOURCES.lock SHA, booted
   official pytest  │  fixed at boot, no cooperation │    by ci/serve_golden.sh)
   ────────────────►│  headers honored)              │
                    └────────────────────────────────┘
```

1. `mutants.json` defines injected MUST-defects. Every mutant (a) models a
   *server* defect a conformant implementation would never emit, (b) cites the
   pinned-register MUST it violates (`requirements/2026-04-08/`; citations are
   machine-checked), (c) is injected by `sticky_proxy.py` so **both suites hit
   the same mutated server** at the same URL with zero harness cooperation.
2. Both suites first run against the **clean** golden through the **same proxy
   in passthrough mode** (baseline; identical network path in all conditions).
3. Per mutant, both suites run again; **CATCH** means the suite's own normal
   report affirmatively signals non-conformance:
   - official: ≥1 test newly failing/erroring vs baseline (tests unstable
     across repeated baseline runs are excluded from catch credit),
   - spck: ≥1 MUST reported as a *deviation* (a degrade to `not-tested` /
     `incomplete` is a MISS for us; a runner crash is never a catch for anyone).
4. Every mutant runs `--repeat` times per suite (default 2); a suite is only
   credited when **every** repeat catches. Non-determinism is flagged in the
   results, and `test_compare.py` fails on it.

## Fairness rules (the part a skeptic should audit)

- **Shared surface only in the headline.** The head-to-head covers only mutants
  on surfaces the official suite actually attempts (REST checkout / order /
  discount / fulfillment / error-envelope / discovery *bodies*). Surfaces only
  we cover (HTTP header contracts here; agent-lane, RFC-9421 signatures,
  cross-version elsewhere) are reported in a separate, clearly-labeled addendum
  and **never** counted against the official suite.
- **Their suite as-configured.** Full `uv run pytest` with the official repo's
  own flower-shop `conformance_input.json` + `test_fixtures.json`, at **current
  upstream main** (`OFFICIAL.lock.json`) — deliberately *newer* than our
  SOURCES.lock signal pin so the official suite competes at its best, including
  its latest fixes. Never a filtered test subset.
- **Our suite as-shipped.** `merchant.py` (the `spck-conformance` CLI) with our
  canonical flower config (`REF_CONFIG`, imported from
  `validate_merchant_checks.py`, not duplicated). No comparison-specific checks
  or config. Checks that stay dormant for lack of config lose us catches —
  reported as our misses, not excused.
- **Losses are reported.** Any official-catch/spck-miss appears in the results
  table and in a dedicated "gaps in the spck suite" section.
- **Register-grounded mutants only.** Candidate defects whose MUST binds the
  *platform* rather than the server (e.g. totals-consistency verification is a
  platform **MAY**, TOT-008) are excluded and documented in
  `mutants.json::_excluded_by_design` — even where the official suite would
  likely have caught them.
- **Not injectable ≠ miss.** Outbound webhook signing can't be mutated by a
  response proxy identically for both suites; excluded for both sides, no
  number claimed.

## Reproduce

```bash
conformance/compare/reproduce.sh          # golden on :8290, proxy on :8291
```

Requires `python3`, `uv`, `git`, network. `results/results.json` records the
exact SHA of every input (our repo, official suite, python-sdk sibling, golden
samples) — a results table is only valid for the pins in its own meta block.

## Files

| file | role |
|---|---|
| `mutants.json` | the common mutant set (data, not code) + design exclusions |
| `sticky_proxy.py` | whole-server single-defect proxy (fairness properties in its docstring) |
| `run_compare.py` | orchestrator: baselines, per-mutant runs, catch rules, rates, report |
| `test_compare.py` | TDD gates: rate math, catch criteria, citations, proxy semantics, post-run calibration |
| `fetch_official.sh` / `OFFICIAL.lock.json` | pinned acquisition of the official suite + sdk sibling |
| `reproduce.sh` | end-to-end reproduction (self-test → golden → run → calibrate) |
| `results/` | committed results (json + human table) for the pinned inputs |
