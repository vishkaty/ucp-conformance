#!/usr/bin/env python3
"""
provenance.py — the "test basis" for the AP2 mandate testbed, stamped onto every
output so results age into dated historical facts rather than verdicts that a later
draft revision turns into errors.

The AP2 mandate wire (delegate-SD-JWT chain) tracks a MOVING IETF draft, so the
testbed follows the interop discipline used by W3C Web Platform Tests and the QUIC
interop matrix: hard conformance language is reserved for the FROZEN standards
(RFC 9901 SD-JWT, RFC 8785 JCS, RFC 7515 JWS); everything on the moving layer is an
INTEROP OBSERVATION against a pinned reference, never a verdict on anyone's impl.
"""

# The moving normative source for the delegate-chain semantics.
DRAFT = "draft-gco-oauth-delegate-sd-jwt-00"

# The reference implementation we pin as the oracle for the moving layer.
# It is a FIXTURE, not a subject under test — its own behavior is never rendered
# as a defect by this testbed.
REFERENCE = "google-agentic-commerce/AP2"
REFERENCE_SHA = "e1ea56db72a6385bce3e5c1112b3a56ce60acb43"

# The frozen standards our OWN code implements and stakes conformance claims on.
FROZEN_STANDARDS = "RFC 9901 (SD-JWT), RFC 8785 (JCS), RFC 7515 (JWS)"


def basis_banner():
    """A one-block provenance header printed above any testbed output."""
    return (
        "── AP2 mandate testbed — EXPERIMENTAL interop lane "
        "(separate from the locked merchant conformance suite) ──\n"
        f"  frozen layer  : conformance checks vs {FROZEN_STANDARDS} (our own code)\n"
        f"  moving layer  : interop observations vs {REFERENCE} @ {REFERENCE_SHA[:10]}\n"
        f"                  ({DRAFT}) — a divergence is reported, never a verdict\n"
        "  posture       : unofficial; the reference is a fixture, not graded"
    )
