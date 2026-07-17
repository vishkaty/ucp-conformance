#!/usr/bin/env python3
"""
sdjwt.py — OUR INDEPENDENT SD-JWT / delegate-chain codec for the FROZEN parts of
the AP2 mandate wire (RFC 9901): disclosure encoding + digests, `_sd` referencing,
`sd_hash`/`issuer_jwt_hash`, and the `~`(hop) / `~~`(chain) split.

Hybrid-(C) role: the AP2 delegate-chain SEMANTICS (who signs what, aud/nonce,
constraint evaluation) track a moving IETF draft and are delegated to the pinned
reference SDK. But the RFC-9901 mechanics below are a settled standard, so we
implement them ourselves and cross-check byte-for-byte against the reference
(validate_sdjwt_vs_reference.py). Independent where frozen; reference where in flux.

Correctness pins (verified against the `sd-jwt` reference lib + AP2 common.py):
  * a disclosure is base64url( json.dumps([salt, name, value]) ) with DEFAULT
    separators (", " / ": ") — NOT compact. Digesting a compact re-encoding is the
    classic wrong assumption; we match the on-wire bytes exactly.
  * digest = base64url( H( ASCII(<base64url-disclosure>) ) ), H per `_sd_alg`
    (default sha-256). The hash is over the base64url STRING, not the raw JSON.
  * sd_hash covers  issuer_jwt + "~" + "~".join(disclosures) + "~"  (trailing tilde,
    KB-JWT excluded); issuer_jwt_hash covers issuer_jwt only.
  * hops in a chain are joined by "~~"; disclosures within a hop by single "~".
"""
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from crypto import b64url, b64url_decode  # noqa: E402

_HASH = {"sha-256": hashlib.sha256, "sha-384": hashlib.sha384, "sha-512": hashlib.sha512}


def _hasher(sd_alg):
    if sd_alg is None:
        return hashlib.sha256
    try:
        return _HASH[sd_alg]
    except KeyError:
        raise ValueError(f"unsupported _sd_alg: {sd_alg!r}")


def hash_ascii(value, sd_alg=None):
    """base64url( H(ASCII(value)) ) — the RFC 9901 digest primitive."""
    return b64url(_hasher(sd_alg)(value.encode("ascii")).digest())


def encode_disclosure(salt, name, value):
    """Serialize an object-property disclosure exactly as the wire carries it."""
    return b64url(json.dumps([salt, name, value]).encode("utf-8"))


def encode_array_disclosure(salt, value):
    """Serialize an array-element disclosure ([salt, value])."""
    return b64url(json.dumps([salt, value]).encode("utf-8"))


def disclosure_digest(b64_disclosure, sd_alg=None):
    """Digest of a base64url disclosure string (what goes in `_sd`)."""
    return hash_ascii(b64_disclosure, sd_alg)


def decode_disclosure(b64_disclosure):
    """Return the parsed JSON array of a disclosure."""
    return json.loads(b64url_decode(b64_disclosure))


def _walk_digests(node, found):
    """Collect every `_sd` array entry and `{"...": d}` placeholder digest."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "_sd" and isinstance(v, list):
                found.update(x for x in v if isinstance(x, str))
            elif k == "..." and isinstance(v, str):
                found.add(v)
            else:
                _walk_digests(v, found)
    elif isinstance(node, list):
        for item in node:
            _walk_digests(item, found)


def _restore_segment(segment, index, total):
    """Restore the trailing `~` stripped when hops were joined by `~~`.

    Ported from the reference `mandate._canonical_chain_segment`: joining a
    dSD-JWT chain strips each non-final hop's trailing `~`; on split we add it
    back unless the hop already ends with `~` or ends in a compact KB-JWT.
    """
    if index == total - 1 or segment.endswith("~"):
        return segment
    last = segment.rsplit("~", 1)[-1]
    if len(last.split(".")) == _JWT_PARTS:  # trailing piece is a KB-JWT
        return segment
    return segment + "~"


def split_chain(token):
    """Split an AP2 delegate chain into hops on `~~`, restoring stripped tildes.

    A single (non-chained) SD-JWT has one hop. Returns hop strings each in
    standalone SD-JWT form (so `parse_hop` accepts them).
    """
    segs = token.split("~~")
    n = len(segs)
    return [_restore_segment(s, i, n) for i, s in enumerate(segs)]


_JWT_PARTS = 3


def decode_segment(segment, what="segment"):
    try:
        decoded = json.loads(b64url_decode(segment))
    except Exception as exc:
        raise ValueError(f"cannot parse JWT {what}: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"JWT {what} must decode to a JSON object")
    return decoded


class Hop:
    """A parsed single SD-JWT hop: issuer JWT + disclosures + optional KB-JWT."""

    __slots__ = ("issuer_jwt", "disclosures", "kb_jwt", "header", "payload")

    def __init__(self, issuer_jwt, disclosures, kb_jwt, header, payload):
        self.issuer_jwt = issuer_jwt
        self.disclosures = disclosures
        self.kb_jwt = kb_jwt
        self.header = header
        self.payload = payload

    @property
    def sd_alg(self):
        alg = self.payload.get("_sd_alg")
        return alg if isinstance(alg, str) else None

    @property
    def sd_jwt(self):
        """The issuer_jwt + disclosures + trailing '~' (the sd_hash pre-image)."""
        if self.disclosures:
            return self.issuer_jwt + "~" + "~".join(self.disclosures) + "~"
        return self.issuer_jwt + "~"

    def sd_hash(self):
        return hash_ascii(self.sd_jwt, self.sd_alg)

    def issuer_jwt_hash(self):
        return hash_ascii(self.issuer_jwt, self.sd_alg)

    def collect_sd_digests(self):
        """Digests referenced by an `_sd` array or `{"...": d}` placeholder in
        the issuer payload only (the top-level references)."""
        found = set()
        _walk_digests(self.payload, found)
        return found

    def referenced_digests(self):
        """The full RFC 9901 referenced-digest closure: top-level references PLUS
        those appearing inside any decoded disclosure's value (nested/recursive
        selective disclosure, §7.1). Every disclosure digest must land in here.
        """
        found = set(self.collect_sd_digests())
        for disc in self.disclosures:
            try:
                arr = decode_disclosure(disc)
            except Exception:
                continue
            # value is the last element ([salt, value] or [salt, name, value]).
            if isinstance(arr, list) and arr:
                _walk_digests(arr[-1], found)
        return found


def parse_hop(hop):
    """Parse a single SD-JWT hop (disclosures separated by single '~')."""
    if hop.startswith("~"):
        raise ValueError("malformed SD-JWT: empty issuer JWT")
    if "~" not in hop:
        raise ValueError("malformed SD-JWT: missing disclosure separator")
    parts = hop.split("~")
    issuer_jwt = parts[0]
    middle = parts[1:-1]
    if any(not seg for seg in middle):
        raise ValueError("malformed SD-JWT: empty disclosure segment")
    if hop.endswith("~"):
        disclosures, kb_jwt = middle, None
    else:
        kb_jwt = parts[-1]
        if len(kb_jwt.split(".")) != _JWT_PARTS:
            raise ValueError("malformed KB-JWT: expected header.payload.signature")
        disclosures = middle
    jwt_parts = issuer_jwt.split(".")
    if len(jwt_parts) != _JWT_PARTS:
        raise ValueError("malformed SD-JWT: issuer JWT must be header.payload.signature")
    header = decode_segment(jwt_parts[0], "header")
    payload = decode_segment(jwt_parts[1], "payload")
    return Hop(issuer_jwt, disclosures, kb_jwt, header, payload)


def parse_chain(token):
    """Parse every hop of a `~~`-joined delegate chain."""
    return [parse_hop(h) for h in split_chain(token)]
