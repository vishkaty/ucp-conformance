# WAIVERS-PROPOSED-3 — census-zero wave (lane/census-zero)

Sequel to `WAIVERS-PROPOSED-2.md` (P2b wave 2, landed). Same posture: this lane does not
edit `conformance/coverage/register_completeness_waivers.json` directly — everything below
is a proposed addition for whoever adjudicates, in the exact shape
`verify_register_completeness.py` expects (`waivers[]` / `scope_exclusions[]` objects), ready
to paste. All entries validated by hand against `validate_waiver()` / `validate_scope()`
(class enum, minimum reason length, `duplicate_of`/`row_id` presence) before listing.

Mission: drive the 2026-08-25 census from 125 unaccounted to 0 (report mode until
2026-09-20). Method identical to the landed P2b lanes: verbatim quotes + exact line anchors
vs `conformance/.vendor/ucp-2026-08-25/`, `ucp:` aliases, the repo's testability enum,
RFC-2119 sentences only, `verify_register.py` after every file, `verify_register_completeness.py`
before/after per area.

## Running baseline

```
Before this lane (main 903fc6a):
  2026-08-25:   922 kw    600 covered    142 scope-excl    55 waived   125 missed  (report mode)
```

Per-area entries below update this as areas land.

---

## Area: overview/index.md (remaining concentration) — commit 1

49 new rows added: OVR-028..OVR-076 (`conformance/requirements/2026-08-25/overview.json`).
Covers 63 of the file's 65 unaccounted mandatory-keyword lines — the ~68-line concentration
(65 unique lines; 2 lines each carried 2 keyword hits) the previous wave (P2b wave 2,
OVR-015..027) named as a future wave in its commit message: namespace/schema resolution
(Schema Composition requirements, Extension Schema Pattern `$defs` rules, `requires`
constraint validation, Resolution Flow), the `ucp` protocol-namespace reservation + all 9
`map_order` rules, transport/profile negotiation (Platform Advertisement on Request,
Negotiation Protocol's Platform/Business Requirements lists), webhooks (business-to-platform
signing), identity/security (Streamable-HTTP JSON-RPC error mapping, the Identity Resolution
Algorithm's steps 1/4/5/6, rejection condition, and authenticated-identity normalization,
Instrument Cardinality), and Versioning (Initial Discovery steps 3/4, Request-Time
Validation, Pre-release hygiene, Component Versioning re-certification and the
`dev.ucp.*` version-declaration duty). Also 1 row in `payment.json` (PAY-048, Instrument
Cardinality, folded with an OVR-adjacent line at L2581/L2583) and 3 rows in `discovery.json`
(DISC-007..009, the URL fetch-safety list's no-redirect / special-use-IP / fetch-failure
rules — DISC-002's own notes already flagged DISC-007's line as an unregistered companion).

```
After this file:
  2026-08-25:   922 kw    665 covered    142 scope-excl    55 waived    60 missed  (report mode)
```

`verify_register.py 2026-08-25`: 860/860 verified, 0 line-warnings, 0 FAILED (up from
806/806 pre-lane).

The remaining 2 overview/index.md lines are true duplicates of already-registered rules, not
missed extraction — waived below.

### Waivers (2)

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/overview/index.md",
 "line": 879,
 "class": "duplicate",
 "duplicate_of": "CAP-004",
 "reason": "'For the `schema` URL of an entity whose name is `name`, a platform **MUST** apply the following:' is the lead-in sentence for the numbered Derivation Algorithm list (items 1-4) that CAP-004 (L881, URL parsing), CAP-005 (L886, registered-domain host), CAP-006 (L889, authority_prefix derivation), and CAP-007 (L893, exact/prefixed match) already register individually, one row per list item. This sentence states no obligation beyond 'apply the following steps' -- the steps themselves carry the substance and are each already a row. duplicate_of names CAP-004 as the first/representative item; the full itemization is CAP-004..CAP-007."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/overview/index.md",
 "line": 1736,
 "class": "duplicate",
 "duplicate_of": "DISC-006",
 "reason": "'Platforms **MUST** communicate their profile URI with each request to enable capability negotiation.' (### Platform Advertisement on Request, section intro) restates, in different words, the exact same obligation DISC-006 already registers verbatim from L1780-1781: 'Platforms **MUST** include their profile URI in every request using the transport-appropriate mechanism.' (Negotiation Protocol > Platform Requirements, item 1, Profile Advertisement). Both sentences require the identical thing -- profile URI on every request -- from the identical actor (the platform); L1736 is the section's plain-prose introduction, L1780 is its formal enumerated restatement that DISC-006 covers. Not a second obligation."
}
```

---

## Area: shopping/checkout/mcp.md + rest.md — commit 4

2 new rows added: MCP-010 (`transports.json`, checkout Request Metadata) and CHK-080
(`checkout-lifecycle.json`, checkout REST Request Signing header requirement). Covers 2 of
the 8 unaccounted lines across the two files with real coverage. The other 6 are per-transport
prose echoes of rules the checkout/index.md operation-contract table (CHK-023, CHK-067) and
transports.json (MCP-004) already register verbatim in substance — genuine duplicates, waived
below rather than re-registered a third time. Waivers do not change `verify_register_completeness.py`'s
local count (this lane does not edit `register_completeness_waivers.json`); the 6 lines below
remain counted as "missed" in this lane's own runs until adjudicated.

```
After this file (real coverage only; waivers not yet applied):
  2026-08-25:   922 kw    690 covered    142 scope-excl    55 waived    36 missed  (report mode)
  (of the 36: 8 are waiver-proposed in this file + WAIVERS-PROPOSED-3's overview.md pair; the
   rest await later areas in this same file)
```

`verify_register.py 2026-08-25`: 882/882 verified, 0 line-warnings, 0 FAILED (up from
880/880 pre-file).

### Waivers (6)

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/rest.md",
 "line": 231,
 "class": "duplicate",
 "duplicate_of": "CHK-023",
 "reason": "'Update Checkout is a full replacement operation. The Platform **MUST** send the entire Checkout resource, including any data updates to write-only fields; the supplied resource replaces the existing Checkout session state.' is the REST binding's prose echo of CHK-023 (checkout/index.md#L1078-1081, the operation-contract table's Update Checkout row): 'Performs a full replacement of the checkout resource. The platform is **REQUIRED** to send the entire checkout resource containing any data updates to write-only data fields. The resource provided in the request will replace the existing checkout session state on the business side.' Same obligation (full-resource replacement including write-only fields), same actor (Platform), restated for the REST transport section rather than a second rule."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/rest.md",
 "line": 234,
 "class": "duplicate",
 "duplicate_of": "CHK-067",
 "reason": "'**MUST NOT** start a new Update Checkout operation while the Checkout is `complete_in_progress`.' restates CHK-067 (checkout/index.md#L451, operation-contract table): 'The Platform **MUST NOT** start a new Update Checkout operation.' verbatim in substance -- the REST section's prose intro to the same complete_in_progress freeze rule the table already states."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/rest.md",
 "line": 237,
 "class": "duplicate",
 "duplicate_of": "CHK-067",
 "reason": "'new Update Checkout request in that state, it **MUST** leave the Checkout unchanged and return the current Checkout with a recoverable error Message.' is the same clause CHK-067 already quotes verbatim ('If the Business receives a new Update Checkout request, it **MUST** leave the Checkout unchanged and return the current Checkout with a recoverable error Message.'), restated in the REST binding's own prose paragraph rather than a second rule."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/mcp.md",
 "line": 392,
 "class": "duplicate",
 "duplicate_of": "CHK-067",
 "reason": "'The Platform **MUST NOT** start a new `update_checkout` operation while the Checkout is `complete_in_progress`.' is the MCP binding's prose echo of the same complete_in_progress freeze rule CHK-067 already registers from the checkout/index.md operation-contract table -- identical substance, restated per-transport."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/mcp.md",
 "line": 395,
 "class": "duplicate",
 "duplicate_of": "CHK-067",
 "reason": "'new `update_checkout` request in that state, it **MUST** leave the Checkout unchanged and return the current Checkout with a recoverable error Message.' restates the same clause CHK-067 already quotes verbatim, in the MCP binding's own prose paragraph."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/mcp.md",
 "line": 856,
 "class": "duplicate",
 "duplicate_of": "MCP-004",
 "reason": "'Implementers **MUST** expose this as an MCP `tools/call` endpoint:' is the Complete Checkout example's intro sentence for the exact same OpenRPC-to-MCP `tools/call` transformation MCP-004 already registers verbatim from this file (L824-826): 'Implementers **MUST** apply this transformation:' followed by the method/params.name, params/params.arguments mapping table. Same obligation (implementers must expose OpenRPC operations as MCP tools/call), restated as a worked-example lead-in for the complete_checkout operation specifically rather than a second, distinct rule."
}
```

---

## Area: shopping/order/mcp.md + index.md + rest.md — commit 5

7 new rows added: MCP-011..013 (`transports.json`), ORD-034..037 (`order.json`). Covers 10 of
the 11 unaccounted lines. The 11th is an internal same-file duplicate.

```
After this file (real coverage only):
  2026-08-25:   922 kw    697 covered    142 scope-excl    55 waived    28 missed  (report mode)
```

`verify_register.py 2026-08-25`: 889/889 verified, 0 line-warnings, 0 FAILED (up from
882/882 pre-file).

### Waivers (1)

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/order/mcp.md",
 "line": 292,
 "class": "duplicate",
 "duplicate_of": "MCP-012",
 "reason": "'* **MUST** check the `messages` array in responses before accessing order data' (Conformance list, bullet 2) restates the exact same obligation MCP-012 already registers from this same file's Error Handling section (L284-285): 'Platforms **MUST** check `messages` before accessing order fields.' Same actor (platform), same rule (check messages before touching order fields), restated as a Conformance-checklist bullet rather than a second rule."
}
```

---
