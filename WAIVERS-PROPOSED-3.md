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
