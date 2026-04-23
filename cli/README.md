# spck — UCP Conformance Testing CLI

Test any [Universal Commerce Protocol](https://ucp.dev) server from the command line.

## Install

```bash
pip install spck
```

Or run directly (zero dependencies, just Python 3.7+):

```bash
curl -O https://raw.githubusercontent.com/vishkaty/ucp-conformance/main/cli/spck.py
python3 spck.py --server https://your-server.com --merchant your.domain.com
```

## Quick Start

```bash
# Run tests (no account needed)
spck --server https://api.example.com --merchant store.example.com

# With API key (results saved to your spck.dev account)
spck --key spck_your_key --server https://api.example.com --merchant store.example.com
```

## Get an API Key

1. Go to [spck.dev/tool](https://spck.dev/tool)
2. Sign in with your email
3. Click **Settings** tab
4. Under **API Keys**, click **Create Key**
5. Copy the key (starts with `spck_`)

Save it for future runs:

```bash
spck --key spck_your_key --save-key --server ...
# Key saved to ~/.spck — no need to pass --key again
```

## Options

```
--server URL         UCP server base URL (required)
--merchant DOMAIN    Merchant domain (required)
--key KEY            API key from spck.dev (syncs reports)
--version VERSION    Spec version: auto, 2026-04-08, 2026-01-23, 2026-01-11
--host-header NAME   Header for merchant routing (default: x-firmly-host)
--headers K=V,...    Extra headers (comma-separated)
--token TOKEN        Payment test token (default: tok_visa)
--json               Output as JSON (for CI/CD pipelines)
--verbose            Show full request/response per test
--save-key           Save API key to ~/.spck
```

## Examples

### Basic test run

```bash
$ spck --server https://api.firmly.work --merchant staging.luma.gift

spck v1.0.0 — UCP Conformance Testing CLI
Server: https://api.firmly.work
Merchant: staging.luma.gift

Discovering server...
  Version: 2026-04-08
  Capabilities: 6
  Payment: gpay
  Product: WJ06-S-Blue (Juno Jacket)

--- A. protocol_test ---
  ✓ PASS  test_discovery (0ms) — v2026-04-08, 6 caps
  ✓ PASS  test_discovery_urls (0ms) — All present
  ...

============================================================
Results: 34 passed, 0 failed, 0 skipped out of 34
============================================================
API calls: 63
```

### JSON output for CI/CD

```bash
spck --server https://api.example.com --merchant store.example.com --json > results.json
```

### With verbose logging

```bash
spck --server https://api.example.com --merchant store.example.com --verbose
```

### Test specific spec version

```bash
spck --server https://api.example.com --merchant store.example.com --version 2026-01-23
```

### Custom headers

```bash
spck --server https://api.example.com --merchant store.example.com \
  --host-header X-Merchant-Domain \
  --headers "Authorization=Bearer token123,X-Custom=value"
```

## What it tests

34 tests across 10 modules mirroring Google's official UCP conformance suite:

| Module | Tests | Coverage |
|--------|:-----:|----------|
| A. Protocol | 4 | Discovery, URLs, versions, error handling |
| B. Checkout Lifecycle | 11 | Create, get, update, cancel, complete, terminal states |
| C. Fulfillment | 3 | Shipping options, tax, totals |
| D. Idempotency | 1 | Create idempotency |
| E. Business Logic | 2 | Totals, buyer persistence |
| F. Validation | 4 | Not found, incomplete, errors, 404 |
| G. Invalid Input | 2 | Missing fields, invalid merchant |
| H. Card Credentials | 2 | Visa, Mastercard via Google Pay |
| I. Order | 1 | Order in response |
| J. Catalog | 4 | Search, empty, pagination, checkout |

## Report sync

When you use `--key`, test results are automatically uploaded to your [spck.dev](https://spck.dev) account. View them in the **My Reports** tab — same as web-based runs. Compare CLI and web runs side by side.

## License

MIT
