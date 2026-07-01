#!/usr/bin/env python3
"""
area_payment.py — Phase 1 checks for the payment area (spec 2026-01-23).

Covers the TESTABLE, response-observable payment MUSTs against the live Flower
Shop reference server, complementing the core payment paths already in
v2026_01_23 (checkout.complete CHK-004/008, validation.payment_failure VAL-004).

Real shapes probed against http://localhost:8182:
  - Discovery /.well-known/ucp advertises `payment_handlers`: a map of
    provider-group -> list of handler declarations, each with an `id`
    (shop_pay, google_pay, mock_payment_handler observed).
  - A create/complete response carries `ucp.payment_handlers` (empty {} here)
    and empties `payment.instruments`; the raw credential token submitted on
    /complete is NOT echoed back anywhere in the response body.

Checks (each kill-rate self-validated by the engine before it can count):
  PAY-002  every advertised payment handler declaration has an `id`      (MUST)
  PAY-009  the complete response MUST NOT echo the submitted credential   (MUST NOT)

Skipped (with reasons) — see SKIPPED at the bottom.
"""
import sys, pathlib, json  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Check  # noqa: E402
import v2026_01_23 as core  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "selfcheck"))
from verdict_gate import CLEAN, DEVIATION  # noqa: E402

# The credential token core._PAYMENT submits on /complete; the business MUST NOT
# echo it back (unidirectional Platform -> Business credential flow).
_SUBMITTED_TOKEN = core._PAYMENT["payment"]["instruments"][0]["credential"]["token"]

# ---- PAY-002: advertised payment handlers each carry an id ------------------
def chk_handler_ids(r):
    """Discovery `payment_handlers` MUST be a non-empty map of handler groups, and
    every handler declaration in every group MUST include an `id`."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    ph = r.json.get("payment_handlers")
    if not isinstance(ph, dict) or not ph:
        return DEVIATION
    for group in ph.values():
        if not isinstance(group, list) or not group:
            return DEVIATION
        for h in group:
            if not isinstance(h, dict) or not h.get("id"):
                return DEVIATION
    return CLEAN

# ---- PAY-009: credential non-echo (Platform -> Business, unidirectional) -----
def f_complete(base):
    cid = (core._create(base).json or {}).get("id")
    return core._complete(base, cid)

def chk_no_credential_echo(r):
    """A successful complete response MUST NOT contain the raw credential token the
    platform submitted. Predicate deviates if the token appears anywhere in the
    response body (a credential echo), which the `set:` mutation below injects."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    if r.json.get("status") != "completed":
        return DEVIATION
    return DEVIATION if _SUBMITTED_TOKEN in json.dumps(r.json) else CLEAN

# ---- registry ---------------------------------------------------------------
CHECKS = [
    Check("payment.handler_ids_advertised", ["PAY-002"], "MUST",
          core._discovery, chk_handler_ids,
          ["status:500", "drop:payment_handlers", "set:payment_handlers={}",
           "corrupt-json", "empty"]),
    Check("payment.credential_non_echo", ["PAY-009"], "MUST NOT",
          f_complete, chk_no_credential_echo,
          # inject a credential echo to prove the check catches it; plus the
          # generic break mutations. NOTE: the injected value is the exact token
          # the platform submitted, so a leak of it anywhere in the body deviates.
          ["set:leaked_credential=" + json.dumps(_SUBMITTED_TOKEN),
           "status:500", "drop:status", "corrupt-json", "empty"]),
]

# ---- SKIPPED (not clean-pass + kill-safe against this server) ---------------
# PAY-008/PAY-010 (payment_instrument id/handler_id/type; handler_id routing):
#   handler_id routing is exercised by core.checkout.complete sending
#   handler_id=mock_payment_handler, but it is not a distinct response-observable
#   assertion — the complete response empties payment.instruments, so there is no
#   echoed instrument to inspect. Covered implicitly by CHK-004/008.
# PAY-013 (card credential MUST NOT be used for checkout): the mock handler
#   accepts a raw card credential and returns 200, so there is no server oracle.
# PAY-015/016/017 (card/token/binding required fields): request-side schema
#   constraints; the server does not echo the submitted credential/binding
#   (see PAY-009), so they are not response-observable here.
# PAY-019/021/022/027/032/034/035/036 (AP2 merchant_authorization, missing-mandate
#   rejection, JWS header claims): needs-receiver. This mock does not embed
#   ap2.merchant_authorization on the checkout response (ucp.payment_handlers/ap2
#   are empty), and no signed checkout_mandate can be driven clean here, so an AP2
#   completion check cannot be driven clean-pass + kill-safe against this server.
# PAY-001/004/005/006/007/011/014/023/024/025/026/028/030/031/033: manual /
#   spec-authoring / needs-receiver requirements with no runtime wire oracle.
