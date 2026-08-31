# WAIVERS-PROPOSED-2 — P2b wave 2 (lane/p2b2-extract)

Per the mission brief: another lane may be touching
`conformance/coverage/register_completeness_waivers.json`'s mechanics concurrently, so this
lane does not edit that file directly (same posture as the landed `lane/p2b-extract`'s
`WAIVERS-PROPOSED.md`, which this file is a sequel to — that one is untouched). Everything
below is a proposed addition for whoever merges. Each entry is written in the exact shape
`verify_register_completeness.py` expects (`scope_exclusions[]` / `waivers[]` objects), ready
to paste into `register_completeness_waivers.json`. All entries validated by hand against
`validate_waiver()` / `validate_scope()` (class enum, minimum reason length,
`duplicate_of`/`row_id` presence) before listing.

## Running baseline

```
Before this lane (main 976a840):
  2026-08-25:   922 kw    509 covered      95 scope-excl    20 waived   298 missed  (report mode)
```

Per-area entries below update this as areas land.

---

## Area: signatures.md (WBA/EdDSA) — commit 1

12 new rows added: SIG-042..SIG-053 (`conformance/requirements/2026-08-25/signatures.json`).
Covers 13 of the file's 15 unaccounted mandatory-keyword lines (SIG-051 alone accounts for
two flagged lines, L627 and L630, since they are one integrated "locate the member; fail if
absent" obligation read as one sentence). The remaining 2 are waiver candidates below, not
missed extraction.

```
After this file:
  2026-08-25:   922 kw    522 covered     95 scope-excl    20 waived   285 missed  (report mode)
```

`verify_register.py 2026-08-25`: 760/760 verified, 0 line-warnings, 0 FAILED (up from 748/748
pre-lane).

**ucp#699 review** (open upstream, proposes binding signed REST responses to their request —
`@authority`/`@method`/`@path`/`@query`;req + a REQUIRED `created` on REST Response Signing
and REST Response Verification): none of SIG-042..SIG-053 fall inside the `### REST Response
Signing` (L537-L604) or `### REST Response Verification` (L745-L798) sections #699 would
change — all 12 rows sit in Signature Algorithms (L102-105), Key Format/WBA key publishing
(L202-205), WBA Interop (L310-344), Signature Encoding (L500-502), or REST Request
Verification's `Signature-Agent` parsing rules (L627-639), none of which #699 touches. No
lineage caveat needed on this batch.

### Waivers (2)

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/signatures.md",
 "line": 300,
 "class": "non-normative",
 "reason": "'To opt in, a signer makes the following changes to their primary UCP signature. Items marked **MUST** are required by [draft-meunier-webbotauth-httpsig-protocol-00]... consult that draft for full details.' This is a captioning/legend sentence introducing the 7-item numbered list that follows -- it tells the reader which of the 7 items carry a MUST, it does not itself impose an obligation on any party. Each item that is itself normative is separately registered (SIG-044 L310, SIG-045 L318, SIG-046 L321, SIG-047 L329, SIG-048 L338); item 1 (non-normative algorithm guidance) and item 6 (a SHOULD) are correctly not registered as MUST rows."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/signatures.md",
 "line": 415,
 "class": "duplicate",
 "duplicate_of": "SIG-010",
 "reason": "R13 matcher artifact (mid-line quote elision). SIG-010 (source L413-L416) already quotes this exact sentence verbatim: 'Implementations **MUST** use `sha-256`.' Physical line 415 in the vendored file reads 'requiring JSON canonicalization. Implementations **MUST** use `sha-256`. For' -- the trailing 'For' begins the next (unquoted) cross-reference sentence, so the covered_lines_for() substring check (line-text-in-fragment) fails on this one physical line even though SIG-010's quote content plainly covers the MUST. Also: parse_source() only anchors the two endpoints of an 'L413-L416' range (413, 416), not every line in between, so 415 gets no exact-anchor credit either. Confirmed by replaying covered_lines_for([SIG-010], flines) directly: it marks 413/414/416 covered but not 415, for exactly this reason. Not a missed clause; do not add a new row or edit SIG-010 to dodge this -- the matcher is being fixed elsewhere."
}
```

---

## Area: shopping/checkout/index.md — commit 2 (22 rows + this waiver batch)

22 new rows added: CHK-058..CHK-079 (`conformance/requirements/2026-08-25/checkout-lifecycle.json`).
Covers 26 of the file's 37 unaccounted mandatory-keyword lines across four wholly new 08-25
subsystems (quantities-and-units enforcement, Actions/Accepted completion, the JSONPath
`path` format constraint, and the disclosure-Message-to-Policy code linkage) — confirmed by
`grep` that 04-08's `docs/specification/checkout.md` has zero occurrences of the load-bearing
terms for each (`quantity_unit`, `complete_in_progress`'s operation-contract table,
`RFC 9535`/JSONPath, `policies[]` cross-reference).

```
After this file:
  2026-08-25:   922 kw    564 covered     95 scope-excl    20 waived   243 missed  (report mode)
```

`verify_register.py 2026-08-25`: 793/793 verified, 0 line-warnings, 0 FAILED (up from
771/771).

### A real finding: 11 of the 37 lines are a stale-waiver reorg artifact, not missed extraction

The remaining 11 unaccounted lines in this file (811, 817, 818, 819, 821, 833, 1161, 1170,
1336, 1360, 1376) are **not** new/missed content at all. Every one of them is prose that
existed **verbatim** at 2026-04-08 (in `docs/specification/checkout.md`, at different line
numbers, before the 08-25 doc reorg moved the file to `docs/specification/shopping/checkout/index.md`)
and was **already judged and waived there** — as a duplicate of an already-registered row, as
non-normative narrative, or as schema-enforced. Because
`register_completeness_waivers.json` keys each waiver by the exact `(version, file, line)`
tuple, none of those 2026-04-08 waivers apply to 2026-08-25's new path/line numbers, so the
census re-flags the identical, already-adjudicated prose as if it were newly unaccounted.
This is the same class of hazard the mission brief's R13/reorg warnings point at, just in
waivers rather than in the matcher.

Each entry below was verified three ways: (1) the 08-25 line's text matches the 04-08 waiver's
quoted reasoning verbatim or near-verbatim, (2) the 04-08 waiver's `duplicate_of`/`row_id`
target still exists in the 2026-08-25 register with equivalent content (checked directly), and
(3) `grep` over the 04-08 tree confirms no other row already covers the 08-25 line under a
different citation.

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 811,
 "class": "non-normative",
 "reason": "Stale-waiver reorg re-point (see 'A real finding' above). Intro sentence to the Warning Presentation rendering-contract table: 'The presentation field on warning messages controls the rendering contract the platform MUST follow.' Narrative lead-in whose normative cells are already rowed -- the overall rendering contract is ERR-012, and the individual table/prose obligations are ERR-013..ERR-022. Not a distinct server obligation (platform-side rendering, not server-observable). Identical prose was waived at 2026-04-08 docs/specification/checkout.md:332 with this same reasoning; the reorg moved the file/line without re-pointing the waiver."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 817,
 "class": "duplicate",
 "duplicate_of": "ERR-015",
 "reason": "Stale-waiver reorg re-point. Contract-table cell 'Proximity to path | MUST' (disclosure column) summarizes the disclosure prose bullet at L838 that ERR-015 already captures verbatim: platforms MUST display the warning in proximity to the component referenced by path. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:338 (duplicate_of ERR-015, same target row id, unchanged across versions)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 818,
 "class": "duplicate",
 "duplicate_of": "ERR-016",
 "reason": "Stale-waiver reorg re-point. Contract-table cell 'Dismissible | MUST NOT' (disclosure column) summarizes the disclosure prose bullet at L842 that ERR-016 already captures verbatim: platforms MUST NOT hide, collapse, or auto-dismiss the disclosure warning. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:339 (duplicate_of ERR-016, same target row id, unchanged across versions)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 819,
 "class": "duplicate",
 "duplicate_of": "ERR-017",
 "reason": "Stale-waiver reorg re-point. Contract-table cell 'Render image_url | MUST' (disclosure column) summarizes the disclosure prose bullet at L843 that ERR-017 already captures verbatim: platforms MUST render image_url when present for disclosure warnings. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:340 (duplicate_of ERR-017, same target row id, unchanged across versions)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 821,
 "class": "duplicate",
 "duplicate_of": "ERR-018",
 "reason": "Stale-waiver reorg re-point. Table cell '| Escalate if cannot honor | -- | MUST via continue_url |' is the tabular restatement of the disclosure-escalation prose at L850, already captured verbatim by ERR-018 ('Platforms that cannot honor the disclosure rendering contract MUST escalate to merchant UI via continue_url...'). Identical prose was waived at 2026-04-08 docs/specification/checkout.md:342 (duplicate_of ERR-018, same target row id, unchanged across versions)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 833,
 "class": "non-normative",
 "reason": "Stale-waiver reorg re-point. Section lead-in: 'Warnings with presentation: \"disclosure\" carry notices ... that MUST follow the prescribed rendering contract below.' Narrative pointer to the disclosure rendering-contract bullets that immediately follow, each already rowed (ERR-014 display content, ERR-015 proximity, ERR-016 must-not-hide, ERR-017 image_url). Not a distinct obligation. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:354 with this same reasoning; the reorg moved the file/line without re-pointing the waiver."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 1161,
 "class": "duplicate",
 "duplicate_of": "SAE-012",
 "reason": "Stale-waiver reorg re-point. 'Eligibility and policy enforcement MUST occur at checkout time using binding transaction data.' Already captured by SAE-012 (same verbatim requirement; SAE-012 cites the context schema description that mirrors this checkout/index.md prose). Same normative obligation, different source location. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:640 (duplicate_of SAE-012, same target row id, unchanged across versions)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 1170,
 "class": "duplicate",
 "duplicate_of": "SAE-001",
 "reason": "Stale-waiver reorg re-point. 'signal values MUST NOT be buyer-asserted claims -- platforms provide signals based on direct observation or by relaying independently verifiable third-party attestations.' Already captured by SAE-001 (quotes the fuller overview/index.md#Signals statement of the same platform-bound MUST NOT). Platform-provenance obligation, not a business-response wire shape. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:649 (duplicate_of SAE-001, same target row id, unchanged across versions)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 1336,
 "class": "duplicate",
 "duplicate_of": "TOT-007",
 "reason": "Stale-waiver reorg re-point. 'MUST NOT alter the rendered output -- the business's presented totals are authoritative' restates TOT-007's prohibition (platforms MUST NOT substitute their own computed totals for the business's values), applied here to the sum-mismatch verification case. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:810 (duplicate_of TOT-007, same target row id, unchanged across versions). The other MUST NOT in the same paragraph (autonomous completion with mismatched totals) is already a distinct row, CHK-055."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 1360,
 "class": "schema-enforced",
 "row_id": "TOT-004",
 "reason": "Stale-waiver reorg re-point. 'Unknown types MUST include display_text (schema-enforced).' The parenthetical explicitly flags schema enforcement and TOT-004 already captures 'unknown (non-well-known) totals types MUST include display_text' with schema_enforced=true. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:834 (row_id TOT-004, same target row id, unchanged across versions -- confirmed TOT-004 still exists at 2026-08-25 with equivalent content and schema_enforced=true)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/checkout/index.md",
 "line": 1376,
 "class": "duplicate",
 "duplicate_of": "TOT-001",
 "reason": "Stale-waiver reorg re-point. 'The business controls what MUST be rendered (top-level entries)' restates TOT-001 (platforms MUST render all top-level totals entries in the order provided); the contrasting 'MAY optionally surface sub-lines' half of the same sentence is TOT-018. Identical prose was waived at 2026-04-08 docs/specification/checkout.md:850 (duplicate_of TOT-001, same target row id, unchanged across versions)."
}
```
