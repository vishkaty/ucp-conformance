#!/usr/bin/env python3
"""
merchant_checks_04_08_payment.py — 2026-04-08-scoped behavioral PAYMENT checks.

These register ids mean something DIFFERENT in the 2026-01-11/01-23 registers (the
04-08 registers renumbered the PAY family — e.g. PAY-003@01-23 is the platform
spec+schema declaration but PAY-003@04-08 is the checkout-response handler echo;
PAY-018@01-23 is binding identity but PAY-018@04-08 is continue_url), so every check
is version-locked (versions=) and this file is named *_04_08 so coverage/matrix.py
attributes its ids to 2026-04-08 only.

Reference target: the controlled fixture in 04-08 mode (validate_merchant_checks
--golden controlled), which declares the dev.spck.tokenpay handler in its profile,
echoes the resolved declaration in every checkout response's ucp.payment_handlers,
and escalates the seeded 3DS token (escalate_token) into a requires_escalation
response carrying continue_url.

Config (under config.payment):
  handler_key         — the reverse-domain registry key the merchant declares
  handler_id          — the id of that handler's declaration
  escalation_payment  — a complete-request body whose credential triggers a
                        3DS/SCA soft-decline (status requires_escalation)

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr and flow helpers from there).
"""
import re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                              # noqa: E402
from merchant_checks import (MCheck, _hdr, profile_resp, create_resp,   # noqa: E402
                             _create_for_complete)

V0408 = ("2026-04-08",)

# ucp.json: the payment_handlers registry is "keyed by reverse-domain name"
# (propertyNames -> shopping/types/reverse_domain_name.json, pinned pattern below)
_RDN = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$")

def _pcfg(ctx):
    return ctx.config.get("payment") or {}

def _registry_ok(ph):
    """The shared PAY-001/PAY-002/PAY-003 registry shape: a keyed object whose keys
    are reverse-domain names and whose values are non-empty arrays of handler
    declarations each carrying an id. An EMPTY registry object is conformant (the
    schema requires the key, not entries), so {} stays CLEAN — the reference fixture
    declares a real handler, which is what the entry-level defect injections bite on."""
    if not isinstance(ph, dict):
        return False
    for k, group in ph.items():
        if not isinstance(k, str) or not _RDN.match(k):
            return False
        if not isinstance(group, list) or not group:
            return False
        for h in group:
            if not isinstance(h, dict) or not isinstance(h.get("id"), str) \
               or not h["id"]:
                return False
    return True

# ---- PAY-001/PAY-002: the profile advertises the payment_handlers registry -----
def p_profile_handlers(r, ctx):
    """PAY-001@04-08: the business profile carries the payment_handlers registry
    keyed by reverse-domain name; PAY-002@04-08: every declaration includes an id."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    if "payment_handlers" not in r.json:
        return DEVIATION
    return CLEAN if _registry_ok(r.json["payment_handlers"]) else DEVIATION

# ---- PAY-003: checkout responses echo handlers in ucp.payment_handlers ---------
def p_response_handlers(r, ctx):
    """PAY-003@04-08: a checkout response's ucp envelope carries the (required)
    payment_handlers registry; entries conform to the response_schema basics (id)."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    ucp = r.json.get("ucp")
    if not isinstance(ucp, dict) or "payment_handlers" not in ucp:
        return DEVIATION
    return CLEAN if _registry_ok(ucp["payment_handlers"]) else DEVIATION

# ---- PAY-018: requires_escalation responses carry continue_url -----------------
def f_escalation(ctx):
    """Create a completable session, then complete with the merchant's seeded
    3DS/SCA soft-decline credential (config: payment.escalation_payment)."""
    cid = (_create_for_complete(ctx).json or {}).get("id")
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete", "POST",
                 _pcfg(ctx).get("escalation_payment"), _hdr())

def p_escalation_continue_url(r, ctx):
    """PAY-018@04-08: the config-promised soft-decline yields HTTP 200 with
    status=requires_escalation (checkout.json status enum; overview.md Scenario B
    shows 200 OK) AND a continue_url ('MUST be provided when status is
    requires_escalation'). The URL must be absolute — the platform MUST open it in
    a WebView/Window (overview.md L1409), which a relative reference cannot satisfy."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    if r.json.get("status") != "requires_escalation":
        return DEVIATION
    cu = r.json.get("continue_url")
    return CLEAN if isinstance(cu, str) and "://" in cu else DEVIATION

CHECKS_04_08_PAYMENT = [
    MCheck("payment.profile_handlers_registry", ["PAY-001", "PAY-002"], "MUST",
           profile_resp, p_profile_handlers,
           ["drop:payment_handlers",                       # required key gone
            "set:payment_handlers=[]",                     # array, not keyed object
            # non-reverse-domain key
            "set:payment_handlers={\"TokenPay\":[{\"id\":\"h1\",\"version\":\"2026-04-08\"}]}",
            # declaration missing id (PAY-002)
            "set:payment_handlers={\"dev.spck.tokenpay\":[{\"version\":\"2026-04-08\"}]}",
            # empty declaration group
            "set:payment_handlers={\"dev.spck.tokenpay\":[]}",
            "corrupt-json", "empty"],
           transport="rest", versions=V0408),
    MCheck("payment.response_handlers_echo", ["PAY-003"], "MUST",
           create_resp, p_response_handlers,
           ["status:500",
            "drop:ucp.payment_handlers",                   # required key gone
            "set:ucp.payment_handlers=[]",                 # array, not keyed object
            # response entry missing id (response_schema inherits base's id)
            "set:ucp.payment_handlers={\"dev.spck.tokenpay\":[{\"version\":\"2026-04-08\"}]}",
            "drop:ucp", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           transport="rest", versions=V0408),
    MCheck("payment.escalation_continue_url", ["PAY-018"], "MUST",
           f_escalation, p_escalation_continue_url,
           ["status:500", "status:402",                    # escalation is NOT a decline
            "drop:continue_url",                           # the PAY-018 defect itself
            "set:continue_url=\"\"",
            "set:continue_url=\"/3ds/relative\"",          # not openable in a WebView
            "set:status=\"completed\"",                    # config promised escalation
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("payment.escalation_payment",), transport="rest",
           versions=V0408),
]
