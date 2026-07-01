# Merchant conformance runner (unofficial)

Point this at **any** UCP server and get an honest, capability-scoped conformance
report. It is not a certification and not affiliated with the official UCP project —
it reports only the checks it actually runs.

```bash
python3 conformance/checks/merchant.py --server https://api.example.com \
    [--config merchant.json] [--json]
```

## How it decides what to test

1. **Discovery.** It fetches `/.well-known/ucp` and reads the server's declared spec
   version and capabilities.
2. **Core checks** (discovery structure, `UCP-Agent` enforcement, idempotency,
   validation) run on **every** server — no seeded data required.
3. **Extension checks** (fulfillment, order, discount, …) run **only if the server
   declares that capability**. Otherwise they are `not-applicable` and are excluded
   from the score — a lean-but-correct merchant is not punished for features it
   doesn't implement.
4. **Data-dependent checks** (completing a real payment, out-of-stock, failing
   payment) need concrete inputs only the merchant knows. Supply them via `--config`;
   without them those checks are `not-tested`, never a silent pass.

The verdict denominator is the set of **applicable, testable MUSTs** for the server's
spec version, so the coverage percentage is honest for that specific server.

## Verdict semantics

| status | meaning |
|---|---|
| `clean-pass` | requirement observed satisfied **and** the check is kill-safe (proven to catch its own defects) |
| `deviation` | a MUST was violated |
| `not-applicable` | the server does not declare the capability this check needs |
| `not-tested` | the check needs `--config` data (or a product) that wasn't provided |

The aggregate is **never green** unless every applicable MUST is a kill-safe pass or an
explicit `not-tested`/`not-applicable`. Partial coverage yields `INCOMPLETE`, not a
false ✓.

## Every check is proven sound before it can grade you

A check that quietly always-passes, or that flags a defect that isn't real, is worse
than no check. So each merchant check must pass a **reference gate** first: run against
the known-good reference server, it has to both clean-pass **and** be kill-safe (catch
every one of its injected mutations). Run it yourself:

```bash
python3 conformance/selfcheck/validate_merchant_checks.py --server http://localhost:8182
```

If any check deviates on the known-good server it's a false-deviation generator; if any
mutation survives it can false-pass. Either fails the gate before it ships.

## The `--config` file

Everything is optional; provide only what applies to your server. See
[`conformance/merchant.config.example.json`](../conformance/merchant.config.example.json)
for a complete, working example (it is the reference server's own config, and is what
the reference gate uses).

| key | type | unlocks |
|---|---|---|
| `product_id` | string | a real, in-stock product id → lifecycle checks (create, GET, idempotency, fulfillment). Auto-discovered if the server supports `catalog.search`. |
| `currency` | string | currency for created sessions (default `USD`). |
| `payment_handlers` | array | payment-handler descriptors echoed on create. |
| `fulfillment_option_id` | string | a valid fulfillment option id so a session can reach `ready_for_complete`. |
| `complete_payment` | object | a payment body that **succeeds** → `checkout.complete_order`, `payment.no_credential_echo`. |
| `fail_payment` | object | a payment body with a **known-failing** token → `validation.payment_failure` (expects 402). |
| `out_of_stock_id` | string | a product id known to be out of stock → `validation.out_of_stock` (expects 4xx). |
| `discount` | object | `{valid_code, invalid_code}` → discount checks (`single_applied`, `accept_one_reject_one`, `unknown_code_rejected`). Requires the `dev.ucp.shopping.discount` capability. |

`complete_payment` / `fail_payment` follow the UCP complete-checkout body shape:

```json
{ "payment": { "instruments": [ { "credential": { "type": "token", "token": "…" }, "…": "…" } ] }, "risk_signals": {} }
```

## What's covered today

16 kill-rate-validated checks spanning discovery, checkout lifecycle, idempotency,
validation, fulfillment, order completion, payment-credential handling, and discounts
— every one proven sound against the reference server. Coverage is reported honestly as
a fraction of the applicable MUSTs; it is **not** full spec coverage, and the report
says so.

> Unofficial, independent project. Not affiliated with, endorsed by, or a substitute
> for the official UCP conformance suite.
