#!/usr/bin/env python3
"""
merchant_checks_04_08_ap2.py — 2026-04-08 AP2 mandate ENFORCE-side behavioral
checks (ap2-mandates.md "Business Verification").

Capability-gated on dev.ucp.shopping.ap2_mandate: these run only against a
merchant that ADVERTISES the AP2 extension — on such a merchant the session is
Security Locked and a completion without a valid ap2.checkout_mandate MUST NOT
complete. Merchants without the capability skip honestly (not-applicable).

Reference target: the controlled fixture in --ap2 mode; kill-proof = the dedicated
selfcheck/validate_ap2_enforce.py gate (clean-pass + kill_safe on the enforcing
golden, DEVIATION on the --ap2-no-enforce mutant), the ORD-012 pattern. The
deeper mandate-crypto surface (nested binding, chain integrity) is covered by the
fixture checks in area_04_08_ap2.py + the ap2-e2e gate.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                       # noqa: E402
from merchant_checks import MCheck, _hdr, _create_payload        # noqa: E402

V0408 = ("2026-04-08",)

# The spec's error-code registry (ap2_mandate.json $defs/error_code). An
# unverifiable mandate may surface as mandate_invalid_signature, or as
# agent_missing_key when the business fails at key resolution first — both are
# spec-registered rejections of an unverifiable presentation.
_INVALID_CODES = {"mandate_invalid_signature", "agent_missing_key"}


def _has_value(node, wanted):
    """True if any value in the JSON tree equals one of `wanted` (envelope-agnostic:
    the spec pins the error CODES, not where in the error body they appear)."""
    if isinstance(node, str):
        return node in wanted
    if isinstance(node, dict):
        return any(_has_value(v, wanted) for v in node.values())
    if isinstance(node, list):
        return any(_has_value(v, wanted) for v in node)
    return False


def _create_then_complete(ctx, complete_body):
    """Create a checkout, then attempt completion with `complete_body`; the
    response under test is the completion response."""
    c = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
              _create_payload(ctx), _hdr())
    cid = (c.json or {}).get("id")
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete",
                 "POST", complete_body, _hdr())


def f_complete_no_mandate(ctx):
    """PAY-035/044/045/047: complete WITHOUT ap2.checkout_mandate."""
    return _create_then_complete(ctx, {})


def f_complete_invalid_mandate(ctx):
    """PAY-038: complete with a syntactically broken, unverifiable mandate."""
    return _create_then_complete(ctx, {"ap2": {"checkout_mandate": "not.a.chain~"}})


def p_mandate_required(resp):
    """The completion MUST be rejected (4xx) with the mandate_required code —
    an accepted completion here is the PAY-035 defect itself."""
    if not (400 <= resp.status < 500):
        return DEVIATION
    return CLEAN if _has_value(resp.json, {"mandate_required"}) else DEVIATION


def p_invalid_mandate_rejected(resp):
    """An unverifiable mandate MUST produce an error (PAY-038), with a
    spec-registered rejection code."""
    if not (400 <= resp.status < 500):
        return DEVIATION
    return CLEAN if _has_value(resp.json, _INVALID_CODES) else DEVIATION


CHECKS_04_08_AP2 = [
    MCheck("payment.ap2_complete_requires_mandate",
           ["PAY-035", "PAY-044", "PAY-045", "PAY-047"], "MUST",
           f_complete_no_mandate, p_mandate_required,
           ["status:200",                       # the merchant that completes anyway
            "drop:code",                        # rejection without the required code
            'set:code="checkout_incomplete"',   # wrong code
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.ap2_mandate", needs=("product",),
           transport="rest", versions=V0408),
    MCheck("payment.ap2_invalid_mandate_rejected", ["PAY-038"], "MUST",
           f_complete_invalid_mandate, p_invalid_mandate_rejected,
           ["status:200",
            "drop:code",
            'set:code="checkout_incomplete"',
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.ap2_mandate", needs=("product",),
           transport="rest", versions=V0408),
]
