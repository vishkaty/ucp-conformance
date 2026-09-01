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

TRANSPORT_PARITY_0825 = SpecLintRule(
    id="SPL-PARITY-IDEM",
    version="2026-08-25",
    predicate_class="transport_header_parity",
    side_a="REST OpenAPI required header parameters "
           "(source/services/shopping/rest.openapi.json)",
    side_b="MCP OpenRPC required meta fields "
           "(source/services/shopping/mcp.openrpc.json)",
    materiality="Re-verification of SPL-PARITY-IDEM at the v2026-08-25 release pin "
                "(PLAN-0825 A.3 / GAP-LEDGER-0825 G11): the finding PERSISTS, "
                "unchanged in shape. REST still requires Idempotency-Key on "
                "create_cart/create_checkout/update_cart/update_checkout; MCP's "
                "base `meta` schema (components.schemas.meta.required) requires "
                "only `ucp-agent`, and only complete_checkout/cancel_checkout gained "
                "a per-method `allOf` branch adding `idempotency-key` — create/update "
                "did not. A client generated from one transport's contract still "
                "emits requests the other transport's server rejects, on the same "
                "four operations as at 2026-04-08.",
    disposition="candidate",
    inputs=("source/services/shopping/rest.openapi.json",
            "source/services/shopping/mcp.openrpc.json"),
)

SIGNATURE_EXAMPLE_COVERAGE_0825 = SpecLintRule(
    id="SPL-SIGEX-COVERAGE",
    version="2026-08-25",
    predicate_class="signature_example_coverage",
    side_a="Headers a shipped signed example actually carries "
           "(docs/specification/**/*.md fenced http blocks)",
    side_b="Components that same example's own Signature-Input covers",
    materiality="The identity-resolution gate (overview/index.md, Enforce "
                "covered-component requirements) names a CLOSED set of request "
                "headers a signature MUST cover when present, and requires a "
                "verifier to SKIP a signature whose covered set omits any of them, "
                "since an uncovered header is treated as unsigned. Two shipped "
                "examples carry UCP-Agent while their own Signature-Input omits "
                "ucp-agent, so the spec models a request that a conformant verifier "
                "is required to skip, in the one place an implementer copies from. "
                "Both sides live in the SAME fenced block, so neither can be argued "
                "away as a reading of prose. Filed upstream 2026-09-01 for "
                "shopping/checkout/rest.md; shopping/order/index.md is already "
                "addressed by our open ucp#659.",
    disposition="candidate",
    inputs=("docs/specification/shopping/checkout/rest.md",
            "docs/specification/shopping/order/index.md",
            "docs/specification/overview/index.md"),
)

RULES = (TRANSPORT_PARITY, TRANSPORT_PARITY_0825, SIGNATURE_EXAMPLE_COVERAGE_0825)
