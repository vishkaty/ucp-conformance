# AP2 mandate golden vectors (versioned)

Reference-interop golden vectors for UCP's AP2 mandate layer — SD-JWT delegate
chains on the `~~` wire format. The delegate-chain layer tracks a moving draft,
so every vector set is keyed to the exact revisions it was generated and
verified against; that key is the point of this page.

> Unofficial, community-maintained. Vectors document observed interop at the
> pinned revisions below — they are not conformance verdicts on any
> implementation of the moving chain layer (see
> `conformance/testbed/README.md` for the full doctrine).

## Version key — set 2026-07 (current)

| Layer | Pinned revision |
| --- | --- |
| Delegate SD-JWT chain draft | `draft-gco-oauth-delegate-sd-jwt-00` |
| AP2 reference implementation | `google-agentic-commerce/AP2` @ `e1ea56d` |
| UCP spec | `2026-04-08` @ `a2d8bf0b` |
| Frozen standards under the chain | RFC 9901 (SD-JWT), RFC 8785 (JCS), RFC 7515 (JWS) |

The vectors are cross-verified **two-way** in CI: our independent
frozen-standards verifier accepts reference-minted chains, and the reference
verifier accepts chains minted by our own primitives (gates
`sdjwt-vs-reference` and `ap2-e2e`, run on every change).

## Inventory

All files under `conformance/selfcheck/fixtures/ap2/golden/`:

| Vector | What it establishes |
| --- | --- |
| `checkout_chain.json` | Valid checkout-mandate delegate chain (reference-generated), `~~` wire form |
| `payment_chain.json` | Valid payment-mandate chain |
| `nested/nested_ucp.valid.json` | `merchant_authorization` correctly bound *inside* the mandate (nested binding) |
| `nested/nested_ucp.hash_mismatch.json` | Negative: checkout-hash binding broken — must be rejected |
| `nested/nested_ucp.missing_mauth.json` | Negative: nested authorization absent — must be rejected |
| `nested/nested_ucp.tampered_terms.json` | Negative: terms altered after signing — must be rejected |

The three negatives are deliberately *syntactically* valid: they parse and
verify at the generic frozen layer and are caught only by the nested-binding
verifier. A verifier that accepts any of them is checking shape, not binding.

## Verifying against them

```shell
pip install spck-conformance   # or clone this repo
```

- Frozen tier (always available, no reference needed):
  `conformance/testbed/frozen.py` — RFC 9901/8785/7515 chain math.
- Nested binding: `conformance/testbed/nested.py`.
- Reference tier (when the pinned AP2 reference is installed):
  `conformance/testbed/semantic.py` cross-checks byte-for-byte.

## Known pinned-reference defects (self-expiring)

Two semantic-tier cases encode DRAFT-CORRECT behavior the pinned reference does
not yet have, because we found the bugs and our fix PRs are still open upstream:

| Case | Correct behavior | Pinned-reference bug | Filed |
| --- | --- | --- | --- |
| `e2e.instrument_extensions_survive_signing` | type-specific `PaymentInstrument` extension fields (x402 `payee_address`/`facilitator`) survive parse→sign→verify | fields silently dropped before signing → x402 CP falls open to hard-coded defaults | [AP2#299](https://github.com/google-agentic-commerce/AP2/issues/299) / [PR#329](https://github.com/google-agentic-commerce/AP2/pull/329) |
| `e2e.reject_unconfirmed_closed_binding` | chain-verify rejects, by default, a payment mandate whose closed `transaction_id` binds a different checkout | closed-binding check silently skipped unless the caller opts in via `expected_transaction_id` | [AP2#328](https://github.com/google-agentic-commerce/AP2/issues/328) / [PR#330](https://github.com/google-agentic-commerce/AP2/pull/330) |

Both are registered in `conformance/ci/known_ap2_reference_defects.json`
(acknowledged loudly, never failed) and the register **self-expires**: when a
re-pinned reference produces the correct outcome the entry goes stale-red until
deleted, after which the case enforces. The same correct behavior is asserted
GREEN today against our own frozen-layer primitives (the `payment-binding` tier
of `validate_ap2_e2e.py`).

## Change policy

When the draft or the reference moves, a **new keyed set** is added alongside
this one — existing sets are never mutated. Each set stays a permanent record
of what its pinned revisions actually produced on the wire, so implementers
can diff behavior across draft revisions instead of guessing.

This policy is **mechanized**, not aspirational: every shipped vector is
pinned by sha256 in `conformance/selfcheck/fixtures/ap2/ARCHIVE.lock.json`
and the `ap2-vector-archive` CI gate reds on a mutated or deleted shipped
vector, on an addition recorded in no set, and on a draft/reference **re-pin
that did not mint its new keyed set** — so the ratchet can only ever move one
way.

## The AP2↔UCP bridge

The `ap2-bridge` CI gate tests the cross-protocol seam itself: a mandate the
AP2 reference SDK mints over a real UCP checkout verifies under the UCP
merchant verifier, our UCP-derived mint verifies under the AP2 reference
verifier, and the ES256/JCS UCP-binding negatives (wrong alg, non-JCS
canonicalization, mismatched checkout hash) are rejected. Two pinned-spec
contradictions at that seam are encoded as **self-expiring known-seam defects**
(`conformance/ci/known_ucp_seam_defects.json`, same mechanism as the reference
register above): [ucp#571](https://github.com/Universal-Commerce-Protocol/ucp/issues/571)
(ap2-mandates.md permits ES512, signatures.md defines no P-521 key form) and
[ucp#599](https://github.com/Universal-Commerce-Protocol/ucp/issues/599)
(the `checkout_mandate` schema pattern rejects the very `~~` serialization the
AP2 SDK mints — including both golden wires on this page). When a re-pinned
spec reconciles either seam, the entry goes stale-red until deleted and the
bridge case then enforces the reconciled behavior permanently.
