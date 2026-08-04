#!/usr/bin/env python3
"""
merchant_checks_01_11_01_23_receiver.py — 01-era (2026-01-11 + 2026-01-23) receiver
tier: the AP2 merchant_authorization embed/algorithm MUSTs, the requires_escalation
continue_url MUSTs, and Idempotency-Key storage — all exhibited by the controlled
fixture in its 01-era modes and already oracle-validated in selfcheck.py (the
escalation lifecycle and the ap2 subtree are driven per version there).

VERSION SCOPING: the 2026-04-08 registers RENUMBERED these families (PAY/CHK/IDM ids
mean OTHER requirements at 04-08), so every check is versions=("2026-01-11",
"2026-01-23") and the module carries BOTH file-name tokens AND the VERSIONS marker
below, so coverage/matrix.py attributes its citations to the 01-era versions only —
never leaking a citation to 2026-04-08. The AP2 ids were verified textually identical
at 2026-01-11 and 2026-01-23 (see PAY-035's note in merchant_checks_01_23.py); the
CHK/IDM ids likewise. Reference target: the controlled fixture booted
`--spec-version 2026-01-23` (and `2026-01-11`), gated by validate_merchant_checks
--golden controlled. Verbatim MUSTs re-read in
conformance/.vendor/ucp-2026-01-23/docs/specification/{ap2-mandates,checkout,
checkout-rest}.md.

NOTE: imported lazily by merchant_checks.all_checks(); pulls MCheck/_hdr from there.
"""
import sys, uuid, json, re, base64, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, Resp, CLEAN, DEVIATION, INCONCLUSIVE  # noqa: E402
from merchant_checks import MCheck, _hdr                      # noqa: E402

# whole-file attribution bound: both 01-era versions (per-check versions= matches)
VERSIONS = ("2026-01-11", "2026-01-23")
V_OLD = VERSIONS

def _pcfg(ctx):  return ctx.config.get("payment") or {}

def _create_payload(ctx):
    return {"id": str(uuid.uuid4()), "currency": ctx.config.get("currency", "USD"),
            "line_items": [{"id": "li_1", "quantity": 1,
                            "item": {"id": ctx.product_id, "price": 1000}, "totals": []}],
            "payment": {"instruments": [], "handlers": ctx.config.get("payment_handlers", [])},
            "status": "incomplete", "ucp": {"version": ctx.version}, "totals": [], "links": []}

def _create(ctx):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _create_payload(ctx), _hdr())

def _complete(ctx, cid, body):
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete",
                 "POST", body, _hdr())

# ======== CHK-037: deterministic checkout logic (checkout.md#L267) =============
# 01-era analogue of CHK-013@04-08. The register text is verbatim-identical at
# 2026-01-11 and 2026-01-23 ("Logic handling the checkout sessions MUST be
# deterministic"); the 04-08 register renumbered it to CHK-013, so this check is
# versions=V_OLD and never leaks to 04-08.
_AVAIL_CODES = {"out_of_stock", "item_unavailable", "insufficient_stock",
                "insufficient_inventory", "unavailable"}

def _create_body(ctx, body):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", body, _hdr())

def _resp_handler_keys(j):
    """UNION of payment-handler keys a checkout response advertises across both
    01-era shapes (ucp.payment_handlers @01-23, root payment.handlers @01-11) — so
    the determinism projection is sensitive to handler-set nondeterminism (a
    randomized handler advertisement must NOT slip through)."""
    keys = set()
    ph = (j.get("ucp") or {}).get("payment_handlers") if isinstance(j.get("ucp"), dict) else None
    if isinstance(ph, dict):
        keys |= set(ph.keys())
    hs = (j.get("payment") or {}).get("handlers") if isinstance(j.get("payment"), dict) else None
    if isinstance(hs, list):
        keys |= {h.get("name") for h in hs if isinstance(h, dict) and h.get("name")}
    return keys

def _norm_proj(j):
    """The input-determined NORMATIVE projection of a checkout response — status,
    currency, priced line items, totals, applied-discount codes, message codes,
    the advertised payment-handler set, and whether continue_url is present.
    Per-request volatility (session id, url VALUES) is excluded; two responses to
    the SAME input MUST agree on this projection."""
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
            "line_items": lines, "totals": totals, "applied": applied, "messages": msgs,
            "handlers": tuple(sorted(k for k in _resp_handler_keys(j) if k)),
            "continue_url_present": j.get("continue_url") is not None}

def _availability_shift(first_j, second_j):
    """True when the second response reflects a STOCK/availability STATE change the
    first did not (fewer priced lines, or a new availability-class message). Two
    identical creates can legitimately differ this way on a conformant merchant that
    RESERVES inventory at create — the deterministic-LOGIC MUST (same input + same
    state -> same output) is not violated by a state change the FIRST call caused.
    Such a difference is graded INCONCLUSIVE, never a determinism deviation."""
    if not (isinstance(first_j, dict) and isinstance(second_j, dict)):
        return False
    if len(second_j.get("line_items") or []) < len(first_j.get("line_items") or []):
        return True
    def avail(j):
        return {m.get("code") for m in (j.get("messages") or [])
                if isinstance(m, dict) and m.get("code") in _AVAIL_CODES}
    return bool(avail(second_j) - avail(first_j))

def f_det_replay(ctx):
    """The SAME create request twice (fresh idempotency keys so BOTH execute). The
    first response (projection + raw json) is stashed on ctx; the second is the
    golden the predicate compares against it."""
    body = _create_payload(ctx)
    body["line_items"][0]["quantity"] = 2
    first = _create_body(ctx, body)
    ok = first.status in (200, 201) and isinstance(first.json, dict)
    ctx._det01_first = _norm_proj(first.json) if ok else None
    ctx._det01_first_json = first.json if ok else None
    return _create_body(ctx, body)

def p_deterministic(r, ctx):
    """CHK-037: replaying an identical create yields the same normative outcome
    (status, priced line items, totals, applied discounts, message codes, advertised
    handler set, continue_url presence); only volatile identity (session id, url
    values) may differ. A first create that did not succeed -> INCONCLUSIVE (a
    transient error is not a determinism finding); a difference explained solely by
    an inventory/availability STATE change the first call caused -> INCONCLUSIVE."""
    if getattr(ctx, "_det01_first", None) is None:
        return INCONCLUSIVE                  # first create not 2xx: cannot attribute
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    if _availability_shift(getattr(ctx, "_det01_first_json", None), r.json):
        return INCONCLUSIVE                  # state change between calls, not logic
    return CLEAN if _norm_proj(r.json) == ctx._det01_first else DEVIATION

# ======== AP2 merchant_authorization (ap2-mandates.md) ========================
def f_ap2(ctx):
    """Any checkout response from an AP2-emitting 01-era merchant carries
    ap2.merchant_authorization (config flag ap2:true)."""
    return _create(ctx)

def _decode_ma_header(r):
    """Return the decoded JWS protected header of ap2.merchant_authorization, or
    None if it is absent / not a detached-content JWS with a b64url-JSON header."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return None
    ma = (r.json.get("ap2") or {}).get("merchant_authorization") \
        if isinstance(r.json.get("ap2"), dict) else None
    if not isinstance(ma, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.\.[A-Za-z0-9_-]+", ma):
        return None
    head = ma.split("..")[0]
    try:
        hdr = json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))
    except Exception:
        return None
    return hdr if isinstance(hdr, dict) else None

def p_ap2_embedded(r, ctx):
    """PAY-019/PAY-021/PAY-027: the business MUST embed its signature
    (merchant_authorization) in the checkout response body under
    ap2.merchant_authorization — present and a well-formed detached-content JWS."""
    return CLEAN if _decode_ma_header(r) is not None else DEVIATION

def p_ap2_algorithm(r, ctx):
    """PAY-026: 'All signatures MUST use one of the following algorithms'
    (ES256/ES384/ES512). The JWS protected header's alg is an approved ES* alg."""
    hdr = _decode_ma_header(r)
    if hdr is None:
        return DEVIATION
    return CLEAN if hdr.get("alg") in ("ES256", "ES384", "ES512") else DEVIATION

# ======== requires_escalation continue_url (checkout.md) ======================
def f_escalate(ctx):
    """Complete with the 3DS soft-decline credential -> status=requires_escalation
    (config: payment.escalation_payment)."""
    cid = (_create(ctx).json or {}).get("id")
    return _complete(ctx, cid, _pcfg(ctx).get("escalation_payment"))

def p_escalation_continue_url(r, ctx):
    """CHK-025/CHK-038: 'Businesses MUST provide continue_url when returning
    status = requires_escalation.'"""
    if r.status != 200 or (r.json or {}).get("status") != "requires_escalation":
        return DEVIATION
    cu = (r.json or {}).get("continue_url")
    return CLEAN if isinstance(cu, str) and cu else DEVIATION

def p_escalation_https(r, ctx):
    """CHK-028: 'continue_url MUST be an absolute HTTPS URL.'"""
    if r.status != 200 or (r.json or {}).get("status") != "requires_escalation":
        return DEVIATION
    cu = (r.json or {}).get("continue_url")
    return CLEAN if isinstance(cu, str) and cu.startswith("https://") else DEVIATION

# ======== Idempotency-Key storage (checkout-rest.md) ==========================
def f_idem_conflict(ctx):
    """Reuse one Idempotency-Key with a DIFFERENT body -> 409 (proves the server
    STORED the key with its original operation result)."""
    key = "recv-idem01-" + uuid.uuid4().hex[:8]
    p1 = _create_payload(ctx)
    fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p1, _hdr(key))
    p2 = _create_payload(ctx); p2["line_items"][0]["quantity"] = 2
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p2, _hdr(key))

def p_idem_conflict_409(r, ctx):
    """IDM-002: 'When an Idempotency-Key is provided, the server MUST store the key
    with the operation result ... and return 409 Conflict if the key is reused with
    different parameters.'"""
    return CLEAN if r.status == 409 else DEVIATION

# ======== PAY-012: dynamic handler filtering by cart context (overview.md#L642) ==
# 01-era analogue of PAY-015@04-08 ("Businesses MUST filter the handlers list based
# on the context of the cart"). The advertised handler set lives in two shapes at
# 01-era: 2026-01-23 checkout responses carry ucp.payment_handlers (a keyed object);
# 2026-01-11 responses carry the root payment.handlers list. The predicate reads
# both. Verbatim-identical MUST at 2026-01-11 and 2026-01-23; 04-08 renumbered it to
# PAY-015, so this check is versions=V_OLD.
def _handler_keys_0123(j):
    """The reverse-domain handler keys a 2026-01-23 checkout response advertises,
    from its NORMATIVE shape ucp.payment_handlers (ucp.json response_checkout_schema
    requires it). Reads only that shape so a non-normative echo of a root `payment`
    object cannot false-flag the check."""
    ph = (j.get("ucp") or {}).get("payment_handlers") if isinstance(j.get("ucp"), dict) else None
    return set(ph.keys()) if isinstance(ph, dict) else set()

def _handler_keys_0111(j):
    """The reverse-domain handler keys a 2026-01-11 checkout response advertises,
    from its NORMATIVE shape: the root payment.handlers list (checkout.json requires
    the root `payment` object at 2026-01-11; payment.json handlers[] of
    payment_handler.json entries, each carrying `name`)."""
    hs = (j.get("payment") or {}).get("handlers") if isinstance(j.get("payment"), dict) else None
    return {h.get("name") for h in hs if isinstance(h, dict) and h.get("name")} \
        if isinstance(hs, list) else set()

def _create_product(ctx, pid):
    p = _create_payload(ctx)
    p["line_items"][0]["item"]["id"] = pid
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr())

def _mk_handler_filtered(keys_fn):
    """Build the (fetch_fn, predicate) pair for the PAY-012 dynamic-filtering check
    reading a specific version's normative handler shape via `keys_fn`."""
    def fetch_fn(ctx):
        """Baseline create (default product): the response advertises the
        context-sensitive handler. Golden: create with the config-named product the
        handler is NOT offered for — the business MUST filter it out. A baseline that
        never offers the context handler makes the scenario vacuous (INCONCLUSIVE);
        a restricted create that is rejected for missing config data -> INCONCLUSIVE
        (a data problem, not a conformance deviation)."""
        filt = (ctx.config.get("payment") or {}).get("filtered") or {}
        base = _create(ctx)
        if filt.get("handler_key") not in keys_fn(base.json if isinstance(base.json, dict) else {}):
            return Resp(0, {}, b'{"probe":"baseline never offers the context-sensitive '
                               b'handler (payment.filtered.handler_key); PAY-012 vacuous"}')
        return _create_product(ctx, filt.get("product_id"))

    def predicate(r, ctx):
        """PAY-012: the restricted basket's response OMITS the context-sensitive
        handler while still offering the base handler (read from this version's
        normative handler shape)."""
        if r.status == 0:
            return INCONCLUSIVE
        if r.status not in (200, 201) or not isinstance(r.json, dict):
            return INCONCLUSIVE if r.status in (400, 404, 422) else DEVIATION
        filt = (ctx.config.get("payment") or {}).get("filtered") or {}
        keys = keys_fn(r.json)
        if not keys or filt.get("handler_key") in keys:
            return DEVIATION
        base_key = (ctx.config.get("payment") or {}).get("handler_key")
        return CLEAN if (base_key in keys if base_key else True) else DEVIATION
    return fetch_fn, predicate

f_handler_filtered_0123, p_handler_filtered_0123 = _mk_handler_filtered(_handler_keys_0123)
f_handler_filtered_0111, p_handler_filtered_0111 = _mk_handler_filtered(_handler_keys_0111)

CHECKS_01_11_01_23_RECEIVER = [
    MCheck("payment.ap2_merchant_authorization_embedded",
           ["PAY-019", "PAY-021", "PAY-027"], "MUST", f_ap2, p_ap2_embedded,
           ["drop:ap2", "set:ap2={}", "set:ap2={\"merchant_authorization\":\"not..valid!!\"}",
            "corrupt-json", "status:500"],
           needs=("product",), cfg_needs=("ap2",), transport="rest", versions=V_OLD),
    MCheck("payment.ap2_approved_algorithm", ["PAY-026"], "MUST", f_ap2, p_ap2_algorithm,
           ["drop:ap2",
            "set:ap2={\"merchant_authorization\":\"eyJhbGciOiJSUzI1NiIsImtpZCI6ImsxIn0..c2ln\"}",
            "corrupt-json", "status:500"],
           needs=("product",), cfg_needs=("ap2",), transport="rest", versions=V_OLD),
    MCheck("checkout.escalation_continue_url_01era", ["CHK-025", "CHK-038"], "MUST",
           f_escalate, p_escalation_continue_url,
           ["status:402", "status:500", "drop:continue_url",
            "set:continue_url=\"\"", "set:status=\"completed\"", "empty", "corrupt-json"],
           cfg_needs=("payment.escalation_payment",), needs=("product",),
           transport="rest", versions=V_OLD),
    MCheck("checkout.escalation_continue_url_https_01era", ["CHK-028"], "MUST",
           f_escalate, p_escalation_https,
           ["status:402", "drop:continue_url", "set:continue_url=\"http://insecure/3ds\"",
            "set:continue_url=\"/3ds/relative\"", "set:status=\"completed\"", "empty"],
           cfg_needs=("payment.escalation_payment",), needs=("product",),
           transport="rest", versions=V_OLD),
    MCheck("checkout.idempotency_conflict_01era", ["IDM-002"], "MUST",
           f_idem_conflict, p_idem_conflict_409,
           ["status:200", "status:201", "status:410"],
           needs=("product",), transport="rest", versions=V_OLD),
    MCheck("checkout.deterministic_logic_01era", ["CHK-037"], "MUST",
           f_det_replay, p_deterministic,
           ["set:status=\"requires_escalation\"", "set:currency=\"EUR\"",
            "set:totals=[]", "set:continue_url=\"https://spck.dev/x/3ds\"",
            "status:500", "corrupt-json", "empty"],
           needs=("product",), transport="rest", versions=V_OLD),
    MCheck("payment.handlers_context_filtered_0123", ["PAY-012"], "MUST",
           f_handler_filtered_0123, p_handler_filtered_0123,
           ["set:ucp={\"version\":\"2026-01-23\",\"payment_handlers\":"
            "{\"dev.spck.tokenpay\":[{\"id\":\"spck_tokenpay\"}],"
            "\"dev.spck.giftpay\":[{\"id\":\"spck_giftpay\"}]}}",
            "corrupt-json", "empty"],
           cfg_needs=("payment.filtered",), needs=("product",),
           transport="rest", versions=("2026-01-23",)),
    MCheck("payment.handlers_context_filtered_0111", ["PAY-012"], "MUST",
           f_handler_filtered_0111, p_handler_filtered_0111,
           ["set:payment={\"handlers\":[{\"name\":\"dev.spck.tokenpay\"},"
            "{\"name\":\"dev.spck.giftpay\"}]}",
            "corrupt-json", "empty"],
           cfg_needs=("payment.filtered",), needs=("product",),
           transport="rest", versions=("2026-01-11",)),
]
