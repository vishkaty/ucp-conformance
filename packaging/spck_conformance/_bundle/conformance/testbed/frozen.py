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


_KNOWN_SD_ALGS = {"sha-256", "sha-384", "sha-512"}


def _nested_sd_alg(node, top=True):
    """True if an `_sd_alg` member appears anywhere BELOW the top level
    (RFC 9901 §4.1.1: _sd_alg MUST appear only once, at the top level)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "_sd_alg" and not top:
                return True
            if _nested_sd_alg(v, top=False):
                return True
    elif isinstance(node, list):
        return any(_nested_sd_alg(v, top=False) for v in node)
    return False


def _malformed_placeholder(node):
    """True if any array-element placeholder `{"...": v}` carries a non-string v."""
    if isinstance(node, dict):
        if "..." in node and not isinstance(node["..."], str):
            return True
        return any(_malformed_placeholder(v) for v in node.values())
    if isinstance(node, list):
        return any(_malformed_placeholder(v) for v in node)
    return False


def frozen_verify(wire):
    """Return (ok: bool, reason: str). ok=True only if structure+integrity+binding hold."""
    try:
        hops = sdjwt.parse_chain(wire)
    except ValueError as exc:
        return False, f"structure: {exc}"
    if not hops:
        return False, "structure: empty chain"

    for i, h in enumerate(hops):
        # _sd_alg discipline (RFC 9901 §4.1.1): unknown values reject; absent
        # defaults to sha-256; a nested occurrence rejects.
        alg = h.payload.get("_sd_alg")
        if alg is not None and alg not in _KNOWN_SD_ALGS:
            return False, f"integrity: hop{i} unknown _sd_alg {alg!r}"
        if _nested_sd_alg({k: v for k, v in h.payload.items() if k != "_sd_alg"}, top=False):
            return False, f"integrity: hop{i} carries a nested _sd_alg (top-level only)"
        if _malformed_placeholder(h.payload):
            return False, f"integrity: hop{i} malformed array placeholder"

        referenced = h.referenced_digests()
        seen = set()
        for disc in h.disclosures:
            d = sdjwt.disclosure_digest(disc, h.sd_alg)
            if d not in referenced:
                return False, f"integrity: hop{i} disclosure not referenced in _sd"
            if d in seen:
                # RFC 9901 §7.1.4.4: the same digest MUST NOT be referenced/used twice.
                return False, f"integrity: hop{i} duplicate disclosure digest"
            seen.add(d)

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


def _rewrite_hop0_payload(wire, edit):
    """Re-encode hop0's issuer-JWT payload after `edit(payload_dict)` mutates it.
    The stale signature is irrelevant at the frozen layer (no hop-sig checks)."""
    hops = wire.split("~~")
    segs = hops[0].split("~")
    h, p, s = segs[0].split(".")
    payload = json.loads(_b64d(p))
    edit(payload)
    segs[0] = h + "." + _b64e(json.dumps(payload).encode()) + "." + s
    hops[0] = "~".join(segs)
    return "~~".join(hops)


def mut_unknown_sd_alg(wire):
    """_sd_alg set to an unregistered/insecure value -> reject (RFC 9901 §4.1.1)."""
    return _rewrite_hop0_payload(wire, lambda p: p.__setitem__("_sd_alg", "md5"))


def mut_nested_sd_alg(wire):
    """An _sd_alg smuggled below the top level -> reject (top-level only)."""
    def edit(p):
        dp = p.get("delegate_payload")
        if isinstance(dp, list) and dp and isinstance(dp[0], dict):
            dp[0]["_sd_alg"] = "sha-256"
        else:
            p["extra"] = {"_sd_alg": "sha-256"}
    return _rewrite_hop0_payload(wire, edit)


def mut_malformed_placeholder(wire):
    """An array placeholder {"...": v} with a non-string digest -> reject."""
    def edit(p):
        dp = p.get("delegate_payload")
        if isinstance(dp, list) and dp and isinstance(dp[0], dict) and "..." in dp[0]:
            dp[0]["..."] = 12345
        else:
            p["delegate_payload"] = [{"...": 12345}]
    return _rewrite_hop0_payload(wire, edit)


def mut_duplicate_disclosure(wire):
    """The same disclosure presented twice -> its digest is used twice -> reject
    (RFC 9901 §7.1.4.4)."""
    hops = wire.split("~~")
    segs = hops[0].split("~")
    for j in range(1, len(segs)):
        if segs[j]:
            segs.insert(j, segs[j])
            break
    hops[0] = "~".join(segs)
    return "~~".join(hops)


# name -> mutator, for the frozen-layer REJECT cases. Every mutant MUST make
# frozen_verify return ok=False (the reason string is diagnostic only). Truncation
# to a lone open mandate is intentionally NOT here: it is well-formed at the frozen
# layer and its rejection ("missing the agent's closing consent hop") is semantic.
FROZEN_MUTANTS = {
    "tampered_disclosure": mut_tamper_disclosure,
    "orphan_disclosure": mut_orphan_disclosure,
    "corrupt_sd_hash": mut_corrupt_sd_hash,
    "broken_chain_separator": mut_break_chain_sep,
    "unknown_sd_alg": mut_unknown_sd_alg,
    "nested_sd_alg": mut_nested_sd_alg,
    "malformed_placeholder": mut_malformed_placeholder,
    "duplicate_disclosure": mut_duplicate_disclosure,
}
