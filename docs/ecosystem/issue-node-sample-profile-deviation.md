# Ready-to-file issue — Universal-Commerce-Protocol/samples

**Verified** with the official `ucp-schema` validator against the sample's actual output
(inner `ucp` object vs `business_schema`), and cross-checked against the spec's Business
Profile Example (`ap2-mandates.md`) and what production Shopify stores serve. Only the two
confirmed field-shape mismatches are claimed.

**File at:** https://github.com/Universal-Commerce-Protocol/samples/issues/new

---

**Title:**
```
Node.js sample: /.well-known/ucp `capabilities` is an array (should be a keyed object) and `services.<name>` is an object (should be an array)
```

**Body:**
```markdown
The Node.js REST sample's `/.well-known/ucp` uses two field shapes that don't match
`schemas/ucp.json`. The Python sample and production Shopify stores use the schema-correct
shapes.

**Observed** (current `main`, [`rest/nodejs/src/api/discovery.ts`](https://github.com/Universal-Commerce-Protocol/samples/blob/main/rest/nodejs/src/api/discovery.ts)):

- `ucp.capabilities` → a JSON **array**
- `ucp.services["dev.ucp.shopping"]` → an **object** (with a `rest` key)

**Expected** (per `schemas/ucp.json`):

- `capabilities` → an **object keyed by reverse-domain capability name**, each value an
  array of entries — e.g. `{ "dev.ucp.shopping.checkout": [ { "version": "…" } ] }`
  (see the Business Profile Example in `specification/ap2-mandates.md`).
- `services.<name>` → an **array** of `{ transport, endpoint, … }` entries.

**Reproduce:**

```bash
cd rest/nodejs && npm install && npm run dev
curl -s http://localhost:3000/.well-known/ucp | jq '.ucp.capabilities, .ucp.services'
```

Validating the profile against `schemas/ucp.json` with the `ucp-schema` validator reports
both mismatches:
`/capabilities … is not of type "object"` and
`/services/dev.ucp.shopping … is not of type "array"`.

Happy to open a PR aligning the Node profile builder with `ucp.json`.
```

---

*Note: the profile is nested under a top-level `ucp` key — the repro path is
`.ucp.capabilities`, not `.capabilities`. Confirmed genuine only after validating the
inner object with the official validator (not by eye).*
