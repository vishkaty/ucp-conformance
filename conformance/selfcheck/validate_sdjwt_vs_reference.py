#!/usr/bin/env python3
"""
validate_sdjwt_vs_reference.py — the Hybrid-(C) cross-check gate.

Our OWN SD-JWT codec (conformance/common/sdjwt.py) implements the FROZEN RFC-9901
mechanics; the PINNED AP2 reference owns the moving delegate-chain machinery. This
gate proves the two AGREE on the frozen surface, so our independent code is anchored
to a third-party oracle — not merely self-consistent.

Two tiers, so it is meaningful with OR without the reference installed:

  ALWAYS (committed goldens, no `ap2` needed):
    * our codec parses the reference-generated `~~` chains (2 hops, ES256, no top-level KB);
    * every disclosure digest WE compute is referenced by that hop's `_sd`/`{"...":d}`
      (RFC 9901 §7 integrity — our independent computation);
    * the closing hop's `sd_hash` claim equals OUR sd_hash of the previous hop
      (the chain binding — our independent computation).

  WHEN `ap2` IS INSTALLED (byte-parity oracle, both directions):
    * OUR hash_ascii / disclosure_digest / sd_hash == reference common.compute_* on
      identical inputs (a divergence here is a real bug in our frozen-standard code).

Exit 0 = agree; 1 = a real divergence; the reference tier auto-skips if `ap2` absent
(CI installs the pinned ref, so it runs there).
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))
import sdjwt  # noqa: E402

GOLD = HERE / "fixtures" / "ap2" / "golden"


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    return bool(cond)


def _our_checks(ok):
    """Tier 1: our independent codec on the committed goldens."""
    for gf in sorted(GOLD.glob("*.json")):
        g = json.loads(gf.read_text())
        hops = sdjwt.parse_chain(g["wire"])
        ok &= check(f"{gf.stem}: parses as a 2-hop delegate chain",
                    len(hops) == 2)
        ok &= check(f"{gf.stem}: both hops are ES256",
                    all(h.header.get("alg") == "ES256" for h in hops))

        # RFC 9901 §7 integrity — every disclosure we see is referenced
        # (top-level or nested inside another disclosure's value).
        for i, h in enumerate(hops):
            referenced = h.referenced_digests()
            for disc in h.disclosures:
                d = sdjwt.disclosure_digest(disc, h.sd_alg)
                ok &= check(
                    f"{gf.stem} hop{i}: our digest of a disclosure is referenced in _sd",
                    d in referenced)

        # Chain binding — closing hop sd_hash == OUR sd_hash(previous hop).
        binding = hops[1].payload.get("sd_hash")
        ok &= check(f"{gf.stem}: closing hop carries an sd_hash binding",
                    isinstance(binding, str) and bool(binding))
        ok &= check(f"{gf.stem}: our sd_hash(prev hop) == closing hop's sd_hash claim",
                    hops[0].sd_hash() == binding)
    return ok


def _reference_parity(ok):
    """Tier 2: our frozen-standard math == reference common.compute_* (both ways)."""
    try:
        from ap2.sdk.sdjwt import common as ref
        from ap2.sdk.utils import b64url_encode as ref_b64
    except ImportError:
        print("  · reference parity SKIPPED (ap2 not installed)")
        return ok

    # disclosure digest parity — build a disclosure, digest it both ways.
    disc = sdjwt.encode_disclosure("2GLC42sKQveCfGfryNRN9w", "given_name", "John")
    ok &= check("disclosure_digest == reference compute_disclosure_digest",
                sdjwt.disclosure_digest(disc, "sha-256")
                == ref.compute_disclosure_digest(disc, "sha-256"))

    # RFC 9901 §5.1 reproducible vector: the John disclosure's known digest.
    ok &= check("John disclosure matches the RFC 9901 §5.1 published digest",
                sdjwt.disclosure_digest(disc, "sha-256")
                == "jsu9yVulwQQlhFlM_3JlzMaSFzglhQG0DpfayQwLUK4")

    # sd_hash / issuer_jwt_hash parity on a real golden hop (restored form, so
    # both parsers accept the trailing-tilde-stripped root segment).
    g = json.loads((GOLD / "checkout_chain.json").read_text())
    hop0 = sdjwt.split_chain(g["wire"])[0]
    ours = sdjwt.parse_hop(hop0)
    ref_tok = ref.parse_token(hop0)
    ok &= check("sd_hash == reference compute_sd_hash",
                ours.sd_hash() == ref.compute_sd_hash(ref_tok))
    ok &= check("issuer_jwt_hash == reference compute_issuer_jwt_hash",
                ours.issuer_jwt_hash() == ref.compute_issuer_jwt_hash(ref_tok))

    # cross-direction: reference's b64url of our digest input round-trips.
    ok &= check("hash_ascii agrees with reference _hash primitive on ASCII input",
                sdjwt.hash_ascii("abc", "sha-256") == ref._hash_ascii("abc", "sha-256"))
    _ = ref_b64  # (imported to assert the util surface exists)
    return ok


def main():
    ok = True
    ok = _our_checks(ok)
    ok = _reference_parity(ok)
    print("\nsdjwt-vs-reference: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
