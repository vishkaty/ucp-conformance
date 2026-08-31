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
