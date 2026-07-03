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

Python ≥ 3.9. One small dependency: `certifi` (a CA bundle so TLS works everywhere).

## Use

```bash
spck-conformance --server https://api.example.com \
    [--config merchant.json] [--json] [--junit report.xml]
```

### Quickstart (30 seconds)

```bash
# 1. point it at your server — no config needed for the discovery + structure checks
spck-conformance --server https://api.example.com

# 2. scaffold a config tailored to YOUR server's declared capabilities
spck-conformance --server https://api.example.com --init merchant.json
#    -> fill in the FILL_ME placeholders (a product id, discount code, payment token…)

# 3. re-run with the config to unlock the data-dependent checks
spck-conformance --server https://api.example.com --config merchant.json
```

On a deviation the report shows **expected (the requirement) vs observed (your actual
response)** so you can fix it directly, and the footer's **Next steps** tells you how to
unlock any `not-tested` checks.

### Use in CI (GitHub Action)

```yaml
# .github/workflows/ucp.yml
jobs:
  conformance:
    runs-on: ubuntu-latest
    steps:
      - uses: vishkaty/ucp-conformance@main
        with:
          server: https://api.example.com
          config: merchant.json        # optional
          # fail-on-deviation: false   # report-only mode
```

The job fails on any MUST deviation and writes a JUnit report (`ucp-conformance.xml`)
your CI can display as a test run.

- **`--config`** — optional JSON supplying data-dependent inputs (product id, discount
  codes, a succeeding/failing payment, an out-of-stock id). Without it, those checks
  are honestly `not-tested` rather than silently passed.
- **`--json`** — full machine-readable report; each check cites its normative clause
  (id, verbatim text, spec source).
- **`--junit FILE`** — JUnit XML for CI (deviation → `<failure>`, not-applicable /
  not-tested → `<skipped>`).
- **Exit code** — `2` if any MUST deviates, else `0` (partial coverage is not a failure).

## What it checks

Across **REST and MCP** transports and spec versions **2026-04-08 / 2026-01-23 /
2026-01-11**: discovery + profile structure, checkout lifecycle (incl. escalation /
`continue_url`), order retrieval + adjustments, idempotency, validation/errors,
payment (handlers, credentials, AP2 mandates), discounts + consent, catalog
(search / lookup / get_product / pagination), cart + cart-to-checkout conversion,
fulfillment, eligibility signals, totals invariants, **RFC 9421 signatures**,
**OAuth 2.0 + PKCE identity-linking**, and **order-event webhooks** — each scoped to
the capabilities the target declares.

Coverage is tracked openly: every normative MUST in each version is a
kill-rate-validated check, a documented exemption, or a tracked gap
([spck.dev/coverage](https://spck.dev/coverage)). The profile-schema and some
schema-oracle checks require the native `ucp-schema` validator (not shipped in the
wheel), so they report `not-tested` here; run from the source repo for full fidelity.

Source, methodology, and the self-validating CI harness:
<https://github.com/vishkaty/ucp-conformance>.
