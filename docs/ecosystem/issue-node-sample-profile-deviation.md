# Ready-to-file issue — Universal-Commerce-Protocol/samples

**Where to file:** https://github.com/Universal-Commerce-Protocol/samples/issues/new
**Tone:** helpful and deferential — you're contributing a fix, not dunking. Attach the
repro so a maintainer can confirm in 30 seconds.

---

**Title:** Node.js REST sample: `/.well-known/ucp` profile shape doesn't match the profile schema (`capabilities` array, `services.<name>` object)

**Body:**

Thanks for the reference implementations — they're really useful for building against UCP.

While testing tooling against the samples I noticed the **Node.js REST sample**
(`rest/nodejs`) serves a discovery profile whose *shape* differs from the profile schema
(`schemas/ucp.json`), in two places. The **Python sample** and a real production Shopify
store both use the schema-correct shapes, so this looks specific to the Node sample.

### Observed

`GET http://localhost:3000/.well-known/ucp` returns:

```json
{
  "version": "2026-01-23",
  "services": {
    "dev.ucp.shopping": {                 // ← object
      "version": "2026-01-23",
      "spec": "...",
      "rest": { "schema": "...", "endpoint": "http://localhost:3000" }
    }
  },
  "capabilities": [                        // ← array
    { "version": "2026-01-23", "spec": ".../shopping/checkout", "schema": "..." },
    ...
  ]
}
```

### Expected (per `schemas/ucp.json`)

- **`capabilities` MUST be an object** keyed by reverse-domain capability names
  (`ucp.json` defines `capabilities` as `type: "object"` with `propertyNames`), e.g.
  `{ "dev.ucp.shopping.checkout": [ { "version": "..." } ], ... }`.
- **`services.<name>` MUST be an array** of `{ transport, endpoint, ... }` entries
  (`ucp.json` defines each service value as `type: "array"` of `service.json#/$defs/base`),
  e.g. `"dev.ucp.shopping": [ { "transport": "rest", "endpoint": "...", ... } ]`.

The Python sample and Shopify (`*.myshopify.com/.well-known/ucp`) both emit the keyed
object + array-of-services form, which validates against `ucp.json`.

### Reproduce

```bash
git clone https://github.com/Universal-Commerce-Protocol/samples
cd samples/rest/nodejs && npm install && npm run dev
curl -s http://localhost:3000/.well-known/ucp | jq '.capabilities, .services'
# capabilities is an array; services.dev.ucp.shopping is an object
```

(Observed on the current `main`. Validating the profile against `schemas/ucp.json` with
the `ucp-schema` validator reports both mismatches.)

Happy to open a PR aligning the Node profile builder with `ucp.json` if that's welcome.

---

*(Found while building spck-conformance, an unofficial conformance checker. Filing this as
a plain, reproducible bug report — the goal is to help the samples, not to promote.)*
