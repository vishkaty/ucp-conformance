# DRAFT upstream test-gap filings from the P2-13 comparison — HELD, not filed

Status: **DRAFT ONLY.** Nothing here has been filed. Owner review required
(per ucp-upstream-work protocol: verify against a fresh upstream checkout at
filing time, re-run the repro, check for duplicates, file under vishkaty).

Framing rule for all filings below: collaborative, "making the official
conformance stack more trustworthy" — the comparison exists because the
official suite is the certification baseline; these are test-gaps our mutation
harness surfaced, offered with repros and (where appropriate) patches. No
mention of head-to-head numbers in the filings themselves.

---

## Filing 1 (conformance + python-sdk, two layers): a 4xx error envelope with `ucp.status: "success"` passes the suite

**Found by:** mutant `err-ucp-status-success` — sticky proxy rewrites
`ucp.status` to `"success"` on every 4xx from `/checkout-sessions*` of the
flower reference. The full official suite stays green (76 tests, 0 new
failures, pinned `6ee0b53c`).

**Spec ground:** `error_response` — the resourceless error shape — requires
`ucp.status` to be `"error"` (checkout.md "Error Response"; the schema's ucp
metadata error branch is a const). A server that rejects a request while
stamping the envelope `success` is emitting a self-contradictory body that a
platform may machine-read as success.

**Why the suite misses it (two layers):**

1. **python-sdk model wiring.** `shopping/types/error_response.py` types the
   envelope as the *generic* metadata:
   ```python
   class ErrorResponse(BaseModel):
       ucp: ucp_1.UcpMetadata          # status: Literal["success","error"] | None = "success"
   ```
   while the same module already defines the correct error branch
   (`UcpMetadataError`, `status: Literal["error"]`). So `ErrorResponse(**data)`
   happily validates `{"ucp": {"status": "success"}, "messages": [...]}`.
   One-line fix: use the error-branch metadata type in `ErrorResponse`
   (mirrors the schema's const).
2. **conformance assertion.** `validation_test._assert_structured_4xx_error`
   validates via `ErrorResponse(**data)` + a substring check on
   `messages[].content`; with the model permissive (layer 1), nothing pins
   `ucp.status == "error"`. Even after the SDK fix, an explicit
   `assertEqual(error_resp.ucp.status, "error")` (or relying on the fixed
   model's ValidationError) is worth a regression test, since the suite's
   in-band 2xx branch DOES check the resourceless `ucp.status == "error"`
   posture — the 4xx branch is the only one that doesn't.

**Repro (against their own reference):** boot the flower sample; run the suite
through `conformance/compare/sticky_proxy.py --mutate
'set-field:ucp.status="success"' --match-path '^/checkout-sessions'
--match-status 4xx` (or hand-patch the sample's error path to stamp
`success`); the suite stays green. Our runner flags it as an ERR-029 deviation
(`checkout.unavailable_all_error_envelope`).

**Proposed shape:** python-sdk PR (model fix + unit test) and a conformance PR
(4xx envelope-consistency assertion + regression test), cross-referenced.

---

## Filing 2 (samples): flower server 500s on a wrong-typed top-level `id` in checkout create

**Found by:** our fuzz gate (`conformance/ci/fuzz_gate.py`), not the mutant
comparison — surfaced while re-validating this branch, and it reproduces
byte-identically on unmodified main, so it arrived with the 2026-08-11
`reference_sample_server` re-pin (or the ucp-sdk 0.4.4 pairing), not with this
lane.

**Defect:** `POST /checkout-sessions` with `id` set to an integer / boolean /
array / object returns **500 Internal Server Error** (bare text body) instead
of a 4xx with the UCP error envelope. Four distinct reproducers, deterministic.
(At 2026-04-08, top-level `id` is `ucp_request: omit` on requests — an
unexpected/typed-wrong member must not crash the server.)

**Proposed shape:** samples issue with the four reproducers; once filed,
register the four crashes in `known_fuzz_defects.json` with the issue link so
our fuzz gate acknowledges them (self-expiring when upstream fixes). Until
then the `fuzz` gate is legitimately RED on this pre-existing upstream defect
— on this branch AND on main.

---

## Deliberately NOT drafted (sparing-offers discipline)

- **Header-contract surfaces** (`Cache-Control` on the discovery profile,
  `Content-Type: application/json` on responses): the official suite has no
  response-header assertions anywhere; that is a *surface* choice, not a bug in
  an existing test. Tracked internally as a possible future suggestion only —
  offering a whole new test surface unprompted dilutes credibility
  (ucp-conformance-offers-sparingly).
- **Everything the official suite caught** (17/18 on the common set at pinned
  `6ee0b53c`) — the suite is genuinely strong on this surface, and several of
  its newest catches come from upstream fixes merged from our earlier filings
  (#74–#81). Worth saying in any public write-up.
