#!/usr/bin/env python3
"""
merchant_checks_04_08_receiver.py — 2026-04-08-scoped behavioral checks for the
RECEIVER tier: continue_url/escalation, cart-to-checkout conversion, eligibility
verification, idempotency, and the algorithm_unsupported signature error code.

Every id here is version-locked to 2026-04-08 (versions=("2026-04-08",)) and this
file is named *_04_08* so coverage/matrix.py attributes its ids to 2026-04-08 only
— the 01-11/01-23 registers reuse CHK/CART/SAE/SIG numbers for OTHER requirements.
Register rows: conformance/requirements/2026-04-08/. Reference target: the
controlled fixture (validate_merchant_checks --golden controlled), whose config
gates each scenario (payment.escalation_payment, eligibility.*, cart.*, etc.).
Verbatim MUSTs re-read in conformance/.vendor/ucp/docs/specification/{checkout,
cart,discount,signatures}.md and .../source/schemas/shopping/checkout.json.

NOTE: imported lazily by merchant_checks.all_checks(); pulls MCheck/_hdr from there.
"""
import sys, uuid, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                    # noqa: E402
from merchant_checks import MCheck, _hdr                      # noqa: E402

V0408 = ("2026-04-08",)

# ---- request helpers ---------------------------------------------------------
def _pcfg(ctx):  return ctx.config.get("payment") or {}
def _ecfg(ctx):  return ctx.config.get("eligibility") or {}

def _create(ctx, body):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", body, _hdr())

def _complete(ctx, cid, body):
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete",
                 "POST", body, _hdr())

def _line(pid, qty=1):
    return {"line_items": [{"item": {"id": pid}, "quantity": qty}]}

def _success_pay():
    return {"payment": {"instruments": [{"id": "instr_ok", "type": "card",
        "credential": {"type": "token", "token": "success_token"}}]}}

def _messages(r):
    return (r.json or {}).get("messages") or [] if isinstance(r.json, dict) else []

def _applied(r):
    d = (r.json or {}).get("discounts") if isinstance(r.json, dict) else None
    return d.get("applied") if isinstance(d, dict) else None

# ======== ESCALATION / continue_url (checkout.md "Continue URL") ==============
def f_escalate(ctx):
    """Complete with the seeded 3DS soft-decline credential -> the session goes to
    status=requires_escalation (config: payment.escalation_payment)."""
    cid = (_create(ctx, _line(ctx.product_id)).json or {}).get("id")
    return _complete(ctx, cid, _pcfg(ctx).get("escalation_payment"))

def p_escalation_continue_url(r, ctx):
    """CHK-001/CHK-014/CHK-043: a requires_escalation response MUST carry
    continue_url ('Businesses MUST provide continue_url when returning status =
    requires_escalation')."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    if r.json.get("status") != "requires_escalation":
        return DEVIATION
    cu = r.json.get("continue_url")
    return CLEAN if isinstance(cu, str) and cu else DEVIATION

def p_escalation_https(r, ctx):
    """CHK-004: 'The continue_url MUST be an absolute HTTPS URL.'"""
    if r.status != 200 or (r.json or {}).get("status") != "requires_escalation":
        return DEVIATION
    cu = (r.json or {}).get("continue_url")
    return CLEAN if isinstance(cu, str) and cu.startswith("https://") else DEVIATION

def p_escalation_buyer_message(r, ctx):
    """CHK-015: a requires_escalation response MUST include at least one message
    with severity requires_buyer_input or requires_buyer_review."""
    if r.status != 200 or (r.json or {}).get("status") != "requires_escalation":
        return DEVIATION
    return CLEAN if any(isinstance(m, dict) and m.get("severity") in
                        ("requires_buyer_input", "requires_buyer_review")
                        for m in _messages(r)) else DEVIATION

# ======== ELIGIBILITY (checkout.md / discount.md "Eligibility Claims") =========
def f_elig_recognized(ctx):
    """Create with a RECOGNIZED eligibility claim (config: eligibility.verifiable)."""
    body = _line(ctx.product_id)
    body["context"] = {"eligibility": [_ecfg(ctx).get("verifiable")]}
    return _create(ctx, body)

def p_provisional_discount(r, ctx):
    """DSC-014/DSC-016: a recognized claim that affects pricing MUST surface a
    corresponding PROVISIONAL discount (automatic:true, provisional:true,
    eligibility:<claim>, no code) in discounts.applied."""
    if r.status not in (200, 201):
        return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list):
        return DEVIATION
    claim = _ecfg(ctx).get("verifiable")
    return CLEAN if any(isinstance(a, dict) and a.get("provisional") is True
                        and a.get("eligibility") == claim and "code" not in a
                        for a in ap) else DEVIATION

def f_elig_unrecognized(ctx):
    body = _line(ctx.product_id)
    body["context"] = {"eligibility": [_ecfg(ctx).get("unrecognized")]}
    return _create(ctx, body)

def p_unrecognized_ignored(r, ctx):
    """SAE-011: 'Businesses MUST ignore unrecognized eligibility values without
    error.' The create succeeds (no error envelope, no type:error message about
    the claim) and surfaces the not-accepted claim as a warning, not an error."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    if (r.json.get("ucp") or {}).get("status") == "error":
        return DEVIATION
    msgs = _messages(r)
    if any(m.get("type") == "error" and m.get("code", "").startswith("eligibility")
           for m in msgs):
        return DEVIATION
    return CLEAN if any(m.get("type") == "warning"
                        and m.get("code") == "eligibility_not_accepted"
                        for m in msgs) else DEVIATION

def f_elig_unrecognized_complete(ctx):
    """Create with an unrecognized claim, then complete -> MUST NOT block."""
    cid = (f_elig_unrecognized(ctx).json or {}).get("id")
    return _complete(ctx, cid, _success_pay())

def p_not_blocked(r, ctx):
    """SAE-014: 'Unrecognized or inapplicable eligibility claims MUST NOT block the
    checkout.' Completion proceeds to status=completed."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("status") == "completed" else DEVIATION

def f_elig_unverifiable_complete(ctx):
    """Create with a recognized-but-UNVERIFIABLE claim, then attempt completion."""
    body = _line(ctx.product_id)
    body["context"] = {"eligibility": [_ecfg(ctx).get("unverifiable")]}
    cid = (_create(ctx, body).json or {}).get("id")
    return _complete(ctx, cid, _success_pay())

def p_completion_blocked(r, ctx):
    """SAE-013/SAE-018/CHK-056: 'Businesses MUST NOT complete a transaction with
    unresolved eligibility claims' — an unverifiable accepted claim leaves the
    session NOT completed."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("status") != "completed" else DEVIATION

def p_eligibility_invalid_message(r, ctx):
    """SAE-017/SAE-020/SAE-019: at completion an accepted claim that remains
    unverified MUST yield type:error code eligibility_invalid severity recoverable,
    and the verification failure MUST affect ONLY the messages array (the response
    is still a normal checkout envelope, not ucp.status:error)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    if (r.json.get("ucp") or {}).get("status") == "error":
        return DEVIATION                     # must affect only messages, not envelope
    return CLEAN if any(m.get("type") == "error"
                        and m.get("code") == "eligibility_invalid"
                        and m.get("severity") == "recoverable"
                        for m in _messages(r)) else DEVIATION

def f_elig_verifiable_complete(ctx):
    """Create with a VERIFIABLE claim, then complete -> resolves, completes."""
    body = _line(ctx.product_id)
    body["context"] = {"eligibility": [_ecfg(ctx).get("verifiable")]}
    cid = (_create(ctx, body).json or {}).get("id")
    return _complete(ctx, cid, _success_pay())

def p_provisional_resolved(r, ctx):
    """DSC-017: 'At checkout completion, all remaining provisional claims MUST be
    resolved.' The completed response carries no applied discount still marked
    provisional."""
    if r.status != 200 or (r.json or {}).get("status") != "completed":
        return DEVIATION
    ap = _applied(r) or []
    return CLEAN if not any(isinstance(a, dict) and a.get("provisional") for a in ap) \
        else DEVIATION

# ======== CART-TO-CHECKOUT CONVERSION (cart.md "Cart to Checkout Conversion") ==
def _seed_cart(ctx, codes=None):
    body = {"line_items": [{"item": {"id": ctx.product_id}, "quantity": 2}]}
    if codes:
        body["discounts"] = {"codes": codes}
    r = fetch(ctx.shopping_endpoint, "/carts", "POST", body, _hdr())
    return (r.json or {}).get("id")

def f_convert_conflicting(ctx):
    """Convert a cart whose contents differ from the (conflicting) checkout-payload
    line_items — the Business MUST use the cart contents (CART-001)."""
    cid = _seed_cart(ctx)
    conflicting = (ctx.config.get("cart") or {}).get("second_product_id")
    return _create(ctx, {"cart_id": cid,
                         "line_items": [{"item": {"id": conflicting}, "quantity": 9}]})

def p_uses_cart_contents(r, ctx):
    """CART-001: 'Business MUST use cart contents and MUST ignore overlapping fields
    in the checkout payload.' The response line_items reflect the CART's product,
    not the conflicting payload product."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    ids = {(li.get("item") or {}).get("id") for li in r.json.get("line_items") or []}
    conflicting = (ctx.config.get("cart") or {}).get("second_product_id")
    if conflicting in ids:
        return DEVIATION                     # overlapping payload leaked in
    return CLEAN if ids == {ctx.product_id} else DEVIATION

def f_convert_codes(ctx):
    """Convert a cart that had a discount code applied — codes carry forward."""
    code = (ctx.config.get("discount") or {}).get("valid_code")
    cid = _seed_cart(ctx, codes=[code])
    return _create(ctx, {"cart_id": cid, "line_items": []})

def p_codes_carried(r, ctx):
    """DSC-009: 'businesses MUST carry forward any discount codes that were applied
    to the cart.' The converted checkout echoes the cart's code."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    code = (ctx.config.get("discount") or {}).get("valid_code")
    d = r.json.get("discounts") or {}
    codes = d.get("codes") or []
    applied = {a.get("code") for a in d.get("applied") or [] if isinstance(a, dict)}
    return CLEAN if code in codes or code in applied else DEVIATION

def f_convert_idempotent(ctx):
    """Convert the SAME cart twice; the second conversion MUST return the existing
    session rather than creating a new one (CART-002)."""
    cid = _seed_cart(ctx)
    _create(ctx, {"cart_id": cid, "line_items": []})       # first -> 201 Created
    return _create(ctx, {"cart_id": cid, "line_items": []})  # second -> existing

def p_idempotent_conversion(r, ctx):
    """CART-002: 'If an incomplete checkout already exists for the given cart_id,
    the business MUST return the existing checkout session rather than creating a
    new one.' The repeat conversion returns the existing session (HTTP 200, not a
    fresh 201 Created)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("id") and r.json.get("status") else DEVIATION

# ======== CART CANCEL (cart.md "Cancel Cart") =================================
def f_cancel_cart(ctx):
    cid = _seed_cart(ctx)
    return fetch(ctx.shopping_endpoint, f"/carts/{cid}/cancel", "POST", {}, _hdr())

def p_cancel_returns_state(r, ctx):
    """CART-018: 'Business MUST return the cart state before deletion.' The cancel
    response is the cart resource (its id + line_items), not a not_found error."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    if (r.json.get("ucp") or {}).get("status") == "error":
        return DEVIATION                     # not_found instead of the cart state
    return CLEAN if r.json.get("id") and r.json.get("line_items") else DEVIATION

# ======== IDEMPOTENCY (checkout-rest.md / cart-rest.md "Idempotency") =========
def f_checkout_idem_conflict(ctx):
    """Reuse one Idempotency-Key with a DIFFERENT body on create checkout."""
    key = "recv-idem-chk-" + uuid.uuid4().hex[:8]
    fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
          _line(ctx.product_id, 1), _hdr(key))
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _line(ctx.product_id, 2), _hdr(key))

def f_cart_idem_conflict(ctx):
    key = "recv-idem-cart-" + uuid.uuid4().hex[:8]
    body = {"line_items": [{"item": {"id": ctx.product_id}, "quantity": 1}]}
    fetch(ctx.shopping_endpoint, "/carts", "POST", body, _hdr(key))
    body2 = {"line_items": [{"item": {"id": ctx.product_id}, "quantity": 2}]}
    return fetch(ctx.shopping_endpoint, "/carts", "POST", body2, _hdr(key))

def p_idem_conflict_409(r, ctx):
    """CHK-048 / CART-026: 'return 409 Conflict if the key is reused with different
    parameters.' Detecting the conflict requires the server to have STORED the key
    with the original operation result."""
    return CLEAN if r.status == 409 else DEVIATION

# ======== SIGNATURE error code (signatures.md "Error Handling") ===============
def f_bad_alg(ctx):
    """POST a request whose RFC 9421 Signature-Input declares an UNSUPPORTED
    algorithm — the verifier must reject it before crypto verification."""
    kid = ((ctx.config.get("signature") or {}).get("request_private_jwk") or {}).get("kid", "x")
    params = f'("@method" "@path");keyid="{kid}";alg="rsa-pss-sha512"'
    h = _hdr()
    h["Signature-Input"] = f"sig1={params}"
    h["Signature"] = "sig1=:AAAA:"           # dummy — rejected on alg before verify
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _line(ctx.product_id), h)

def p_algorithm_unsupported(r, ctx):
    """SIG-035: 'algorithm_unsupported maps to HTTP 400 (signature algorithm not
    supported).'"""
    if r.status != 400 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("code") == "algorithm_unsupported" else DEVIATION

CHECKS_04_08_RECEIVER = [
    # ---- escalation / continue_url ------------------------------------------
    MCheck("checkout.escalation_continue_url", ["CHK-001", "CHK-014", "CHK-043"],
           "MUST", f_escalate, p_escalation_continue_url,
           ["status:402", "status:500", "drop:continue_url",
            "set:continue_url=\"\"", "set:status=\"completed\"", "empty", "corrupt-json"],
           cfg_needs=("payment.escalation_payment",), needs=("product",),
           transport="rest", versions=V0408),
    MCheck("checkout.escalation_continue_url_https", ["CHK-004"], "MUST",
           f_escalate, p_escalation_https,
           ["status:402", "drop:continue_url", "set:continue_url=\"http://insecure/3ds\"",
            "set:continue_url=\"/3ds/relative\"", "set:status=\"completed\"", "empty"],
           cfg_needs=("payment.escalation_payment",), needs=("product",),
           transport="rest", versions=V0408),
    MCheck("checkout.escalation_buyer_message", ["CHK-015"], "MUST",
           f_escalate, p_escalation_buyer_message,
           ["status:402", "drop:messages", "set:messages=[]",
            "set:messages=[{\"type\":\"info\",\"content\":\"x\"}]",
            "set:status=\"completed\"", "empty"],
           cfg_needs=("payment.escalation_payment",), needs=("product",),
           transport="rest", versions=V0408),
    # ---- eligibility ---------------------------------------------------------
    MCheck("eligibility.provisional_discount", ["DSC-014", "DSC-016"], "MUST",
           f_elig_recognized, p_provisional_discount,
           ["status:500", "drop:discounts",
            "set:discounts={\"codes\":[],\"applied\":[]}",
            "set:discounts={\"codes\":[],\"applied\":[{\"title\":\"x\",\"amount\":300}]}",
            "corrupt-json"],
           cfg_needs=("eligibility.verifiable",), needs=("product",),
           transport="rest", capability="dev.ucp.shopping.discount", versions=V0408),
    MCheck("eligibility.unrecognized_ignored", ["SAE-011"], "MUST",
           f_elig_unrecognized, p_unrecognized_ignored,
           ["status:400", "set:ucp={\"version\":\"2026-04-08\",\"status\":\"error\"}",
            "drop:messages", "set:messages=[]",
            "set:messages=[{\"type\":\"error\",\"code\":\"eligibility_invalid\"}]",
            "corrupt-json"],
           cfg_needs=("eligibility.unrecognized",), needs=("product",),
           transport="rest", versions=V0408),
    MCheck("eligibility.unrecognized_not_blocking", ["SAE-014"], "MUST",
           f_elig_unrecognized_complete, p_not_blocked,
           ["status:400", "set:status=\"requires_escalation\"",
            "set:status=\"incomplete\"", "drop:status", "empty"],
           cfg_needs=("eligibility.unrecognized",), needs=("product",),
           transport="rest", versions=V0408),
    MCheck("eligibility.completion_blocked", ["SAE-013", "SAE-018", "CHK-056"], "MUST",
           f_elig_unverifiable_complete, p_completion_blocked,
           ["set:status=\"completed\"", "status:500"],
           cfg_needs=("eligibility.unverifiable",), needs=("product",),
           transport="rest", versions=V0408),
    MCheck("eligibility.invalid_message", ["SAE-017", "SAE-020", "SAE-019"], "MUST",
           f_elig_unverifiable_complete, p_eligibility_invalid_message,
           ["status:500", "drop:messages", "set:messages=[]",
            "set:messages=[{\"type\":\"error\",\"code\":\"eligibility_invalid\",\"severity\":\"unrecoverable\"}]",
            "set:messages=[{\"type\":\"warning\",\"code\":\"eligibility_invalid\",\"severity\":\"recoverable\"}]",
            "set:ucp={\"version\":\"2026-04-08\",\"status\":\"error\"}", "corrupt-json"],
           cfg_needs=("eligibility.unverifiable",), needs=("product",),
           transport="rest", versions=V0408),
    MCheck("eligibility.provisional_resolved", ["DSC-017"], "MUST",
           f_elig_verifiable_complete, p_provisional_resolved,
           ["status:500", "set:status=\"ready_for_complete\"",
            "set:discounts={\"applied\":[{\"title\":\"x\",\"amount\":300,\"provisional\":true}]}",
            "corrupt-json"],
           cfg_needs=("eligibility.verifiable",), needs=("product",),
           transport="rest", capability="dev.ucp.shopping.discount", versions=V0408),
    # ---- cart-to-checkout conversion ----------------------------------------
    MCheck("cart.conversion_uses_cart_contents", ["CART-001"], "MUST",
           f_convert_conflicting, p_uses_cart_contents,
           ["status:500", "set:line_items=[{\"item\":{\"id\":$PRODUCT2},\"quantity\":9}]",
            "set:line_items=[{\"item\":{\"id\":$PRODUCT},\"quantity\":1},{\"item\":{\"id\":$PRODUCT2},\"quantity\":9}]",
            "set:line_items=[]", "corrupt-json"],
           cfg_needs=("cart.second_product_id",), needs=("product",),
           transport="rest", capability="dev.ucp.shopping.cart", versions=V0408),
    MCheck("cart.conversion_carries_codes", ["DSC-009"], "MUST",
           f_convert_codes, p_codes_carried,
           ["status:500", "drop:discounts",
            "set:discounts={\"codes\":[],\"applied\":[]}", "corrupt-json"],
           cfg_needs=("discount.valid_code",), needs=("product",),
           transport="rest", capability="dev.ucp.shopping.cart", versions=V0408),
    MCheck("cart.conversion_idempotent", ["CART-002"], "MUST",
           f_convert_idempotent, p_idempotent_conversion,
           ["status:201", "status:500", "drop:id", "drop:status", "empty"],
           cfg_needs=("cart.second_product_id",), needs=("product",),
           transport="rest", capability="dev.ucp.shopping.cart", versions=V0408),
    # ---- cart cancel ---------------------------------------------------------
    MCheck("cart.cancel_returns_state", ["CART-018"], "MUST",
           f_cancel_cart, p_cancel_returns_state,
           ["status:500", "drop:id", "drop:line_items",
            "set:ucp={\"version\":\"2026-04-08\",\"status\":\"error\"}", "empty"],
           cfg_needs=("cart.second_product_id",), needs=("product",),
           transport="rest", capability="dev.ucp.shopping.cart", versions=V0408),
    # ---- idempotency ---------------------------------------------------------
    MCheck("checkout.idempotency_conflict", ["CHK-048"], "MUST",
           f_checkout_idem_conflict, p_idem_conflict_409,
           ["status:200", "status:201", "status:410"],
           needs=("product",), transport="rest", versions=V0408),
    MCheck("cart.idempotency_conflict", ["CART-026"], "MUST",
           f_cart_idem_conflict, p_idem_conflict_409,
           ["status:200", "status:201", "status:422"],
           cfg_needs=("cart.second_product_id",), needs=("product",),
           transport="rest", capability="dev.ucp.shopping.cart", versions=V0408),
    # ---- signature algorithm error code -------------------------------------
    MCheck("signatures.algorithm_unsupported", ["SIG-035"], "MUST",
           f_bad_alg, p_algorithm_unsupported,
           ["status:200", "status:201", "status:401", "drop:code",
            "set:code=\"signature_invalid\"", "empty", "corrupt-json"],
           cfg_needs=("signature.request_private_jwk",), needs=("product",),
           transport="rest", versions=V0408),
]
