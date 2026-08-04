#!/usr/bin/env python3
"""
merchant_checks_04_08_receiver.py — 2026-04-08-scoped behavioral checks for the
RECEIVER tier: continue_url/escalation, cart-to-checkout conversion, eligibility
verification, idempotency,.

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
import sys, uuid, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, Resp, CLEAN, DEVIATION              # noqa: E402
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

def _line(ctx, qty=1):
    """The create body a CONFORMANT platform sends at 2026-04-08: line_items only.

    checkout.json annotates every other member with `ucp_request: omit` — `currency`
    included ("merchants determine currency", derived from address, context and geo IP).
    So this minimal body is not a lazy shortcut, it is the correct request, and the
    generated CheckoutCreateRequest carries no `currency` field precisely because of
    that annotation.

    Resist the temptation to add fields here to make a server happy. Doing so probes
    merchants with a request no conformant platform would send, and it hides exactly the
    class of defect a server has when it depends on a field the spec told platforms to
    omit.
    """
    return {"line_items": [{"item": {"id": ctx.product_id}, "quantity": qty}]}

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
    cid = (_create(ctx, _line(ctx)).json or {}).get("id")
    return _complete(ctx, cid, _pcfg(ctx).get("escalation_payment"))

def p_escalation_continue_url(r, ctx):
    """CHK-001/CHK-014/CHK-043: a requires_escalation response MUST carry
    continue_url ('Businesses MUST provide continue_url when returning status =
    requires_escalation')."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    if r.json.get("status") != "requires_escalation":
        return DEVIATION
    cu = r.json.get("continue_url")
    return CLEAN if isinstance(cu, str) and cu else DEVIATION

def p_escalation_https(r, ctx):
    """CHK-004: 'The continue_url MUST be an absolute HTTPS URL.'"""
    if r.status not in (200, 201) or (r.json or {}).get("status") != "requires_escalation":
        return DEVIATION
    cu = (r.json or {}).get("continue_url")
    return CLEAN if isinstance(cu, str) and cu.startswith("https://") else DEVIATION

def p_requires_star_implies_escalation(r, ctx):
    """ERR-004: a response carrying any message with requires_* severity MUST have
    ucp/checkout status requires_escalation (checkout.md: 'Errors with requires_*
    severity contribute to status: requires_escalation'). Verified on the soft-
    decline response, which carries a requires_buyer_input message."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    has_star = any(isinstance(m, dict) and str(m.get("severity", "")).startswith("requires_")
                   for m in _messages(r))
    if not has_star:
        return DEVIATION                    # scenario must produce a requires_* message
    return CLEAN if r.json.get("status") == "requires_escalation" else DEVIATION

def p_escalation_buyer_message(r, ctx):
    """CHK-015: a requires_escalation response MUST include at least one message
    with severity requires_buyer_input or requires_buyer_review."""
    if r.status not in (200, 201) or (r.json or {}).get("status") != "requires_escalation":
        return DEVIATION
    return CLEAN if any(isinstance(m, dict) and m.get("severity") in
                        ("requires_buyer_input", "requires_buyer_review")
                        for m in _messages(r)) else DEVIATION

# ======== ELIGIBILITY (checkout.md / discount.md "Eligibility Claims") =========
def f_elig_recognized(ctx):
    """Create with a RECOGNIZED eligibility claim (config: eligibility.verifiable)."""
    body = _line(ctx)
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
    body = _line(ctx)
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
    body = _line(ctx)
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
    body = _line(ctx)
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
          _line(ctx, 1), _hdr(key))
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _line(ctx, 2), _hdr(key))

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
    MCheck("checkout.requires_star_implies_escalation", ["ERR-004"], "MUST",
           f_escalate, p_requires_star_implies_escalation,
           ["status:402", "set:status=\"incomplete\"", "set:status=\"ready_for_complete\"",
            "drop:status", "set:messages=[]", "empty", "corrupt-json"],
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
    # SAE-012 rides the same scenario deliberately: context.json's own normative
    # sentence ("Eligibility and policy enforcement MUST occur at checkout time
    # using binding transaction data") is exactly what this exhibits — the claim
    # the CONTEXT carried was accepted provisionally at create, and the binding
    # enforcement happened AT COMPLETION, where the unverifiable claim blocked the
    # transaction (context was not treated as authoritative).
    MCheck("eligibility.completion_blocked",
           ["SAE-013", "SAE-018", "CHK-056", "SAE-012"], "MUST",
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
]

# ======== RECEIVER-GAP tier 2 (2026-07 wave: CHK-013/050/051/053, DSC-013, ======
# ======== PAY-014/015, SAE-008 — the remaining needs-receiver MUSTs) ============
def _norm_proj(j):
    """The NORMATIVE projection of a checkout response — everything the spec makes
    input-determined, with per-request volatility (ids, urls) excluded. Two
    responses to the same input MUST agree on this projection."""
    if not isinstance(j, dict):
        return None
    lines = [((li.get("item") or {}).get("id"), li.get("quantity"),
              (li.get("item") or {}).get("price"))
             for li in j.get("line_items") or [] if isinstance(li, dict)]
    totals = [(t.get("type"), t.get("amount"))
              for t in j.get("totals") or [] if isinstance(t, dict)]
    d = j.get("discounts") if isinstance(j.get("discounts"), dict) else {}
    applied = sorted((a.get("code"), a.get("amount"), bool(a.get("automatic")))
                     for a in d.get("applied") or [] if isinstance(a, dict))
    msgs = sorted((m.get("type"), m.get("code"))
                  for m in j.get("messages") or [] if isinstance(m, dict))
    return {"status": j.get("status"), "currency": j.get("currency"),
            "line_items": lines, "totals": totals, "applied": applied,
            "messages": msgs}

# ---- CHK-013: deterministic checkout logic --------------------------------------
def f_det_replay(ctx):
    """The SAME create request twice (fresh idempotency keys, so both EXECUTE).
    The first response's normative projection is stashed on the ctx; the second is
    the golden the predicate compares against it."""
    body = _line(ctx, 2)
    first = _create(ctx, body)
    ctx._det_first = _norm_proj(first.json) if first.status in (200, 201) else None
    return _create(ctx, body)

def p_deterministic(r, ctx):
    """CHK-013: 'Logic handling the checkout sessions MUST be deterministic' —
    replaying an identical create yields the same normative outcome (status,
    priced line items, totals, applied discounts, message codes); only volatile
    identity (session id, urls) may differ."""
    if r.status not in (200, 201) or getattr(ctx, "_det_first", None) is None:
        return DEVIATION
    return CLEAN if _norm_proj(r.json) == ctx._det_first else DEVIATION

# ---- CHK-050: protocol errors carry a JSON body with code + content -------------
def p_protocol_error_shape(r, ctx):
    """CHK-050: 'Protocol errors: Return appropriate HTTP status code (401, 403,
    409, 429, 503) with JSON body containing code and content.' Exercised on the
    idempotency-key conflict, whose status the spec pins to 409 (CHK-048).
    PLACEMENT-PERMISSIVE, value-strict (the _err_codes precedent): the pair may
    sit at the top level (the flat transport-error body) or inside a messages[]
    error entry (the envelope form the official reference emits) — the quote
    demands the body CONTAIN code and content, not a nesting depth."""
    if r.status != 409 or not isinstance(r.json, dict):
        return DEVIATION
    j = r.json
    if isinstance(j.get("code"), str) and j["code"] \
       and isinstance(j.get("content"), str) and j["content"]:
        return CLEAN
    return CLEAN if any(isinstance(m, dict)
                        and isinstance(m.get("code"), str) and m["code"]
                        and isinstance(m.get("content"), str) and m["content"]
                        for m in j.get("messages") or []) else DEVIATION

# ---- CHK-051/CHK-053: unavailable merchandise is a BUSINESS OUTCOME -------------
def f_create_all_unavailable(ctx):
    """Create where EVERY requested item is unavailable (the seeded zero-stock
    product) — checkout.md: the business cannot create the resource."""
    return _create(ctx, {"line_items": [
        {"item": {"id": ctx.config.get("out_of_stock_id")}, "quantity": 1}]})

def p_all_unavailable_error_envelope(r, ctx):
    """CHK-053: all items unavailable -> the response carries ucp.status:'error'
    with a messages array describing the failure and NO resource body (no
    checkout id / line_items / status). The cited checkout.md clause pins the
    ENVELOPE discriminator, not an HTTP status — the HTTP-200 business-outcome
    rule is CHK-051's row (graded separately, config-gated: the official
    reference still answers 400 here); a 5xx crash is never acceptable."""
    if not (200 <= r.status < 500) or not isinstance(r.json, dict):
        return DEVIATION
    j = r.json
    if (j.get("ucp") or {}).get("status") != "error":
        return DEVIATION
    if not any(isinstance(m, dict) and m.get("type") == "error"
               for m in j.get("messages") or []):
        return DEVIATION
    return DEVIATION if (j.get("id") or j.get("line_items") or j.get("status")) \
        else CLEAN

def f_create_partial_unavailable(ctx):
    """Create mixing one purchasable line with one unavailable line — a business
    outcome that still yields a checkout resource plus messages."""
    return _create(ctx, {"line_items": [
        {"item": {"id": ctx.product_id}, "quantity": 1},
        {"item": {"id": ctx.config.get("out_of_stock_id")}, "quantity": 1}]})

def p_partial_unavailable_outcome(r, ctx):
    """CHK-051: 'Business outcomes (including unavailable merchandise) return
    HTTP 200 with the UCP envelope and messages array' — the mixed create yields
    a checkout resource carrying ONLY the purchasable line, with the
    unavailability surfaced in messages (registered codes out_of_stock /
    item_unavailable), never a protocol 4xx."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    j = r.json
    ucp = j.get("ucp")
    if not isinstance(ucp, dict) or ucp.get("status") == "error":
        return DEVIATION
    lines = j.get("line_items")
    if not j.get("id") or not isinstance(lines, list) or not lines:
        return DEVIATION
    bad = ctx.config.get("out_of_stock_id")
    if any((li.get("item") or {}).get("id") == bad for li in lines
           if isinstance(li, dict)):
        return DEVIATION                    # the unavailable item was "sold" anyway
    return CLEAN if any(isinstance(m, dict)
                        and m.get("code") in ("out_of_stock", "item_unavailable")
                        for m in j.get("messages") or []) else DEVIATION

# ---- DSC-013: automatic discounts cannot be removed by the platform -------------
def f_clear_codes_update(ctx):
    """Create a session that qualifies for the merchant's AUTOMATIC discount
    (config discount.automatic names a qualifying basket), verify it applied,
    then UPDATE with discounts.codes=[] — the platform's only removal lever."""
    auto = (ctx.config.get("discount") or {}).get("automatic") or {}
    body = {"line_items": [{"item": {"id": auto.get("product_id")},
                            "quantity": auto.get("quantity", 1)}]}
    created = _create(ctx, body)
    j = created.json if isinstance(created.json, dict) else {}
    if not any(isinstance(a, dict) and a.get("automatic")
               for a in _applied(created) or []):
        return Resp(0, {}, b'{"probe":"the config discount.automatic basket produced '
                           b'no automatic discount entry; cannot exercise DSC-013"}')
    cid = j.get("id")
    body["discounts"] = {"codes": []}
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}", "PUT",
                 body, _hdr())

def p_automatic_survives(r, ctx):
    """DSC-013: 'Cannot be removed by the platform' — after the platform clears
    discounts.codes, the automatic discount entry is STILL applied."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if any(isinstance(a, dict) and a.get("automatic")
                        for a in _applied(r) or []) else DEVIATION

# ---- PAY-014: handler_id validated against the advertised set -------------------
def f_unknown_handler_complete(ctx):
    """Complete with an instrument whose handler_id is NOT in the advertised
    handler set (an otherwise-valid success credential). Return the completion
    response when the merchant (wrongly) completed; else the session's current
    state — the predicate asserts it was NOT completed."""
    cid = (_create(ctx, _line(ctx)).json or {}).get("id")
    pay = json.loads(json.dumps(_success_pay()))
    pay["payment"]["instruments"][0]["handler_id"] = \
        "spck_handler_not_in_advertised_set"
    done = _complete(ctx, cid, pay)
    if done.status in (200, 201) and isinstance(done.json, dict) \
       and done.json.get("status") == "completed":
        return done                          # processed with an unvalidated handler
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}", "GET",
                 None, _hdr())

def p_not_completed(r, ctx):
    """PAY-014: 'Validate handler_id before processing (ensure handler is in the
    advertised set)' — the session is NOT completed after a completion attempt
    carrying an unknown handler_id."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("status") and r.json["status"] != "completed" \
        else DEVIATION

# ---- PAY-015: handlers list dynamically filtered by cart context ----------------
def _handler_keys(r):
    ph = ((r.json or {}).get("ucp") or {}).get("payment_handlers") \
        if isinstance(r.json, dict) else None
    return set(ph.keys()) if isinstance(ph, dict) else set()

def f_context_filtered_create(ctx):
    """Baseline create (unrestricted product): the advertised handler set includes
    the context-sensitive handler. Golden: create with the config-named product
    the handler is not offered for — the merchant MUST filter it out."""
    filt = (ctx.config.get("payment") or {}).get("filtered") or {}
    base = _create(ctx, _line(ctx))
    if filt.get("handler_key") not in _handler_keys(base):
        return Resp(0, {}, b'{"probe":"baseline response never offers the '
                           b'context-sensitive handler (payment.filtered.handler_key); '
                           b'the filtering scenario would be vacuous"}')
    return _create(ctx, {"line_items": [
        {"item": {"id": filt.get("product_id")}, "quantity": 1}]})

def p_handler_filtered(r, ctx):
    """PAY-015: 'Businesses MUST filter the handlers list based on the context of
    the cart' — the restricted basket's response omits the context-sensitive
    handler while still offering the base handler."""
    if r.status not in (200, 201):
        return DEVIATION
    filt = (ctx.config.get("payment") or {}).get("filtered") or {}
    keys = _handler_keys(r)
    if not keys or filt.get("handler_key") in keys:
        return DEVIATION
    base_key = (ctx.config.get("payment") or {}).get("handler_key")
    return CLEAN if (base_key in keys if base_key else True) else DEVIATION

# ---- SAE-008: attribution presence/absence changes nothing ----------------------
_ATTRIBUTION = {"campaign_id": "18234567890", "campaign_source": "spck-suite",
                "campaign_medium": "cpc", "gclid": "EAIaIQobChMIspckprobe"}

def f_attribution_replay(ctx):
    """The same create WITHOUT and then WITH the top-level attribution field
    (overview.md Attribution example shape). The attribution-free projection is
    stashed; the attribution-carrying response is the golden."""
    body = _line(ctx, 2)
    plain = _create(ctx, body)
    ctx._sae008_plain = _norm_proj(plain.json) if plain.status in (200, 201) else None
    with_attr = dict(body)
    with_attr["attribution"] = dict(_ATTRIBUTION)
    return _create(ctx, with_attr)

def p_attribution_no_effect(r, ctx):
    """SAE-008: 'the field's presence or absence MUST NOT affect the response or
    negotiation' — the normative projection with attribution equals the one
    without it (and the request was not rejected)."""
    if r.status not in (200, 201) or getattr(ctx, "_sae008_plain", None) is None:
        return DEVIATION
    return CLEAN if _norm_proj(r.json) == ctx._sae008_plain else DEVIATION

CHECKS_04_08_RECEIVER_GAPS = [
    MCheck("checkout.deterministic_logic", ["CHK-013"], "MUST",
           f_det_replay, p_deterministic,
           ["status:500", "set:status=\"requires_escalation\"",
            "set:currency=\"EUR\"", "set:totals=[]", "set:line_items=[]",
            "corrupt-json", "empty"],
           needs=("product",), transport="rest", versions=V0408),
    # kill set is deliberately SHAPE-INDEPENDENT (status/empty/corrupt): the
    # placement-permissive predicate accepts two conformant body shapes, and a
    # field-drop that kills one golden's shape survives the other's
    MCheck("checkout.protocol_error_shape", ["CHK-050"], "MUST",
           f_checkout_idem_conflict, p_protocol_error_shape,
           ["status:200", "status:201", "status:400", "corrupt-json", "empty"],
           needs=("product",), transport="rest", versions=V0408),
    MCheck("checkout.unavailable_all_error_envelope", ["CHK-053"], "MUST",
           f_create_all_unavailable, p_all_unavailable_error_envelope,
           ["status:500", "status:503", "drop:messages", "set:messages=[]",
            "set:ucp={\"version\":\"2026-04-08\",\"status\":\"success\"}",
            "set:id=\"chk_leaked\"", "corrupt-json", "empty"],
           cfg_needs=("out_of_stock_id",), needs=("product",),
           transport="rest", versions=V0408),
    # config-gated on unavailable.business_outcome: the two-layer HTTP-200
    # business-outcome contract is a capability the CONTROLLED golden implements
    # and the official reference does not yet (it still answers 400 with the
    # error envelope) — "correctly dormant, do not force" (audit rule; the
    # webhooks.signed precedent). REF_CONFIG documents the tripwire.
    MCheck("checkout.unavailable_business_outcome", ["CHK-051"], "MUST",
           f_create_partial_unavailable, p_partial_unavailable_outcome,
           ["status:400", "drop:messages", "set:messages=[]",
            "set:ucp={\"version\":\"2026-04-08\",\"status\":\"error\"}",
            "set:line_items=[]", "drop:id", "corrupt-json", "empty"],
           cfg_needs=("out_of_stock_id", "unavailable.business_outcome"),
           needs=("product",), transport="rest", versions=V0408),
    MCheck("discount.automatic_not_removable", ["DSC-013"], "MUST NOT",
           f_clear_codes_update, p_automatic_survives,
           ["status:500", "drop:discounts",
            "set:discounts={\"codes\":[],\"applied\":[]}", "corrupt-json", "empty"],
           cfg_needs=("discount.automatic",), needs=("product",),
           capability="dev.ucp.shopping.discount", transport="rest", versions=V0408),
    MCheck("payment.handler_id_validated", ["PAY-014"], "MUST",
           f_unknown_handler_complete, p_not_completed,
           ["status:500", "set:status=\"completed\"", "drop:status",
            "corrupt-json", "empty"],
           needs=("product",), transport="rest", versions=V0408),
    MCheck("payment.handlers_context_filtered", ["PAY-015"], "MUST",
           f_context_filtered_create, p_handler_filtered,
           ["status:500", "drop:ucp",
            "set:ucp.payment_handlers={\"dev.spck.tokenpay\":[{\"id\":\"spck_tokenpay\"}],"
            "\"dev.spck.giftpay\":[{\"id\":\"spck_giftpay\"}]}",
            "set:ucp.payment_handlers={}", "corrupt-json"],
           cfg_needs=("payment.filtered",), needs=("product",),
           transport="rest", versions=V0408),
    MCheck("signals.attribution_no_effect", ["SAE-008"], "MUST NOT",
           f_attribution_replay, p_attribution_no_effect,
           ["status:400", "status:500", "set:currency=\"EUR\"", "set:totals=[]",
            "set:status=\"requires_escalation\"", "corrupt-json", "empty"],
           needs=("product",), transport="rest", versions=V0408),
]
