#!/usr/bin/env python3
"""
frozen.py — an INDEPENDENT verifier for the frozen (RFC 9901) properties of an AP2
mandate delegate chain, plus mutators that break each property. Uses only our own
codec (conformance/common/sdjwt.py) — no reference SDK — so these E2E cases run
everywhere, including when the moving reference is unavailable.

`frozen_verify` checks the three properties a conformant wire MUST satisfy at the
frozen layer:
  1. STRUCTURE  — the `~~` chain and every hop parse as well-formed SD-JWTs.
  2. INTEGRITY  — every disclosure's digest is referenced by an `_sd`/placeholder
                  (RFC 9901 §7.1), including nested/recursive references.
  3. BINDING    — each KB hop's `sd_hash` equals our sd_hash of the previous hop.

The mutators produce REJECT fixtures from a valid golden. Each mutation targets one
property, so a golden that verifies OK while every mutant is REJECTed is kill-safe.
"""
import base64
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "common"))
import sdjwt  # noqa: E402


def frozen_verify(wire):
    """Return (ok: bool, reason: str). ok=True only if structure+integrity+binding hold."""
    try:
        hops = sdjwt.parse_chain(wire)
    except ValueError as exc:
        return False, f"structure: {exc}"
    if not hops:
        return False, "structure: empty chain"

    for i, h in enumerate(hops):
        referenced = h.referenced_digests()
        for disc in h.disclosures:
            if sdjwt.disclosure_digest(disc, h.sd_alg) not in referenced:
                return False, f"integrity: hop{i} disclosure not referenced in _sd"

    for i in range(1, len(hops)):
        claim = hops[i].payload.get("sd_hash")
        if claim is None:
            # a hop may bind via issuer_jwt_hash instead; accept that alternative.
            claim = hops[i].payload.get("issuer_jwt_hash")
            if claim is None:
                return False, f"binding: hop{i} carries neither sd_hash nor issuer_jwt_hash"
            if claim != hops[i - 1].issuer_jwt_hash():
                return False, f"binding: hop{i} issuer_jwt_hash != H(prev issuer jwt)"
            continue
        if claim != hops[i - 1].sd_hash():
            return False, f"binding: hop{i} sd_hash != our sd_hash(prev hop)"

    return True, "ok"


# ── Mutators: each returns a new wire that breaks exactly one frozen property ──

def _flip_char(s, i):
    c = "B" if s[i] != "B" else "C"
    return s[:i] + c + s[i + 1:]


def mut_tamper_disclosure(wire):
    """Flip a byte inside the first hop's first disclosure -> digest no longer matches."""
    hops = wire.split("~~")
    segs = hops[0].split("~")
    # find first non-empty disclosure segment after the issuer jwt
    for j in range(1, len(segs)):
        if segs[j]:
            segs[j] = _flip_char(segs[j], len(segs[j]) // 2)
            break
    hops[0] = "~".join(segs)
    return "~~".join(hops)


def mut_orphan_disclosure(wire):
    """Append a well-formed disclosure whose digest is referenced nowhere."""
    hops = wire.split("~~")
    orphan = sdjwt.encode_disclosure("orphansaltorphansalt00", "extra", "value")
    segs = hops[0].split("~")
    # insert before any trailing empty segment so the hop stays parseable
    if segs[-1] == "":
        segs.insert(-1, orphan)
    else:
        segs.append(orphan)
    hops[0] = "~".join(segs)
    return "~~".join(hops)


def mut_corrupt_sd_hash(wire):
    """Rewrite the closing hop's issuer payload sd_hash to a wrong value."""
    hops = wire.split("~~")
    last = sdjwt.split_chain(wire)[-1]  # restored form for clean parse
    issuer_jwt = last.split("~")[0]
    h, p, s = issuer_jwt.split(".")
    payload = json.loads(_b64d(p))
    if "sd_hash" in payload:
        payload["sd_hash"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    elif "issuer_jwt_hash" in payload:
        payload["issuer_jwt_hash"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    new_issuer = h + "." + _b64e(json.dumps(payload).encode()) + "." + s
    # rebuild the last raw hop, preserving its original (possibly stripped) tail
    raw_last = hops[-1]
    hops[-1] = new_issuer + raw_last[len(raw_last.split("~")[0]):]
    return "~~".join(hops)


def mut_break_chain_sep(wire):
    """Collapse the `~~` hop separator to a single `~` -> malformed chain."""
    return wire.replace("~~", "~", 1)


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


# name -> mutator, for the frozen-layer REJECT cases. Every mutant MUST make
# frozen_verify return ok=False (the reason string is diagnostic only). Truncation
# to a lone open mandate is intentionally NOT here: it is well-formed at the frozen
# layer and its rejection ("missing the agent's closing consent hop") is semantic.
FROZEN_MUTANTS = {
    "tampered_disclosure": mut_tamper_disclosure,
    "orphan_disclosure": mut_orphan_disclosure,
    "corrupt_sd_hash": mut_corrupt_sd_hash,
    "broken_chain_separator": mut_break_chain_sep,
}
