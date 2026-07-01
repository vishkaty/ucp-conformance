# spck-conformance

Unofficial, capability-adaptive conformance runner for the **Universal Commerce
Protocol (UCP)**. Point it at any UCP server and get an honest, capability-scoped
report — it runs only the checks that apply to what the server declares, and every
check is kill-rate-validated (proven to catch its own defects) before it ships.

> Independent project. Not affiliated with, endorsed by, or a substitute for the
> official UCP conformance suite. It reports only the checks it actually runs.

## Install

```bash
pip install spck-conformance
```

No third-party dependencies (Python ≥ 3.9, stdlib only).

## Use

```bash
spck-conformance --server https://api.example.com \
    [--config merchant.json] [--json] [--junit report.xml]
```

- **`--config`** — optional JSON supplying data-dependent inputs (product id, discount
  codes, a succeeding/failing payment, an out-of-stock id). Without it, those checks
  are honestly `not-tested` rather than silently passed.
- **`--json`** — full machine-readable report; each check cites its normative clause
  (id, verbatim text, spec source).
- **`--junit FILE`** — JUnit XML for CI (deviation → `<failure>`, not-applicable /
  not-tested → `<skipped>`).
- **Exit code** — `2` if any MUST deviates, else `0` (partial coverage is not a failure).

## What it checks

Discovery + profile structure, checkout lifecycle, idempotency, validation,
fulfillment, order completion, payment-credential handling, discounts, catalog
(search/lookup), and cart — scoped to the capabilities the target declares. The
profile-schema check requires the native `ucp-schema` validator (not shipped in the
wheel), so it reports `not-tested` here; run it from the source repo for full fidelity.

Source, methodology, and the self-validating CI harness:
<https://github.com/vishkaty/ucp-conformance>.
