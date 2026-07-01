# UCP Conformance — Coverage & Methodology

**Unofficial, independent tool.** Not affiliated with, endorsed by, or a substitute
for the official [UCP conformance suite](https://github.com/Universal-Commerce-Protocol/conformance).
This tool produces a **conformance report**, never a certification: a pass reflects
only the checks actually run against a server, and is **not proof of compliance**.
The official suite is authoritative.

This document is the public, auditable record of *what* the tool checks, *how* it
knows the check is correct, and *what it does not check (and why)*.

## The core guarantee: it cannot falsely certify

Every check is forced through machinery that makes a green verdict impossible unless
it is earned:

1. **Quote-verified register.** Every requirement is extracted from the spec and
   stored with a verbatim quote at a cited file+line of the pinned spec. A CI check
   (`verify_register.py`) rejects any requirement whose quote does not match the
   source — so no requirement is invented or paraphrased.
2. **Schema oracle.** Response shape is validated by the *official* `ucp-schema`
   validator (not a hand-rolled one), so `$ref`/`$defs`/`allOf` composition can't
   silently diverge.
3. **Mutation kill-rate.** Each check is run against the reference server both clean
   (must pass) and against a battery of deliberately-corrupted responses (each must
   fail). A check that lets a defect through is flagged **UNSAFE** and cannot count.
4. **Verdict gate.** No aggregate "pass" unless *every* in-scope MUST is a kill-safe
   pass or explicitly `not-tested` — with the scope stamp + this disclaimer present.
   A single not-tested MUST yields `INCOMPLETE`, never a green.

## Source of truth (pinned by commit)

| Artifact | Pin |
|---|---|
| Spec `2026-04-08` | `Universal-Commerce-Protocol/ucp@a2d8bf0b` (tag commit dated 2026-05-22) |
| Spec `2026-01-23` | `ucp@dcf7eac7` |
| Spec `2026-01-11` | `ucp@806a5b74` |
| Official suite (oracle, spec-2026-01-23) | `conformance@main` (pinned SHA) |
| Schema validator | `ucp-schema@main` (pinned SHA) |
| Reference server | `samples@main` Flower Shop (serves 2026-01-23) + `python-sdk@f54858e` |

Full pins + rationale: `conformance/SOURCES.lock.json`. Discrepancies we surface as
flags (never silently resolve): `conformance/AMBIGUITIES.md`.

## Requirements registers (verified)

| Spec version | Requirements | MUST / MUST NOT | SHOULD | MAY | schema-enforced | official-suite oracle |
|---|---|---|---|---|---|---|
| **2026-04-08** | 438 | 322 | 67 | 49 | 93 | 29 (7%) |
| **2026-01-23** | 235 | 181 | 32 | 22 | 50 | 68 (29%) |
| **2026-01-11** | 223 | 172 | 32 | 19 | 48 | 67 (30%) |

All three registers are quote-verified (`438 + 235 + 223 = 896` requirements, 100%).

`2026-01-23` is the **oracle-backed primary**: it's the only version with a live
reference server *and* an official test suite, so 29% of its requirements have an
independent test oracle. `2026-04-08` is the latest spec but has **no reference
server**, so it is validated against synthetic fixtures + spec traceability.

## Executable checks — `2026-01-23` (against the live reference server)

50 checks across 8 modules, **every one kill-rate-validated** (clean-pass + catches
100% of its injected defects). Current aggregate: **`INCOMPLETE` — 47/98 testable
MUSTs (48%), 0 deviations.**

| Module | Checks | Requirements |
|---|---|---|
| core (discovery + lifecycle + idempotency + validation) | 12 | DISC-007/013, CHK-001/002/004/005/008/012, IDM-004, VAL-001/003/004, FUL-008 |
| checkout-lifecycle-2 | 6 | CHK-003/006/007/010/012/013, IDM-003 |
| fulfillment | 5 | FUL-003/004/007/008/030 |
| order | 6 | ORD-001/002/003/004/005/010 |
| discount | 5 | DSC-004/005/006/007/011 |
| validation + security | 8 | VAL-005..009, SEC-001/002/003 |
| negotiation | 6 | DISC-001, NEG-012/016/017/019 |
| payment | 2 | PAY-002/009 |

## Executable checks — `2026-04-08` (synthetic fixtures, no live server)

Because 04-08 has no reference server, its checks validate hand-built synthetic
response fixtures through the **official `ucp-schema` validator** (the same oracle,
version-matched to 04-08), and the same mutation kill-rate proves each catches its
defect. **14 checks, all kill_safe**; aggregate **`INCOMPLETE` — 15/170 MUSTs (9%),
0 deviations.**

| Area | Checks | Requirements |
|---|---|---|
| catalog | 1 | CAT-029 (search response schema) |
| checkout + totals | 6 | CHK-033/034, TOT-005/006/014/015 (totals contract + signs) |
| order | 4 | ORD-003/004/005 (incl. the `currency`-required 04-08 delta) |
| error-envelope | 3 | ERR-001/003/028/029/030 |

## What is NOT tested, and why (honest)

The ~52 uncovered `2026-01-23` MUSTs are **not gaps we hide** — the report marks them
`not-tested`. They fall into:

- **needs-receiver** (46 MUSTs) — order webhooks require a public callback endpoint.
- **needs-oauth** (12 MUSTs) — identity-linking requires a live OAuth flow.
- **manual / prose** (68 rows) — rendering and semantic requirements a machine can't
  verify from a response (e.g. "option title MUST distinguish from siblings").
- **request-side / data-state** — schema constraints on requests, or state only
  observable across mutations we can't drive against the reference server.

Raising coverage means building that infrastructure (a webhook receiver, an OAuth
harness), not writing more of the same checks. Nothing below the reported coverage %
is claimed as passing.

## Run it yourself

```bash
# self-validate the whole apparatus
python3 conformance/selfcheck/verify_register.py            # registers quote-verified
python3 conformance/selfcheck/schema_oracle.py             # schema-oracle parity
python3 conformance/selfcheck/verdict_gate.py              # verdict-gate unit tests
python3 conformance/selfcheck/mutation_killrate.py         # kill-rate demo

# run the conformance report (unofficial) — text or CI-friendly JSON
python3 conformance/checks/report.py --version 2026-01-23 --server http://localhost:8182
python3 conformance/checks/report.py --version 2026-04-08 --json     # fixture-based
```

Exit code: `0` pass · `1` incomplete · `2` fail (a MUST deviation).
