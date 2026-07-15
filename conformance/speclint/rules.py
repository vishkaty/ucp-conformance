#!/usr/bin/env python3
"""
rules.py — speclint rules as data.

A rule binds a predicate class to the concrete spec artifacts it reads, the two
"sides" it puts in tension, its materiality (why an implementer is misled), and a
ledger reference so a human disposition exists before anything is filed upstream.
speclint runs read-only against the SHA-pinned vendored spec (conformance/.vendor);
findings are candidates for the manual five-gate filing protocol, never auto-filed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpecLintRule:
    id: str
    version: str                     # vendored spec version the rule reads
    predicate_class: str             # which predicate in predicates.py
    side_a: str                      # human label for the first artifact/claim
    side_b: str                      # human label for the second
    materiality: str                 # how a real implementer is misled
    disposition: str                 # "candidate" | "advisory" | "drift-guard"
    ledger_ref: str = ""             # AMBIGUITIES.md / ops ledger id, once triaged
    inputs: tuple = field(default_factory=tuple)  # vendored-relative file paths


TRANSPORT_PARITY = SpecLintRule(
    id="SPL-PARITY-IDEM",
    version="2026-04-08",
    predicate_class="transport_header_parity",
    side_a="REST OpenAPI required header parameters "
           "(source/services/shopping/rest.openapi.json)",
    side_b="MCP OpenRPC required meta fields "
           "(source/services/shopping/mcp.openrpc.json)",
    materiality="A client generated from one transport's contract emits requests "
                "the other transport's server rejects: MCP marks Idempotency-Key "
                "optional on create/update while REST requires it (and MCP itself "
                "requires it on complete/cancel), so the transports disagree on the "
                "retry-safety guarantee of create/update.",
    disposition="candidate",
    inputs=("source/services/shopping/rest.openapi.json",
            "source/services/shopping/mcp.openrpc.json"),
)

RULES = (TRANSPORT_PARITY,)
