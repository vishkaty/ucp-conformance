#!/usr/bin/env python3
"""
matrix.py — the AP2 mandate 49-case matrix (ops/ap2-e2e-testbed-design-2026-07-16
§3, groups A–H) as DATA. Every row is LAYER-TAGGED and BOUND to the executable
case(s) that prove it, so a failure anywhere in the testbed attributes to the
right certification layer, and the matrix can never silently drift from what
actually runs (validate_ap2_matrix.py enforces both directions).

Layers (§2 of the design doc):
  canonical    — ap2-protocol.org mandate model + the frozen standards under it
                 (RFC 9901 SD-JWT / RFC 8785 JCS / RFC 7515 JWS)
  ucp-binding  — UCP's concrete binding (ES256, JCS, checkout_hash/transaction_id
                 identity, nested merchant_authorization)
  reference    — the moving delegate-chain draft, oracle = the PINNED reference
                 SDK (interop observations, never verdicts — testbed README)

Binding tiers (where the executable case lives):
  frozen      frozen.FROZEN_MUTANTS name, or "golden" (the goldens' accept path)
  nested      the committed nested_ucp.<name> golden (validate_ap2_e2e nested tier)
  structural  structural.CASES id      (hermetic — runs everywhere, our code only)
  semantic    semantic.CASES id        (reference-wrapped — runs when SDK present)

The ORACLE of a row is derived, never hand-tagged: a row is `independent` if at
least one binding is frozen/nested/structural (our own frozen-standard code —
these rows MUST hold even where the reference SDK's soft install failed, the
P1-9 CI-weakness rule); a row whose only bindings are semantic is `reference`.

`xrefs` name additional cases that exercise the same property at another layer
(counted for reverse-completeness, not for the row's oracle).
"""

GROUPS = {"A": "structural/schema", "B": "SD-JWT wire format",
          "C": "issuer signature", "D": "key binding / consent",
          "E": "replay / freshness / audience", "F": "chain / delegation",
          "G": "constraint evaluation", "H": "cross-mandate integration"}

LAYERS = ("canonical", "ucp-binding", "reference")

# Semantic cases deliberately OUTSIDE the 49-row matrix (each must justify why).
EXTRAS = {
    "e2e.instrument_extensions_survive_signing":
        "AP2#299 register case — signing fidelity of instrument extension "
        "fields; a reference-SDK finding of ours, not a matrix row",
}

# (case_no, group, title, layer, bindings, xrefs, note)
ROWS = [
    (1, "A", "valid closed checkout mandate verifies end-to-end", "canonical",
     [("structural", "st.checkout_valid_accepts")], [], ""),
    (2, "A", "valid closed payment mandate verifies + binds its checkout",
     "canonical", [("structural", "st.payment_valid_accepts")], [], ""),
    (3, "A", "valid open mandate: constraints[] + on-curve cnf key", "canonical",
     [("structural", "st.open_wellformed")], [], ""),
    (4, "A", "closed mandate missing required checkout_hash", "canonical",
     [("structural", "st.reject_missing_checkout_hash")], [], ""),
    (5, "A", "wrong vct literal", "canonical",
     [("structural", "st.reject_wrong_vct")], [], ""),
    (6, "A", "open mandate missing cnf", "canonical",
     [("structural", "st.reject_open_missing_cnf")], [], ""),
    (7, "A", "checkout_hash != H(checkout_jwt)", "ucp-binding",
     [("nested", "hash_mismatch")],
     [("semantic", "e2e.reject_checkout_hash_mismatch")], ""),
    (8, "A", "payment transaction_id != H(checkout_jwt)", "ucp-binding",
     [("structural", "st.reject_payment_wrong_checkout")],
     [("semantic", "e2e.reject_transaction_id_mismatch")], ""),
    (9, "A", "bad ISO-4217 currency / non-integer minor-unit", "canonical",
     [("structural", "st.reject_bad_currency")], [], ""),
    (10, "B", "correct ~/~~ + trailing-tilde wire parses and verifies",
     "canonical", [("frozen", "golden")], [], ""),
    (11, "B", "malformed serialization", "canonical",
     [("frozen", "broken_chain_separator")], [], ""),
    (12, "B", "orphan disclosure (digest in no _sd)", "canonical",
     [("frozen", "orphan_disclosure")], [], ""),
    (13, "B", "duplicate digest", "canonical",
     [("frozen", "duplicate_disclosure")], [], ""),
    (14, "B", "tampered disclosure (digest != _sd)", "canonical",
     [("frozen", "tampered_disclosure")], [], ""),
    (15, "B", "unknown/insecure _sd_alg", "canonical",
     [("frozen", "unknown_sd_alg")], [], ""),
    (16, "B", "_sd_alg below top level", "canonical",
     [("frozen", "nested_sd_alg")], [], ""),
    (17, "B", "malformed array placeholder", "canonical",
     [("frozen", "malformed_placeholder")], [], ""),
    (18, "C", "valid merchant sig on checkout_jwt + nested mAuth", "ucp-binding",
     [("nested", "valid")],
     [("nested", "missing_mauth"), ("nested", "tampered_terms")],
     "xrefs are this row's negative twins: the PAY-042 stripped-mAuth golden "
     "and the terms-tampered-after-signing golden"),
    (19, "C", "bad merchant sig on checkout_jwt", "ucp-binding",
     [("structural", "st.reject_bad_merchant_sig")], [], ""),
    (20, "C", "wrong issuer/root key", "canonical",
     [("structural", "st.reject_wrong_platform_key")],
     [("semantic", "e2e.reject_wrong_root_key")], ""),
    (21, "C", "alg:none on the issuer JWT", "canonical",
     [("structural", "st.reject_alg_none_root")], [], ""),
    (22, "C", "declared/actual alg mismatch", "ucp-binding",
     [("structural", "st.reject_alg_swap")], [], ""),
    (23, "C", "JCS canonicalization mismatch on merchant_authorization",
     "ucp-binding", [("structural", "st.reject_non_jcs_mauth")], [], ""),
    (24, "D", "KB hop signed by the cnf-bound key verifies", "canonical",
     [("structural", "st.checkout_valid_accepts")], [],
     "shares the golden-accept evidence of row 1 (same executable, this row "
     "attributes the cnf-signature property)"),
    (25, "D", "KB hop signed by a key NOT bound in cnf", "canonical",
     [("structural", "st.reject_wrong_agent_key")],
     [("semantic", "e2e.reject_consent_forgery")], ""),
    (26, "D", "alg:none on the KB hop", "canonical",
     [("structural", "st.reject_alg_none_kb")], [], ""),
    (27, "D", "sd_hash != H(presented previous hop)", "canonical",
     [("frozen", "corrupt_sd_hash")], [], ""),
    (28, "D", "wrong KB-hop typ", "reference",
     [("structural", "st.reject_wrong_kb_typ")], [],
     "the kb+sd-jwt typ invariant is the reference convention; asserted "
     "hermetically by our merchant-side verifier"),
    (29, "D", "terminal hop binds a further cnf", "reference",
     [("structural", "st.reject_terminal_cnf")], [], ""),
    (30, "D", "verify without aud+nonce (draft intent: must error)", "reference",
     [("semantic", "e2e.aud_nonce_optional_tripwire")], [],
     "TRIPWIRE: the pinned reference ACCEPTS (expected_aud/nonce are optional "
     "kwargs) — expect pins the observed behavior; a re-pin that errors REDs "
     "this case as the cue to flip the row to its enforcing form. Candidate "
     "upstream question, not yet filed."),
    (31, "D", "closed by a key NOT in the open cnf (consent forgery)",
     "canonical", [("semantic", "e2e.reject_consent_forgery")],
     [("structural", "st.reject_wrong_agent_key")], ""),
    (32, "D", "missing user consent (lone open mandate)", "canonical",
     [("structural", "st.reject_lone_open")],
     [("semantic", "e2e.reject_missing_consent")], ""),
    (33, "E", "wrong aud", "reference",
     [("semantic", "e2e.reject_wrong_aud")], [], ""),
    (34, "E", "wrong/reused nonce (replay)", "reference",
     [("semantic", "e2e.reject_replayed_nonce")], [], ""),
    (35, "E", "iat beyond the 300s clock skew", "reference",
     [("structural", "st.reject_future_iat")], [],
     "skew value is the reference verifier's default; asserted hermetically"),
    (36, "E", "expired exp / future nbf", "canonical",
     [("structural", "st.reject_expired"),
      ("structural", "st.reject_future_nbf")], [], ""),
    (37, "F", "valid 2-hop open→closed ~~ chain (two-way interop)", "reference",
     [("semantic", "e2e.our_mint_reference_interop"), ("frozen", "golden")], [],
     "our issuer → reference verifier AND reference issuer (goldens) → our "
     "verifier"),
    (38, "F", "broken delegation binding (hop not under prev cnf; 2-hop form)",
     "reference", [("structural", "st.reject_wrong_agent_key")], [],
     "in the 2-hop testbed this is the same wire as row 25; row kept so a "
     "future >2-hop archive extends it without renumbering"),
    (39, "F", "issuer_jwt_hash binding mode (valid + mismatch)", "reference",
     [("semantic", "e2e.issuer_jwt_hash_binding_mode"),
      ("frozen", "corrupt_sd_hash")], [], ""),
    (40, "F", "reveal beyond the signed disclosure set", "canonical",
     [("frozen", "orphan_disclosure"),
      ("semantic", "e2e.reference_rejects_orphan")], [],
     "cross-oracle: our frozen layer and the reference must BOTH reject"),
    (41, "F", "attached plain KB-JWT presentation form", "reference",
     [("structural", "st.reject_attached_kb")], [], ""),
    (42, "G", "closed within open constraints", "reference",
     [("semantic", "e2e.amount_range_within")], [], ""),
    (43, "G", "closed violates a constraint (per type)", "reference",
     [("semantic", "e2e.reject_constraint_violation"),
      ("semantic", "e2e.reject_amount_range_violation"),
      ("semantic", "e2e.reject_disallowed_payee")], [], ""),
    (44, "G", "unknown constraint MUST fail", "reference",
     [("semantic", "e2e.reject_unknown_constraint")], [], ""),
    (45, "G", "open PaymentReference names a different checkout", "reference",
     [("semantic", "e2e.reject_open_reference_mismatch")], [], ""),
    (46, "H", "payment mandate binds a different checkout than processed",
     "ucp-binding",
     [("structural", "st.reject_payment_wrong_checkout"),
      ("semantic", "e2e.reject_unconfirmed_closed_binding")], [],
     "the semantic leg is the AP2#328/PR#330 register case (acknowledged while "
     "the pinned reference skips the default check)"),
    (47, "H", "receipt reference != hash of the leaf closed mandate", "canonical",
     [("structural", "st.receipt_reference_binds"),
      ("structural", "st.reject_receipt_reference_mismatch")], [],
     "CAVEAT: the reference SDK documents `reference` as 'the hash of the "
     "closed mandate' but leaves the value caller-supplied and never derives "
     "it; the sd_hash derivation is OUR convention (cross-anchored to the "
     "reference's sd_hash math), not a reference-pinned byte contract"),
    (48, "H", "human-present happy path E2E", "reference",
     [("semantic", "e2e.checkout_happy_path")], [], ""),
    (49, "H", "human-not-present happy path E2E", "reference",
     [("semantic", "e2e.payment_happy_path")], [], ""),
]


def oracle_of(row):
    """`independent` if any binding runs on our own frozen-standard code."""
    _, _, _, _, bindings, _, _ = row
    return ("independent"
            if any(t in ("frozen", "nested", "structural") for t, _ in bindings)
            else "reference")


def summarize():
    by_layer, by_oracle = {}, {}
    for row in ROWS:
        by_layer[row[3]] = by_layer.get(row[3], 0) + 1
        o = oracle_of(row)
        by_oracle[o] = by_oracle.get(o, 0) + 1
    return {"rows": len(ROWS), "by_layer": by_layer, "by_oracle": by_oracle}


if __name__ == "__main__":
    import json
    print(json.dumps(summarize(), indent=2))
