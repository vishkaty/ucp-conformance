# UCP Conformance Testing Tool

Test any [Universal Commerce Protocol](https://ucp.dev) implementation against the official spec.

## What it does

- **42 tests** across 10 modules mirroring Google's official UCP conformance suite
- Tests discovery, checkout lifecycle, fulfillment, idempotency, business logic, validation, input handling, payment credentials, orders, and catalog
- **Full request/response logging** for every API call — timestamps, headers, bodies, status codes, timing
- **Multi-version support** — test against spec versions 2026-04-08, 2026-01-23, 2026-01-11, or all at once
- **Auto-detects deviations** from the UCP spec and reports them
- **Downloadable reports** in JSON (full log) and Markdown formats
- **Save reports** to your account for future reference

## Quick Start (local)

```bash
# No build step needed — it's a single HTML file
cd public
python3 -m http.server 8080
# Open http://localhost:8080
```

## Test Modules

| Module | Tests | Coverage |
|--------|:-----:|----------|
| A. protocol_test | 4 | Discovery, URLs, version negotiation, error handling |
| B. checkout_lifecycle | 11 | Create, get, update, cancel, complete, terminal state immutability |
| C. fulfillment_test | 3 | Shipping options, tax calculation, totals consistency |
| D. idempotency_test | 1 | Create idempotency |
| E. business_logic_test | 2 | Totals on create, buyer persistence |
| F. validation_test | 4 | Product not found, complete without fulfillment, error structure, 404 |
| G. invalid_input_test | 2 | Missing currency, invalid merchant |
| H. card_credential_test | 2 | Google Pay with Visa and Mastercard tokens |
| I. order_test | 1 | Order data in complete response |
| J. catalog_test | 4 | Search, empty results, pagination, catalog-to-checkout |

## Deploy to Cloudflare

### Frontend (Cloudflare Pages)

1. Connect this repo to Cloudflare Pages
2. Set build output directory to `public`
3. No build command needed

### Backend (Cloudflare Workers) — optional, for auth & saved reports

```bash
cd api

# Create KV namespaces
wrangler kv:namespace create OTP_STORE
wrangler kv:namespace create SESSIONS
wrangler kv:namespace create USERS
wrangler kv:namespace create REPORTS

# Update wrangler.toml with the namespace IDs from above

# Set email delivery API key (Resend)
wrangler secret put RESEND_API_KEY

# Deploy
wrangler deploy
```

### Environment Variables

| Variable | Required | Description |
|----------|:--------:|-------------|
| `RESEND_API_KEY` | Yes | [Resend](https://resend.com) API key for sending OTP emails |
| `FROM_EMAIL` | No | Sender email address (default: `noreply@ucp-conformance.dev`) |

## Configuration

All fields are configurable in the UI:

| Field | Default | Description |
|-------|---------|-------------|
| Server Base URL | `https://api.firmly.work` | The UCP server to test |
| Merchant Domain | `staging.luma.gift` | Merchant domain for multi-tenant servers |
| Spec Version | `2026-04-08` | UCP spec version to test against |
| Test Product ID | `EYE001` | Product ID for checkout tests |
| Payment Token | `tok_visa` | Stripe test token for payment completion |
| Host Header | `x-firmly-host` | Header name for merchant routing |

## How it works

The tool runs entirely in the browser. It makes direct API calls to the UCP server using `fetch()`. The server must have CORS enabled for the tool's origin.

Each test:
1. Makes one or more API calls
2. Validates the response against spec requirements
3. Logs the full request/response for traceability
4. Reports pass/fail with details

No data is sent to any third party. Reports are stored in Cloudflare KV if you deploy the backend.

## License

MIT
