# CI / TDD infrastructure — "the test suite for the test suite"

A conformance suite that can silently mis-grade is worse than none. So this suite
tests *itself*: on every change we bring up a known-good golden server and run a set
of gates that fail loudly if a check, the register, or the engine loses soundness.

## The gates (`run_suite.py`)

| gate | what it proves | anchor (not written by us) |
|---|---|---|
| `register` | every register row quotes the pinned spec **verbatim** | official spec text |
| `verdict` | the no-false-green verdict gate's own unit tests hold | — |
| `schema` | our schema checks agree with the official validator | official `ucp-schema` binary |
| `suite-04-08` | 2026-04-08 fixture checks pass, no false green | official schemas |
| `fixture` | our controlled merchant's profile + responses are schema-valid | official `ucp.json` / catalog schemas |
| `merchant` | every merchant check is **clean-pass + kill-safe** on the Flower Shop golden | independent golden server |
| `merchant-catalog` | catalog checks are clean-pass + kill-safe on our controlled fixture | fixture (schema-anchored) |
| `suite-01-23` | the 2026-01-23 suite vs a live golden, no false green | independent golden server |
| `killrate` | injected defects are caught (100% kill-rate) | mutation harness |

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

# bring up the golden, run all gates, tear down
PORT=8182 conformance/ci/serve_golden.sh
python3 conformance/ci/run_suite.py --server http://localhost:8182 --require-server
conformance/ci/stop_golden.sh
```

Locally, with the golden already up, just: `python3 conformance/ci/run_suite.py`.

## CI

`.github/workflows/conformance.yml` runs the above on every push/PR that touches
`conformance/**`: fetch pinned sources → serve golden → run gates → tear down. A red
run means a change broke the suite's soundness. Treat it like any failing test:
the change is wrong until the gate is green (or the gate itself is corrected with
justification).

This is the TDD loop: **change a check → run the suite → it must stay green.** Proven
to catch regressions — weakening a check until it is no longer kill-safe turns the
`merchant` gate red.
