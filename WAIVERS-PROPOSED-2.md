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


---

## Area: embedded checkout + embedded-protocol (adjudicated file by file)

Per the brief's warning ("this is the scope-exclusion-candidate class -- adjudicate
honestly file by file... the landed lane's tokenization-guide precedent shows blanket
exclusion can be WRONG, read before excluding"): both `embedded-protocol.md` (47
unaccounted lines) and `shopping/cart/embedded.md` (28 unaccounted lines) were read in
full, end to end, before any exclusion/waiver was proposed.

**Verdict: `embedded-protocol.md` is genuinely 100% out-of-scope** (a browser
host<->iframe postMessage/MessageChannel/native-webview transport spec -- JSON-RPC
message format, handshake, auth escalation, session error, lifecycle/state-change
notifications, error codes, CSP/iframe-sandbox/credentialless-iframe security -- nothing
in any of its 557 lines is a server HTTP/MCP endpoint behavior). This is not a new
finding: the register already scope-excludes this exact file for 2026-04-08 with this
exact rationale. The file did not move in the 08-25 reorg (same path,
`docs/specification/embedded-protocol.md`) and a full read confirms the content is
unchanged in kind. **Proposed fix: extend the existing scope exclusion's `versions` list
to add `2026-08-25`, not add a new entry or touch its `file`/`reason`.**

**Verdict: `shopping/cart/embedded.md` is NOT 100% out-of-scope.** It is the 2026-08-25
successor of `embedded-cart.md` (scope-excluded at 04-08 with the same browser-transport
rationale), and 27 of its 28 unaccounted lines are exactly that -- ECaP's own postMessage
handshake/payload/notification mechanics, the literal browser `MessagePort` type, host-side
URL construction. But one line is not: L86 requires the Cart REST/MCP API **response
body** itself to include an embedded service binding with `config.delegate` when ECaP is
offered for that session -- a real, server-observable requirement, extracted as CART-034
in the previous commit. Because a scope exclusion in `verify_register_completeness.py` is
whole-file with no per-line carve-out (`excluded = (ver, rel) in scope_idx` short-circuits
every occurrence in the file before any row is even checked), scope-excluding this file
would have silently hidden CART-034's own row from ever being credited or checked --
exactly the kind of over-broad exclusion the brief's tokenization-guide precedent warns
against. **Proposed fix: do not scope-exclude this file; waive its other 27 lines
individually (below).**

```
Before this batch:
  2026-08-25:   922 kw    582 covered     95 scope-excl    20 waived   225 missed  (report mode)

If the embedded-protocol.md scope-exclusion extension is applied (+47 scope-excl):
  -> scope-excl 95 -> 142, missed 225 -> 178

If the 27 shopping/cart/embedded.md waivers below are ALSO applied (+27 waived):
  -> waived 20 -> 47, missed 178 -> 151
```

### Scope exclusion extension (not a new entry -- extend the existing one)

The existing entry in `register_completeness_waivers.json` (`scope_exclusions[]`):

```json
{
 "file": "docs/specification/embedded-protocol.md",
 "versions": ["2026-04-08"],
 "class": "out-of-scope",
 "reason": "The Embedded Protocol (EP) itself: window.postMessage / MessagePort channel establishment and message framing between a host page and an embedded commerce UI. Purely a browser client-side transport contract; nothing here is observable at a server endpoint, so it is outside a server-conformance checker's testable surface."
}
```

should become:

```json
{
 "file": "docs/specification/embedded-protocol.md",
 "versions": ["2026-04-08", "2026-08-25"],
 "class": "out-of-scope",
 "reason": "The Embedded Protocol (EP) itself: window.postMessage / MessagePort channel establishment and message framing between a host page and an embedded commerce UI. Purely a browser client-side transport contract; nothing here is observable at a server endpoint, so it is outside a server-conformance checker's testable surface. Re-verified against the 2026-08-25 vendor tree (same path, unmoved by the reorg; full 557-line read confirms no server-observable content was added -- JSON-RPC message format, handshake/auth/session-error/lifecycle/state-change message patterns, error codes, and CSP/iframe-sandbox/credentialless-iframe security all remain browser-transport-only)."
}
```

### Line waivers: `shopping/cart/embedded.md`'s other 27 lines (non-normative)

```json
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 137,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Host-side URL-construction step (augmenting continue_url with ep_* query params before loading the iframe) -- browser/host behavior, no server endpoint to probe."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 145,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. ep_version is a URL query parameter the host sets when loading the embedded iframe URL, not a field in any server response."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 162,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. 'in all responses' here means the ECaP JSON-RPC responses exchanged over the postMessage/MessagePort channel during the embedded session, not the original Cart REST/MCP response -- browser-transport session state, not server-observable."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 164,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Same ECaP session-bound version-echo rule as L162 (its MUST NOT half); browser-transport session state."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 185,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Conformance requirement on the Embedded Cart's own JS/webview implementation (which JSON-RPC methods it must handle) -- not a server response shape."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 200,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. The ep.cart.ready handshake broadcast is sent over postMessage by the embedded iframe's own code; no server endpoint emits or receives it."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 209,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. delegate is a field of the ep.cart.ready postMessage request payload sent from the iframe to the host, not a server response field."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 210,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Subset-of-config.delegate constraint on the same ep.cart.ready postMessage payload field as L209."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 237,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Host's obligation to respond to the ep.cart.ready postMessage request -- host-side browser behavior."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 243,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. ucp REQUIRED field of the host's ep.cart.ready postMessage RESPONSE (over the browser transport), not a REST/MCP response."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 244,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. status MUST be success/error on the same postMessage response as L243."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 248,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. credential/upgrade mutual-exclusion rule on the same ep.cart.ready postMessage response."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 252,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. credential MUST-be-set-if-auth-requested rule on the same postMessage response."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 253,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. credential MUST-NOT-be-set-if-upgrade-present rule on the same postMessage response (paired with L248)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 272,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. port MUST be a MessagePort object -- literally a browser API type, the clearest possible marker of browser-transport-only content."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 292,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Embedded Cart's channel-upgrade handling (discard/switch/re-send over the new MessagePort) -- iframe-side browser behavior."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 295,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Same channel-upgrade rule as L292 (the MUST-only-over-upgraded-channel half)."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 299,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Host's error_response behavior on a failed postMessage handshake -- host-side browser behavior."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 301,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Host's tear-down/redirect behavior on a handshake error -- host-side browser behavior, paired with L299."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 302,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Embedded Cart's MUST-NOT-send-further-messages rule after a handshake error, paired with L299/L301 on the same postMessage error path."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 316,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Error-escalation notification (ep.cart.error) issued by the Embedded Cart over the postMessage channel -- browser transport, not a server endpoint."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 334,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. cart REQUIRED payload field of the ep.cart.start postMessage notification -- the notification itself travels over the browser transport, not as a REST/MCP response."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 369,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Same REQUIRED-payload pattern as L334, for the ep.cart.complete postMessage notification."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 405,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Same REQUIRED-payload pattern as L334, for the ep.cart.line_items.change postMessage notification."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 433,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Same REQUIRED-payload pattern as L334, for the ep.cart.buyer.change postMessage notification."
},
{
 "version": "2026-08-25",
 "file": "docs/specification/shopping/cart/embedded.md",
 "line": 461,
 "class": "non-normative",
 "reason": "Part of the Embedded Cart Protocol (ECaP) browser host<->iframe postMessage/MessageChannel transport layer (shopping/cart/embedded.md), which -- apart from the one Cart-response requirement extracted as CART-034 -- binds the host application and the embedded iframe's own JS/webview code, not the server's REST/MCP API; not observable by a server-endpoint conformance checker without a headless-browser harness driving the actual postMessage/MessagePort handshake. Same rationale as the existing embedded-protocol.md and embedded-cart.md (now this path) scope exclusions, applied per-line here because this one file also carries CART-034's genuine server-observable row. Same REQUIRED-payload pattern as L334, for the ep.cart.messages.change postMessage notification."
}

```
