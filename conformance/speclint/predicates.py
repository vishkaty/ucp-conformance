#!/usr/bin/env python3
"""
predicates.py — speclint decision functions.

Each predicate takes already-parsed spec structures and returns a deterministic,
sorted list of Findings. Predicates never read files or the network themselves;
that separation keeps them pure and trivially testable against synthetic inputs
(see fixtures/class_negatives/, which prove a predicate does NOT fire on a
consistent-but-different pair — i.e. it has no constant-FIRE bug).
"""
from __future__ import annotations

from dataclasses import dataclass

# Headers that BOTH transports model, so a per-operation required/optional
# disagreement is a genuine cross-transport contradiction — not an artifact of
# one transport lacking the concept. REST header name -> MCP `meta` field name.
COMPARABLE_HEADERS = {
    "Idempotency-Key": "idempotency-key",
    "UCP-Agent": "ucp-agent",
}

# Headers deliberately EXCLUDED from parity (legitimately transport-specific):
#   Request-Id                       REST tracing baseline; JSON-RPC carries `id`.
#   Content-Type / Accept*           HTTP content negotiation; no MCP analogue.
#   Authorization / X-API-Key /
#   Signature / Signature-Input /
#   Content-Digest                   auth & signing layer, modelled outside `meta`.
# Comparing these would fire on expected baseline differences, so they are never
# in COMPARABLE_HEADERS.


@dataclass(frozen=True, order=True)
class ParityFinding:
    """One operation where a comparable header's required-ness differs by transport."""
    operation: str
    header: str
    required_in: str   # "rest" or "mcp" — the transport that requires it
    optional_in: str   # the transport that does not


def transport_header_parity(rest_required: dict,
                            mcp_required: dict,
                            comparable: dict = COMPARABLE_HEADERS) -> list:
    """Findings where a comparable header is required in one transport, not the other.

    Only operations present in BOTH transports (matched by identical name, as the
    UCP shopping service uses the same operation names across REST and MCP) are
    compared. Returns a deterministically sorted list.
    """
    findings = []
    shared_ops = set(rest_required) & set(mcp_required)
    for op in shared_ops:
        for header, meta_field in comparable.items():
            in_rest = header in rest_required[op]
            in_mcp = meta_field in mcp_required[op]
            if in_rest != in_mcp:
                findings.append(ParityFinding(
                    operation=op,
                    header=header,
                    required_in="rest" if in_rest else "mcp",
                    optional_in="mcp" if in_rest else "rest",
                ))
    return sorted(findings)
