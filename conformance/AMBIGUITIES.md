# Ambiguity / Discrepancy Register

Verified contradictions, version deltas, and under-specified areas. Each has a
**chosen interpretation + rationale**, and is **surfaced in the report as a flag** —
never silently resolved in the server's favor. Tests cite these ids (e.g. `AMB-001`).

| id | Issue | Evidence (pinned SHA) | Decision | Surfaced as |
|----|-------|-----------------------|----------|-------------|
| **AMB-001** | Version-incompatible status: spec says **422**, official suite asserts **400**. | `ucp:overview.md#L699` (422 mapping) vs `conformance:protocol_test.py::test_version_negotiation` (asserts 400). Suite is pinned to spec **2026-01-23**, predates 04-08 error table. | **Spec authoritative → accept 422.** If 400 observed, do not fail outright. | `advisory` flag: "server returned 400; spec 2026-04-08 maps version_unsupported→422; official suite (01-23) still expects 400". |
| **AMB-002** | The `v2026-04-08` git tag is dated **2026-05-22** and carries post-date cherry-picks. | `SOURCES.lock.json` (commit a2d8bf0b, date 2026-05-22). | Pin by SHA, treat the **tagged artifact** as "04-08"; never infer contents from the date. | Documented in SOURCES.lock + scope stamp shows the commit SHA. |
| **AMB-003** | Header scheme differs by version: 01-23 = `Request-Signature` (detached JWT, RFC 7797); 04-08 = RFC 9421 `Signature`/`Signature-Input`. `request-id` is suite-only (no spec basis). | `ucp:signatures.md` (04-08) vs 01-23 docs; `conformance:integration_test_utils.py::get_headers`. | **Version-gate** header expectations. Do NOT require `request-id`. Do NOT require `request-signature` on 04-08. | Per-version test wiring; report notes the scheme tested. |
| **AMB-004** | Totals invariants `sum(non-total)==total` and sub-line `sum(lines)==parent` are **prose-only, not in `totals.json`**. | `ucp:checkout.md` (prose) vs `ucp:schemas/shopping/types/totals.json` (no such constraint). | Implement as **coded checks** in addition to ucp-schema validation. | A passing ucp-schema validation does NOT imply totals consistency; both run. |
| **AMB-005** | `severity: unrecoverable` exists in 04-08 but **not** in 01-23's enum. | `ucp:schemas/.../message_error.json` (04-08 enum has 4 values) vs 01-23 (3). | **Version-gate** the accepted severity enum. | Report flags an `unrecoverable` severity when grading an 01-23 target. |
| **AMB-006** | `capabilities_incompatible` returns **HTTP 200 with error body**, not a 4xx. | `ucp:overview.md#L700` (200/result). | Expect 200-with-error-body; a 4xx here is a deviation. | `deviation` if a 4xx is returned for the no-intersection case. |
| **AMB-007** | A previously-claimed "platforms MUST strip scripts/untrusted HTML from product/variant descriptions" requirement does **NOT exist** in the pinned 04-08 spec. | `grep -rinE 'strip\|sanitiz\|untrusted\|script\|xss' docs/ source/schemas/` returns only schema-authoring boilerplate and the marketing homepage's own HTML — no normative rule. | **Do NOT add a conformance check for it** (testing a non-requirement = false-FAIL). The tester's OWN output escaping is a separate tool-security concern, not a UCP requirement. | n/a — requirement removed from scope; documented so it isn't reintroduced. |

## How flags render
A flag is a non-failing annotation attached to a check result. It appears in the
report next to the affected check, carries the `AMB-id` and a one-line explanation,
and is counted in the summary. Flags never flip an aggregate verdict by themselves,
but a check that depends on an ambiguous interpretation can only be `clean-pass`
when it matches the **spec-authoritative** reading; any other reading is `advisory`
or `deviation` per the table above.

## DSC-003 — case-insensitive discount codes (reference server does NOT satisfy)
The spec (2026-01-23 DSC-003) says discount codes MUST be matched case-insensitively.
The Flower Shop reference server rejects a lowercased form of a valid code (e.g. `10off`
for seeded `10OFF`), so it does not implement DSC-003. A check for this deviates on the
known-good server and therefore cannot be reference-gated against it; deferred until a
golden that implements case-insensitive matching is available. Discovered 2026-07-01.

## ERR-001 — error message envelope (reference server uses a different shape)
Spec 2026-01-23 ERR-001 says an error message MUST include type, code, content, and
severity. The Flower Shop reference server instead returns `{"detail": "...", "code":
"..."}` on a 400 (e.g. out-of-stock). So it satisfies VAL-006 (populated `detail`) but
NOT the ERR-001 four-field envelope. A strict ERR-001 check would deviate on the
known-good server, so it's not reference-gateable against Flower Shop; deferred.
Discovered 2026-07-01.
