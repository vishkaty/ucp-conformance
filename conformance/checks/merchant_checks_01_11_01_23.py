#!/usr/bin/env python3
"""
merchant_checks_01_11_01_23.py — OLD-VERSIONS (2026-01-11 / 2026-01-23) behavioral
checks (ROADMAP Phase 3 grind). Every check is version-locked via `versions=`; the
file name carries BOTH version tokens so coverage/matrix.py bounds attribution to
2026-01-11 + 2026-01-23. Register-id texts were verified TEXTUALLY IDENTICAL at both
versions for every dual-scoped citation (single-version rows are locked narrower).

Reference goldens: the controlled fixture in 01-23 AND 01-11 modes (run_suite gates
merchant-ctrl-01-23 / merchant-ctrl-01-11) plus the Flower Shop (2026-01-23) for the
checks whose scenario data it has. Scenario-dependent checks are config-gated
(cfg_needs) so a golden without the scenario skips honestly, never false-deviates.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import sys, pathlib, uuid
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                     # noqa: E402
from merchant_checks import (MCheck, _hdr, profile_resp, idem_conflict_resp, p_409,
                             _create_for_complete, _dvalid, _dsecond)  # noqa: E402
from merchant_checks_01_23 import _payload, _dcfg              # noqa: E402
from tls_check_01_11_01_23 import tls_probe                    # noqa: E402
from engine import Resp                                        # noqa: E402
from verdict_gate import INCONCLUSIVE                          # noqa: E402

V_BOTH = ("2026-01-11", "2026-01-23")
V_11 = ("2026-01-11",)
V_23 = ("2026-01-23",)

# ---- DISC-002 / DISC-003: capability metadata + namespace-origin binding ---------
def _authority(name):
    """The namespace authority host for a reverse-domain capability name
    (overview.md Governance table): {reverse-domain}.{service}.{capability} ->
    reverse the domain labels (all but the last two): dev.ucp.* -> ucp.dev,
    com.example.* -> example.com. None when the name can't carry a domain."""
    labels = name.split(".") if isinstance(name, str) else []
    if len(labels) < 3:
        return None
    return ".".join(reversed(labels[:-2]))

def _origin_ok(url, authority):
    """The spec/schema URL origin MUST match the namespace authority — an https URL
    on exactly the authority host (the binding table shows https://<authority>/...)."""
    if not isinstance(url, str) or not authority:
        return False
    u = urlsplit(url)
    return u.scheme == "https" and u.hostname == authority

def p_cap_meta_01_11(r):
    """DISC-002 @2026-01-11: capabilities is the ARRAY generation; every entry MUST
    carry spec and schema (capability.json $defs/discovery also requires them —
    the fixture's selfcheck.py anchors the whole profile on that oracle def)."""
    caps = (r.json or {}).get("capabilities") if isinstance(r.json, dict) else None
    if not isinstance(caps, list):
        return DEVIATION
    if not caps:
        return CLEAN                       # empty-but-valid: vacuous truth (W2-F10)
    for e in caps:
        if not isinstance(e, dict):
            return DEVIATION
        for f in ("name", "version", "spec", "schema"):
            if not (isinstance(e.get(f), str) and e[f]):
                return DEVIATION
    return CLEAN

def p_cap_meta_01_23(r):
    """DISC-002 @2026-01-23: keyed-object capabilities; every declared entry MUST
    carry spec and schema (prose MUST; the 01-23 profile schema does not enforce it)."""
    caps = (r.json or {}).get("capabilities") if isinstance(r.json, dict) else None
    if not isinstance(caps, dict):
        return DEVIATION
    if not caps:
        return CLEAN                       # empty-but-valid: vacuous truth (W2-F10)
    for entries in caps.values():
        if not isinstance(entries, list) or not entries:
            return DEVIATION
        for e in entries:
            if not isinstance(e, dict):
                return DEVIATION
            for f in ("spec", "schema"):
                if not (isinstance(e.get(f), str) and e[f]):
                    return DEVIATION
    return CLEAN

def p_origin_binding_01_11(r):
    caps = (r.json or {}).get("capabilities") if isinstance(r.json, dict) else None
    if not isinstance(caps, list):
        return DEVIATION
    if not caps:
        return CLEAN                       # empty-but-valid: vacuous truth (W2-F10)
    for e in caps:
        if not isinstance(e, dict):
            return DEVIATION
        auth = _authority(e.get("name"))
        if not auth or not _origin_ok(e.get("spec"), auth) \
           or not _origin_ok(e.get("schema"), auth):
            return DEVIATION
    return CLEAN

def p_origin_binding_01_23(r):
    caps = (r.json or {}).get("capabilities") if isinstance(r.json, dict) else None
    if not isinstance(caps, dict):
        return DEVIATION
    if not caps:
        return CLEAN                       # empty-but-valid: vacuous truth (W2-F10)
    for name, entries in caps.items():
        auth = _authority(name)
        if not auth or not isinstance(entries, list) or not entries:
            return DEVIATION
        for e in entries:
            if not isinstance(e, dict) or not _origin_ok(e.get("spec"), auth) \
               or not _origin_ok(e.get("schema"), auth):
                return DEVIATION
    return CLEAN

# ---- CHK-050: REST bodies are valid JSON (RFC 8259) ------------------------------
def create_resp_json(ctx):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, [(ctx.product_id, 1)]), _hdr())

def p_body_is_json(r):
    return CLEAN if isinstance(r.json, (dict, list)) else DEVIATION

# ---- NEG-020 / NEG-021 + ERR-006: standard verbs & status codes ------------------
def verbs_resp(ctx):
    """POST creates, GET retrieves — the standard-verb mapping in action."""
    r = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
              _payload(ctx, [(ctx.product_id, 1)]), _hdr())
    cid = (r.json or {}).get("id") if isinstance(r.json, dict) else None
    if not cid:
        return r                      # create failed -> predicate grades the failure
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}", "GET", None, _hdr())

def p_get_retrieves(r):
    if r.status != 200 or not isinstance(r.json, dict) or not r.json.get("id"):
        return DEVIATION
    return CLEAN

# IANA-registered client-error codes (RFC 9110 + registered extensions)
_IANA_4XX = {400, 401, 402, 403, 404, 405, 406, 407, 408, 409, 410, 411, 412, 413,
             414, 415, 416, 417, 418, 421, 422, 423, 424, 425, 426, 428, 429, 431, 451}

def nonexistent_get_resp(ctx):
    return fetch(ctx.shopping_endpoint,
                 f"/checkout-sessions/ucp_nonexistent_{uuid.uuid4().hex[:10]}",
                 "GET", None, _hdr())

def p_standard_error_status(r):
    """A request for a session that cannot exist must be answered with a STANDARD
    (IANA-registered) client-error status — not a made-up code and not a 200."""
    return CLEAN if r.status in _IANA_4XX else DEVIATION

# ---- NEG-018 / SEC-004: all UCP communication over HTTPS -------------------------
def https_resp(ctx):
    """Synthetic Resp carrying the TLS probe of the shopping endpoint (no HTTP
    request involved) — same harness as CHK-051, but ANY TLS version satisfies
    this row (the 1.3 minimum is CHK-051's separate requirement)."""
    r = Resp(200, {}, b"{}")
    r.tls = tls_probe(ctx.shopping_endpoint or ctx.base)
    return r

def p_https_service(r):
    t = getattr(r, "tls", None) or {}
    if not t.get("applicable"):
        return INCONCLUSIVE               # plain-HTTP dev golden -> not-tested
    return CLEAN if t.get("handshake_ok") else DEVIATION

# ---- DSC-001: discounts.codes replacement semantics ------------------------------
def dsc001_resp(ctx):
    """Create with the first valid code, then UPDATE submitting only the second:
    the submitted set REPLACES the previous one."""
    first = _dvalid(ctx)
    r = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
              _payload(ctx, [(ctx.product_id, 1)], codes=[first]), _hdr())
    cid = (r.json or {}).get("id") if isinstance(r.json, dict) else None
    if not cid:
        return r
    upd = _payload(ctx, [(ctx.product_id, 1)], codes=[_dsecond(ctx)])
    upd["id"] = cid                       # id is REQUIRED on 01-era updates (CHK-016)
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}", "PUT", upd, _hdr())

def p_codes_replaced(r, ctx):
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    d = r.json.get("discounts")
    codes = d.get("codes") if isinstance(d, dict) else None
    if not isinstance(codes, list):
        return DEVIATION
    up = [c.upper() for c in codes if isinstance(c, str)]
    first, second = (_dvalid(ctx) or "").upper(), (_dsecond(ctx) or "").upper()
    return CLEAN if second in up and first not in up else DEVIATION

# ---- DSC-016 / DSC-017: allocation-sum invariant + positive amounts --------------
def _item_cart_resp(ctx, extra_codes=()):
    it = _dcfg(ctx).get("item") or {}
    codes = [it.get("code")] + [c for c in extra_codes if c]
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, [(it.get("product_id"), int(it.get("quantity", 1)))],
                          codes=codes), _hdr())

def dsc016_resp(ctx):
    return _item_cart_resp(ctx)

def p_allocations_sum(r):
    """Every applied discount carrying allocations satisfies
    sum(allocations[].amount) == applied.amount; the seeded item-discount scenario
    guarantees at least one such entry (never vacuously true)."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    d = r.json.get("discounts")
    ap = d.get("applied") if isinstance(d, dict) else None
    if not isinstance(ap, list):
        return DEVIATION
    with_alloc = [x for x in ap if isinstance(x, dict) and x.get("allocations")]
    if not with_alloc:
        return DEVIATION
    for x in with_alloc:
        try:
            s = sum(a["amount"] for a in x["allocations"])
        except (KeyError, TypeError):
            return DEVIATION
        if not (isinstance(x.get("amount"), int) and x["amount"] > 0 and s == x["amount"]):
            return DEVIATION
    return CLEAN

def dsc017_resp(ctx):
    return _item_cart_resp(ctx, extra_codes=(_dvalid(ctx),))

def p_amounts_positive(r):
    """01-era amount convention: applied amounts are POSITIVE integers; totals
    discount entries and line_items[].discount are positive too (the subtractive
    presentation is display-side; the wire amounts are positive at 01-era)."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    d = r.json.get("discounts")
    ap = d.get("applied") if isinstance(d, dict) else None
    if not isinstance(ap, list) or not ap:
        return DEVIATION
    for x in ap:
        if not (isinstance(x, dict) and isinstance(x.get("amount"), int)
                and x["amount"] > 0):
            return DEVIATION
        for a in x.get("allocations") or []:
            if not (isinstance(a, dict) and isinstance(a.get("amount"), int)
                    and a["amount"] >= 0):
                return DEVIATION
    for t in r.json.get("totals") or []:
        if isinstance(t, dict) and t.get("type") in ("discount", "items_discount"):
            if not (isinstance(t.get("amount"), int) and t["amount"] > 0):
                return DEVIATION
    for li in r.json.get("line_items") or []:
        if isinstance(li, dict) and "discount" in li:
            if not (isinstance(li["discount"], int) and li["discount"] > 0):
                return DEVIATION
    return CLEAN

# ---- ERR-005: requires_* severity => status requires_escalation ------------------
def err005_resp(ctx):
    """Complete with the seeded escalation payment: the merchant surfaces a
    requires_* message, so status MUST be requires_escalation."""
    r = fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
              _payload(ctx, [(ctx.product_id, 1)]), _hdr())
    cid = (r.json or {}).get("id") if isinstance(r.json, dict) else None
    if not cid:
        return r
    esc = (ctx.config.get("payment") or {}).get("escalation_payment")
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete", "POST",
                 esc, _hdr())

def p_requires_escalates(r):
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    msgs = r.json.get("messages") or []
    has_req = any(isinstance(m, dict)
                  and str(m.get("severity", "")).startswith("requires_") for m in msgs)
    if not has_req:                       # seeded scenario MUST surface the message
        return DEVIATION
    return CLEAN if r.json.get("status") == "requires_escalation" else DEVIATION

# ---- NEG-013 / NEG-014 / ERR-007: negotiation-failure error response -------------
def neg013_resp(ctx):
    """Present a platform profile pinned to an unsupported version: the business
    MUST validate the version and answer with the error envelope from the pinned
    spec's example (status requires_escalation + a type:error message)."""
    url = (ctx.config.get("negotiation") or {}).get("unsupported_version_profile_url")
    hdrs = _hdr()
    hdrs["UCP-Agent"] = f'profile="{url}"'
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _payload(ctx, [(ctx.product_id, 1)]), hdrs)

def p_negotiation_error_envelope(r):
    if r.status >= 500 or not isinstance(r.json, dict):
        return DEVIATION                  # crashing is not "returning an error response"
    if r.json.get("status") != "requires_escalation":
        return DEVIATION
    msgs = r.json.get("messages") or []
    return CLEAN if any(isinstance(m, dict) and m.get("type") == "error"
                        for m in msgs) else DEVIATION

# ---- ORD-006 / ORD-007 / ORD-009: order retention + append-only logs -------------
def _complete_to_order(ctx):
    """Create->complete; returns (order_id, ordered_snapshot, last_resp)."""
    r = _create_for_complete(ctx)
    cjson = r.json if isinstance(r.json, dict) else {}
    cid = cjson.get("id")
    if not cid:
        return None, None, r
    snapshot = [(li.get("id"), (li.get("item") or {}).get("id"), li.get("quantity"))
                for li in cjson.get("line_items") or []]
    comp = fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete", "POST",
                 ctx.config.get("complete_payment"), _hdr())
    oid = ((comp.json or {}).get("order") or {}).get("id") \
        if isinstance(comp.json, dict) else None
    return oid, snapshot, comp

def ord006_resp(ctx):
    oid, snapshot, last = _complete_to_order(ctx)
    ctx._ord006_expected = snapshot       # stash on ctx: Resp.clone() drops attrs
    if not oid:
        return last
    return fetch(ctx.shopping_endpoint, f"/orders/{oid}", "GET", None, _hdr())

def p_line_items_retained(r, ctx):
    """The order's line_items reflect exactly what was purchased at checkout:
    same ids, same item ids, same quantities (order quantity is the {total,
    fulfilled} object at 01-era; total carries the purchased count)."""
    expected = getattr(ctx, "_ord006_expected", None)
    if r.status != 200 or not isinstance(r.json, dict) or not expected:
        return DEVIATION
    lis = r.json.get("line_items")
    if not isinstance(lis, list) or len(lis) != len(expected):
        return DEVIATION
    by_id = {li.get("id"): li for li in lis if isinstance(li, dict)}
    for lid, iid, qty in expected:
        li = by_id.get(lid)
        if not li or (li.get("item") or {}).get("id") != iid:
            return DEVIATION
        q = li.get("quantity")
        total = q.get("total") if isinstance(q, dict) else q
        if total != qty:
            return DEVIATION
    return CLEAN

def _testing_hook_resp(ctx, action):
    oid, _, last = _complete_to_order(ctx)
    if not oid:
        return last
    got = fetch(ctx.shopping_endpoint, f"/orders/{oid}", "GET", None, _hdr())
    lis = (got.json or {}).get("line_items") or []
    lid = lis[0].get("id") if lis and isinstance(lis[0], dict) else None
    return fetch(ctx.shopping_endpoint, f"/testing/orders/{oid}/{action}", "POST",
                 {"line_item_id": lid, "quantity": 1}, _hdr())

def ord007_resp(ctx):
    return _testing_hook_resp(ctx, "adjust")

def p_adjustment_log_fields(r):
    """Each adjustment in the append-only log carries id, type, occurred_at and
    status (status within the pinned enum); 01-era quantities/amounts are UNSIGNED."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    adjs = r.json.get("adjustments")
    if not isinstance(adjs, list) or not adjs:
        return DEVIATION
    for a in adjs:
        if not isinstance(a, dict):
            return DEVIATION
        for f in ("id", "type", "occurred_at"):
            if not (isinstance(a.get(f), str) and a[f]):
                return DEVIATION
        if a.get("status") not in ("pending", "completed", "failed"):
            return DEVIATION
        for li in a.get("line_items") or []:
            if not (isinstance(li, dict) and isinstance(li.get("quantity"), int)
                    and li["quantity"] >= 1):
                return DEVIATION
        if "amount" in a and not (isinstance(a["amount"], int) and a["amount"] >= 1):
            return DEVIATION
    return CLEAN

def ord009_resp(ctx):
    return _testing_hook_resp(ctx, "fulfill")

def p_fulfillment_event_fields(r):
    """Each fulfillment event (append-only shipment log) carries id, occurred_at,
    type and line_items (each with id + quantity >= 1)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    evs = (r.json.get("fulfillment") or {}).get("events") \
        if isinstance(r.json.get("fulfillment"), dict) else None
    if not isinstance(evs, list) or not evs:
        return DEVIATION
    for e in evs:
        if not isinstance(e, dict):
            return DEVIATION
        for f in ("id", "occurred_at", "type"):
            if not (isinstance(e.get(f), str) and e[f]):
                return DEVIATION
        lis = e.get("line_items")
        if not isinstance(lis, list) or not lis:
            return DEVIATION
        for li in lis:
            if not (isinstance(li, dict) and li.get("id")
                    and isinstance(li.get("quantity"), int) and li["quantity"] >= 1):
                return DEVIATION
    return CLEAN

# ---- DSC-019: buyer consent boolean states persisted -----------------------------
def dsc019_resp(ctx):
    p = _payload(ctx, [(ctx.product_id, 1)])
    p["buyer"] = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
                  "consent": {"marketing": True, "analytics": False}}
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr())

def p_consent_persisted(r):
    """The submitted boolean consent states are persisted at checkout.buyer.consent
    VERBATIM (exact booleans — a stringified or flipped state is a deviation).
    Extra consent keys a merchant echoes are outside this row (typing of every
    present state is DSC-020's schema check)."""
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    consent = ((r.json.get("buyer") or {}).get("consent")
               if isinstance(r.json.get("buyer"), dict) else None)
    if not isinstance(consent, dict):
        return DEVIATION
    return CLEAN if consent.get("marketing") is True \
        and consent.get("analytics") is False else DEVIATION

CHECKS_01_11_01_23 = [
    # --- discovery: capability metadata + namespace-origin binding ---
    MCheck("discovery.capability_spec_schema_01_11", ["DISC-002"], "MUST",
           profile_resp, p_cap_meta_01_11,
           ["drop:capabilities.0.spec", "drop:capabilities.0.schema",
            'set:capabilities.0.spec=""', "drop:capabilities", "corrupt-json"],
           versions=V_11),
    MCheck("discovery.capability_spec_schema_01_23", ["DISC-002"], "MUST",
           profile_resp, p_cap_meta_01_23,
           ['set:capabilities={"dev.ucp.shopping.checkout":[{"version":"2026-01-23"}]}',
            "drop:capabilities", "corrupt-json"],
           versions=V_23),
    MCheck("discovery.capability_origin_binding_01_11", ["DISC-003"], "MUST",
           profile_resp, p_origin_binding_01_11,
           ['set:capabilities.0.schema="https://evil.example/x.json"',
            'set:capabilities.0.spec="http://ucp.dev/spec"',   # https required
            "drop:capabilities", "corrupt-json"],
           versions=V_11),
    MCheck("discovery.capability_origin_binding_01_23", ["DISC-003"], "MUST",
           profile_resp, p_origin_binding_01_23,
           ['set:capabilities={"dev.ucp.shopping.checkout":[{"version":"2026-01-23",'
            '"spec":"https://evil.example/spec","schema":"https://evil.example/x.json"}]}',
            "drop:capabilities", "corrupt-json"],
           versions=V_23),
    # --- transport/protocol hygiene ---
    MCheck("checkout.body_valid_json_01era", ["CHK-050"], "MUST",
           create_resp_json, p_body_is_json,
           ["corrupt-json", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           transport="rest", versions=V_BOTH),
    MCheck("checkout.idem_conflict_409_01era", ["IDM-005"], "MUST",
           idem_conflict_resp, p_409,
           ["status:200", "status:201", "status:400"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           transport="rest", versions=V_BOTH),
    MCheck("http.standard_verbs", ["NEG-020"], "MUST",
           verbs_resp, p_get_retrieves,
           ["status:500", "drop:id", "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           transport="rest", versions=V_BOTH),
    MCheck("http.standard_error_status", ["NEG-021", "ERR-006"], "MUST",
           nonexistent_get_resp, p_standard_error_status,
           ["status:599", "status:299", "status:200"],
           capability="dev.ucp.shopping.checkout", transport="rest", versions=V_BOTH),
    # Transport-layer: kill proof lives in the dedicated reference gate
    # (selfcheck/validate_tls_check.py — https golden CLEAN, https-with-no-TLS
    # mutant DEVIATION, plain-http dev golden INCONCLUSIVE), like CHK-051.
    MCheck("transport.https_all_communication", ["NEG-018", "SEC-004"], "MUST",
           https_resp, p_https_service,
           [],
           capability="dev.ucp.shopping.checkout", versions=V_BOTH),
    # --- discounts ---
    MCheck("discount.codes_replacement", ["DSC-001"], "MUST",
           dsc001_resp, p_codes_replaced,
           ["set:discounts.codes=[$DVALID,$DSECOND]",      # previous code kept -> not replaced
            "set:discounts.codes=[$DVALID]",               # submitted set ignored
            "drop:discounts", "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount.valid_code", "discount.second_valid_code"),
           transport="rest", versions=V_BOTH),
    MCheck("discount.allocations_sum_matches", ["DSC-016"], "MUST",
           dsc016_resp, p_allocations_sum,
           ["set:discounts.applied.0.allocations.0.amount=1",   # sum diverges
            "drop:discounts.applied.0.allocations.0",           # allocations emptied
            "set:discounts.applied.0.amount=999999",            # amount diverges
            "drop:discounts", "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.discount",
           cfg_needs=("discount.item",), transport="rest", versions=V_BOTH),
    MCheck("discount.amounts_positive_integers", ["DSC-017"], "MUST",
           dsc017_resp, p_amounts_positive,
           ["set:discounts.applied.0.amount=-5",                # negative amount
            "set:discounts.applied.0.amount=4.5",               # non-integer
            "set:line_items.0.discount=-1",                     # negative line discount
            'set:totals=[{"type":"subtotal","amount":2400},'
            '{"type":"items_discount","amount":-480},'
            '{"type":"discount","amount":-240},'
            '{"type":"total","amount":1680}]',                  # negative totals entries
            "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.discount",
           cfg_needs=("discount.item", "discount.valid_code"),
           transport="rest", versions=V_BOTH),
    # --- errors / negotiation ---
    MCheck("errors.requires_severity_escalates", ["ERR-005"], "MUST",
           err005_resp, p_requires_escalates,
           ['set:status="completed"',                 # requires_* message but no escalation
            "drop:messages",                          # scenario message suppressed
            'set:messages.0.severity="recoverable"',  # severity downgraded
            "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("payment.escalation_payment",), transport="rest", versions=V_BOTH),
    MCheck("negotiation.failure_error_envelope", ["NEG-013", "NEG-014", "ERR-007"],
           "MUST", neg013_resp, p_negotiation_error_envelope,
           ['set:status="completed"', "drop:messages", "set:messages=[]",
            'set:messages.0.type="warning"', "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("negotiation.unsupported_version_profile_url",),
           transport="rest", versions=V_BOTH),
    # --- order ---
    MCheck("order.line_items_retained_01era", ["ORD-006"], "MUST",
           ord006_resp, p_line_items_retained,
           ["drop:line_items.0", "set:line_items=[]",
            'set:line_items.0.quantity={"total":0,"fulfilled":0}',
            "corrupt-json", "status:404"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=("complete_payment",), transport="rest", versions=V_BOTH),
    MCheck("order.adjustment_log_fields_01era", ["ORD-007"], "MUST",
           ord007_resp, p_adjustment_log_fields,
           ["drop:adjustments.0.id", "drop:adjustments.0.occurred_at",
            'set:adjustments.0.status="reversed"', "set:adjustments=[]",
            "set:adjustments.0.line_items.0.quantity=-1",   # 01-era: unsigned (min 1)
            "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=("order.simulate_adjustment", "complete_payment"),
           transport="rest", versions=V_BOTH),
    MCheck("order.fulfillment_event_fields", ["ORD-009"], "MUST",
           ord009_resp, p_fulfillment_event_fields,
           ["drop:fulfillment.events.0.id", "drop:fulfillment.events.0.line_items",
            "set:fulfillment.events=[]",
            "set:fulfillment.events.0.line_items.0.quantity=0",
            "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=("order.simulate_fulfillment", "complete_payment"),
           transport="rest", versions=V_BOTH),
    # --- buyer consent ---
    MCheck("consent.boolean_states_persisted", ["DSC-019"], "MUST",
           dsc019_resp, p_consent_persisted,
           ["set:buyer.consent.marketing=false",       # flipped, not persisted verbatim
            'set:buyer.consent.marketing="true"',      # stringified, not boolean
            "drop:buyer.consent", "drop:buyer", "corrupt-json", "status:500"],
           capability="dev.ucp.shopping.buyer_consent", needs=("product",),
           transport="rest", versions=V_BOTH),
]
