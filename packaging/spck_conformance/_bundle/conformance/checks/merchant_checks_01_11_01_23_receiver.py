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
from engine import fetch, CLEAN, DEVIATION                    # noqa: E402
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
]
