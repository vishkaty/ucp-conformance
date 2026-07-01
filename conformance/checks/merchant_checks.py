#!/usr/bin/env python3
"""
merchant_checks.py — capability-adaptive, context-driven checks for ANY UCP server.

Unlike the reference-server suite (which hard-codes Flower Shop seeded data to prove
the engine), these checks take a MerchantCtx and adapt to the server:
  * `capability=None` checks are core (run on every server);
  * a check with a `capability` runs only if the server DECLARES it, else not-applicable;
  * a check listing `needs` (config keys / a product) runs only if that data is
    available (auto-discovered or from --config), else not-tested.

Each check is still mutation kill-rate-validated (clean-pass + catches its defects).
The verdict denominator is the APPLICABLE testable MUSTs, so a lean server scores
honestly. Data-dependent negative cases (out-of-stock, failing token, discount codes)
come from the optional merchant config.
"""
import sys, uuid, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, fetch, mutate, CLEAN, DEVIATION   # noqa: E402
from verdict_gate import CheckResult, INCONCLUSIVE          # noqa: E402

STATUS_ENUM = {"incomplete", "requires_escalation", "ready_for_complete",
               "complete_in_progress", "completed", "canceled"}

class MCheck:
    def __init__(self, cid, req_ids, keyword, fetch_fn, predicate, mutations,
                 capability=None, needs=(), cfg_needs=(), transport=None):
        self.id, self.req_ids, self.keyword = cid, req_ids, keyword
        self.fetch_fn, self.predicate, self.mutations = fetch_fn, predicate, mutations
        self.capability, self.needs = capability, tuple(needs)
        # cfg_needs = merchant-config keys that must be present to run this check
        # (data-dependent negatives: out-of-stock id, failing payment, etc.)
        self.cfg_needs = tuple(cfg_needs)
        # transport="rest" -> requires a REST shopping transport; if the server offers
        # only mcp/embedded this check is not-applicable (this runner is REST-scoped).
        self.transport = transport

# ---- request helpers (parameterized by the merchant context) ----------------
def _hdr(idem=None):
    return {"UCP-Agent": 'profile="https://spck.dev/agent"', "request-signature": "test",
            "idempotency-key": idem or str(uuid.uuid4()), "request-id": str(uuid.uuid4()),
            "Content-Type": "application/json"}

def _create_payload(ctx, with_fulfillment=False):
    p = {"id": str(uuid.uuid4()), "currency": ctx.config.get("currency", "USD"),
         "line_items": [{"id": "li_1", "quantity": 1,
                         "item": {"id": ctx.product_id, "price": 1000}, "totals": []}],
         "payment": {"instruments": [], "handlers": ctx.config.get("payment_handlers", [])},
         "status": "incomplete", "ucp": {"version": ctx.version}, "totals": [], "links": []}
    if with_fulfillment:
        p["fulfillment"] = {"methods": [{"id": "m1", "type": "shipping",
            "destinations": [{"id": "d1", "address_country": "US"}], "line_item_ids": ["li_1"],
            "selected_destination_id": "d1",
            "groups": [{"id": "g1", "line_item_ids": ["li_1"], "selected_option_id": "std"}]}]}
    return p

def profile_resp(ctx):
    return Resp(200, {"Content-Type": "application/json"},
                json.dumps(ctx.profile.get("ucp", ctx.profile)).encode())

def create_resp(ctx):
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _create_payload(ctx), _hdr())

def create_resp_ful(ctx):
    """Create while REQUESTING fulfillment, so a fulfillment server returns methods."""
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST",
                 _create_payload(ctx, with_fulfillment=True), _hdr())

def retrieve_resp(ctx):
    """Create, then GET the session back."""
    r = create_resp(ctx)
    cid = (r.json or {}).get("id")
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}", "GET", None, _hdr())

def nonexistent_resp(ctx):
    """Create referencing a product that cannot exist — MUST be rejected (4xx)."""
    p = _create_payload(ctx)
    p["line_items"][0]["item"]["id"] = "ucp_nonexistent_" + uuid.uuid4().hex[:10]
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr())

def no_agent_resp(ctx):
    """Otherwise-valid create with the mandatory UCP-Agent header removed — MUST 4xx."""
    h = _hdr(); h.pop("UCP-Agent", None)
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", _create_payload(ctx), h)

def idem_conflict_resp(ctx):
    """Same idempotency-key, DIFFERENT body -> MUST 409 Conflict."""
    k = str(uuid.uuid4())
    fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", _create_payload(ctx), _hdr(k))
    p2 = _create_payload(ctx)
    p2["line_items"][0]["quantity"] = 2
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p2, _hdr(k))

# ---- data-dependent flows (config-gated) ------------------------------------
def _create_for_complete(ctx):
    """Create a session driven to a completable state, selecting the config option."""
    p = _create_payload(ctx, with_fulfillment=True)
    opt = ctx.config.get("fulfillment_option_id")
    if opt:
        p["fulfillment"]["methods"][0]["groups"][0]["selected_option_id"] = opt
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr())

def complete_resp(ctx):
    """Full happy-path: create -> complete with the merchant's success payment."""
    cid = (_create_for_complete(ctx).json or {}).get("id")
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete", "POST",
                 ctx.config.get("complete_payment"), _hdr())

def payment_fail_resp(ctx):
    """Complete with the merchant's known-failing payment -> MUST be rejected (402)."""
    cid = (_create_for_complete(ctx).json or {}).get("id")
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}/complete", "POST",
                 ctx.config.get("fail_payment"), _hdr())

def out_of_stock_resp(ctx):
    """Create referencing the merchant's known out-of-stock product -> MUST 4xx."""
    p = _create_payload(ctx)
    p["line_items"][0]["item"]["id"] = ctx.config["out_of_stock_id"]
    return fetch(ctx.shopping_endpoint, "/checkout-sessions", "POST", p, _hdr())

# ---- discount flows (capability dev.ucp.shopping.discount, config-gated) ------
def _apply_codes(ctx, codes):
    """Create a fresh checkout, then PUT discounts.codes; return the update Resp."""
    c = (create_resp(ctx).json or {})
    cid = c.get("id")
    li = (c.get("line_items") or [{}])[0]
    body = {"id": cid, "currency": c.get("currency", ctx.config.get("currency", "USD")),
            "line_items": [{"id": li.get("id"),
                            "item": {"id": (li.get("item") or {}).get("id")}, "quantity": 1}],
            "payment": {"instruments": (c.get("payment") or {}).get("instruments", [])},
            "discounts": {"codes": codes}}
    return fetch(ctx.shopping_endpoint, f"/checkout-sessions/{cid}", "PUT", body, _hdr())

def _dvalid(ctx):   return (ctx.config.get("discount") or {}).get("valid_code")
def _dinvalid(ctx): return (ctx.config.get("discount") or {}).get("invalid_code")

def disc_single_resp(ctx):  return _apply_codes(ctx, [_dvalid(ctx)])
def disc_reject_resp(ctx):  return _apply_codes(ctx, [_dvalid(ctx), _dinvalid(ctx)])
def disc_unknown_resp(ctx): return _apply_codes(ctx, [_dinvalid(ctx)])

# ---- catalog flows (capability dev.ucp.shopping.catalog.*, product from config) --
def catalog_search_resp(ctx):
    return fetch(ctx.shopping_endpoint, "/catalog/search", "POST", {"query": "*"}, _hdr())

def catalog_lookup_resp(ctx):
    return fetch(ctx.shopping_endpoint, "/catalog/lookup", "POST",
                 {"ids": [ctx.product_id]}, _hdr())

def p_catalog_search(r):
    """CAT-012: search returns a products array; each product is well-formed (id/title/variants)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    prods = r.json.get("products")
    if not isinstance(prods, list):
        return DEVIATION
    for p in prods:
        if not (isinstance(p, dict) and p.get("id") and p.get("title")
                and isinstance(p.get("variants"), list) and p["variants"]):
            return DEVIATION
    return CLEAN

def p_catalog_lookup_inputs(r):
    """CAT-017/018: lookup variants MUST carry a non-empty inputs correlation array."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    prods = r.json.get("products")
    if not isinstance(prods, list) or not prods:
        return DEVIATION
    for p in prods:
        for v in (p.get("variants") or []):
            inp = v.get("inputs")
            if not isinstance(inp, list) or not inp or not all(i.get("id") for i in inp):
                return DEVIATION
    return CLEAN

def _discounts(r):  return (r.json or {}).get("discounts") if isinstance(r.json, dict) else None
def _applied(r):
    d = _discounts(r); return d.get("applied") if isinstance(d, dict) else None
def _has_discount_total(r):
    tot = (r.json or {}).get("totals") if isinstance(r.json, dict) else None
    return isinstance(tot, list) and any(
        isinstance(t, dict) and t.get("type") in ("discount", "items_discount") for t in tot)

# ---- predicates -------------------------------------------------------------
def p_version(r):
    import re
    v = (r.json or {}).get("version")
    return CLEAN if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else DEVIATION

def p_rest_endpoint(r):
    svc = ((r.json or {}).get("services") or {}).get("dev.ucp.shopping") or []
    rest = next((s for s in svc if isinstance(s, dict) and s.get("transport") == "rest"), None)
    return CLEAN if rest and rest.get("endpoint") else DEVIATION

def p_profile_schema(r, ctx):
    """DISC-000: the discovered profile MUST validate against the official ucp.json
    business_schema (via the ucp-schema oracle). Catches ALL profile-structure
    deviations (capabilities/services shape, required fields) in one spec-anchored
    check. Returns INCONCLUSIVE if the oracle isn't available (caller -> not-tested)."""
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "selfcheck"))
    try:
        from schema_oracle import validate_profile, OracleUnavailable
    except Exception:
        return INCONCLUSIVE
    try:
        ok, _ = validate_profile(r.json, version=ctx.version or "2026-01-23", role="business")
    except Exception:
        # OracleUnavailable OR any oracle-wiring glitch -> inconclusive, NEVER a false
        # deviation. Only a clean "oracle ran and rejected" yields a deviation.
        return INCONCLUSIVE
    return CLEAN if ok else DEVIATION

def p_reverse_domain(r):
    caps = (r.json or {}).get("capabilities")
    # DISC-001: capabilities MUST be a keyed object whose names are reverse-domain form.
    # A list/array (or missing) is non-conformant -> deviation (never iterate it blindly).
    if not isinstance(caps, dict) or not caps:
        return DEVIATION
    import re
    return CLEAN if all(re.match(r"^[a-z0-9.]+\.[a-z0-9_]+", k) for k in caps) else DEVIATION

def p_create_ok(r):
    if r.status not in (200, 201) or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("id") and r.json.get("status") in STATUS_ENUM else DEVIATION

def p_fulfillment_shape(r):
    try:
        m = r.json["fulfillment"]["methods"][0]
    except (KeyError, IndexError, TypeError):
        return DEVIATION
    return CLEAN if m.get("id") and m.get("type") in ("shipping", "pickup") else DEVIATION

def p_get_ok(r):
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("id") and r.json.get("status") in STATUS_ENUM else DEVIATION

def p_4xx(r):
    return CLEAN if 400 <= r.status < 500 else DEVIATION

def p_409(r):
    return CLEAN if r.status == 409 else DEVIATION

def p_completed(r):
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("status") == "completed" and r.json.get("order") else DEVIATION

def p_402(r):
    return CLEAN if r.status == 402 else DEVIATION

def _cred_tokens(payment):
    """All raw credential token strings inside a payment body (to check they aren't echoed)."""
    toks = []
    for inst in ((payment or {}).get("payment", {}) or {}).get("instruments", []) or []:
        t = (inst.get("credential") or {}).get("token")
        if isinstance(t, str) and t:
            toks.append(t)
    return toks

def p_no_echo(r, ctx):
    """PAY-009: a successful completion MUST NOT echo the raw payment credential back."""
    if not isinstance(r.json, dict) or r.json.get("status") != "completed":
        return DEVIATION
    body = json.dumps(r.json)
    toks = _cred_tokens(ctx.config.get("complete_payment"))
    return DEVIATION if any(t in body for t in toks) else CLEAN

def p_disc_single(r, ctx):
    """DSC-004/011: the valid code is applied (code + amount>0) and surfaced as a total."""
    if r.status != 200:
        return DEVIATION
    ap = _applied(r)
    if not isinstance(ap, list) or not ap:
        return DEVIATION
    first = ap[0]
    if not (isinstance(first, dict) and first.get("code") == _dvalid(ctx)
            and isinstance(first.get("amount"), int) and first["amount"] > 0):
        return DEVIATION
    return CLEAN if _has_discount_total(r) else DEVIATION

def p_disc_reject_one(r, ctx):
    """DSC-006/007: accept-one-reject-one — valid applied; invalid echoed in codes, not applied."""
    if r.status != 200:
        return DEVIATION
    d = _discounts(r)
    if not isinstance(d, dict):
        return DEVIATION
    ap, codes = d.get("applied"), d.get("codes")
    if not isinstance(ap, list) or not isinstance(codes, list):
        return DEVIATION
    applied_codes = {x.get("code") for x in ap if isinstance(x, dict)}
    if _dvalid(ctx) not in applied_codes:        return DEVIATION   # valid one applied
    if _dinvalid(ctx) in applied_codes:          return DEVIATION   # rejected NOT applied
    if _dinvalid(ctx) not in codes:              return DEVIATION   # rejected echoed back
    return CLEAN

def p_disc_unknown(r, ctx):
    """DSC-007: an unknown-only code is rejected — echoed in codes, nothing applied, no total."""
    if r.status != 200:
        return DEVIATION
    d = _discounts(r)
    if not isinstance(d, dict):
        return DEVIATION
    if d.get("applied"):            return DEVIATION   # bogus code wrongly applied
    if _has_discount_total(r):      return DEVIATION
    codes = d.get("codes")
    return CLEAN if isinstance(codes, list) and _dinvalid(ctx) in codes else DEVIATION

# ---- the merchant-agnostic check set (2026-01-23 / 2026-04-08 shared core) ---
CHECKS = [
    MCheck("discovery.version", ["DISC-013"], "MUST", profile_resp, p_version,
           ["drop:version", "corrupt-json", "empty"]),
    MCheck("discovery.rest_endpoint", ["DISC-007"], "MUST", profile_resp, p_rest_endpoint,
           ["drop:services", "set:services={}", "corrupt-json"], transport="rest"),
    MCheck("discovery.reverse_domain_names", ["DISC-001"], "MUST", profile_resp, p_reverse_domain,
           ["drop:capabilities", "set:capabilities={}", "corrupt-json"]),
    MCheck("discovery.profile_schema", ["DISC-000"], "MUST", profile_resp, p_profile_schema,
           ["drop:version", "set:capabilities=[]", "set:services=[]", "corrupt-json"]),
    MCheck("checkout.create_valid", ["CHK-001"], "MUST", create_resp, p_create_ok,
           ["status:500", "drop:id", "set:status=\"bogus\"", "empty"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="rest"),
    MCheck("checkout.retrieve", ["CHK-002"], "MUST", retrieve_resp, p_get_ok,
           ["status:404", "drop:id", "drop:status", "empty", "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="rest"),
    MCheck("validation.requires_ucp_agent", ["CHK-052"], "MUST", no_agent_resp, p_4xx,
           ["status:200", "status:201"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="rest"),
    MCheck("validation.nonexistent_product", ["VAL-003"], "MUST", nonexistent_resp, p_4xx,
           ["status:200", "status:201"],
           capability="dev.ucp.shopping.checkout", transport="rest"),
    MCheck("idempotency.conflict_409", ["IDM-004"], "MUST", idem_conflict_resp, p_409,
           ["status:200", "status:201"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="rest"),
    MCheck("fulfillment.method_shape", ["FUL-003"], "MUST", create_resp_ful, p_fulfillment_shape,
           ["drop:fulfillment", "drop:fulfillment.methods.0.type", "corrupt-json"],
           capability="dev.ucp.shopping.fulfillment", needs=("product",), transport="rest"),
    # --- data-dependent (config-gated) — merchant supplies the concrete inputs ---
    MCheck("checkout.complete_order", ["CHK-004", "CHK-008"], "MUST", complete_resp, p_completed,
           ["status:500", "set:status=\"incomplete\"", "drop:order", "drop:status"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=("complete_payment",), transport="rest"),
    MCheck("payment.no_credential_echo", ["PAY-009"], "MUST NOT", complete_resp, p_no_echo,
           ["set:order.leak=$CRED", "set:status=\"incomplete\"", "drop:status"],
           needs=("product",), cfg_needs=("complete_payment",), transport="rest"),
    MCheck("validation.payment_failure", ["VAL-004"], "MUST", payment_fail_resp, p_402,
           ["status:200", "status:201"], needs=("product",),
           cfg_needs=("fail_payment",), transport="rest"),
    MCheck("validation.out_of_stock", ["VAL-001"], "MUST", out_of_stock_resp, p_4xx,
           ["status:200", "status:201"], cfg_needs=("out_of_stock_id",), transport="rest"),
    # --- discount (capability-gated + config-gated on discount codes) ---
    MCheck("discount.single_applied", ["DSC-004", "DSC-011"], "MUST", disc_single_resp, p_disc_single,
           ["status:500", "drop:discounts", "drop:discounts.applied",
            "drop:discounts.applied.0.code", "set:totals=[]",
            "set:discounts={\"applied\":[]}", "corrupt-json", "empty"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount",), transport="rest"),
    MCheck("discount.accept_one_reject_one", ["DSC-006", "DSC-007"], "MUST", disc_reject_resp,
           p_disc_reject_one,
           ["status:500", "drop:discounts", "drop:discounts.applied", "drop:discounts.codes",
            "set:discounts={\"codes\":[$DVALID,$DINVALID],\"applied\":[{\"code\":$DVALID},{\"code\":$DINVALID}]}",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount",), transport="rest"),
    # --- catalog (capability-gated; product from config/auto-discovery) ---
    MCheck("catalog.search_shape", ["CAT-012"], "MUST", catalog_search_resp, p_catalog_search,
           ["status:500", "drop:products", "set:products=\"x\"",
            "set:products=[{\"id\":\"p\"}]", "corrupt-json"],
           capability="dev.ucp.shopping.catalog.search", transport="rest"),
    MCheck("catalog.lookup_inputs", ["CAT-017", "CAT-018"], "MUST", catalog_lookup_resp,
           p_catalog_lookup_inputs,
           ["status:500", "drop:products", "set:products=[]",
            "drop:products.0.variants.0.inputs", "set:products.0.variants.0.inputs=[]",
            "corrupt-json"],
           capability="dev.ucp.shopping.catalog.lookup", needs=("product",), transport="rest"),
    MCheck("discount.unknown_code_rejected", ["DSC-007"], "MUST", disc_unknown_resp, p_disc_unknown,
           ["status:500", "drop:discounts", "drop:discounts.codes",
            "set:discounts={\"codes\":[$DINVALID],\"applied\":[{\"code\":$DINVALID,\"amount\":100}]}",
            "corrupt-json", "empty"],
           capability="dev.ucp.shopping.discount", needs=("product",),
           cfg_needs=("discount",), transport="rest"),
]

# ---- runner: capability-gated, config-gated, kill-rate-validated -------------
import inspect as _inspect

def _expand_mut(m, ctx):
    """Expand config-specific placeholders in a mutation to concrete JSON values, so a
    generic check can inject a concrete defect at kill-rate time.
      $CRED     -> the merchant's payment credential token (no-echo injection)
      $DVALID   -> a valid discount code
      $DINVALID -> an invalid/unknown discount code
    """
    if "$CRED" in m:
        toks = _cred_tokens(ctx.config.get("complete_payment"))
        m = m.replace("$CRED", json.dumps(toks[0]) if toks else '"__cred__"')
    if "$DVALID" in m:
        m = m.replace("$DVALID", json.dumps(_dvalid(ctx) or "__v__"))
    if "$DINVALID" in m:
        m = m.replace("$DINVALID", json.dumps(_dinvalid(ctx) or "__x__"))
    return m

def _pred(chk, resp, ctx):
    """Call a predicate as p(resp) or p(resp, ctx) — ctx-aware predicates read config."""
    fn = chk.predicate
    try:
        n = len(_inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        n = 1
    return fn(resp, ctx) if n >= 2 else fn(resp)

def run_merchant_checks(ctx, checks=CHECKS):
    results, detail = [], []
    for chk in checks:
        if chk.transport == "rest" and not getattr(ctx, "has_rest", True):
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, "not-tested"))  # not-applicable
            detail.append((chk, {"status": "not-applicable (no REST transport)",
                                  "kill_safe": None})); continue
        if chk.capability and chk.capability not in ctx.capabilities:
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, "not-tested"))  # not-applicable
            detail.append((chk, {"status": "not-applicable", "kill_safe": None})); continue
        if "product" in chk.needs and not ctx.product_id:
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, "not-tested"))
            detail.append((chk, {"status": "not-tested (no product)", "kill_safe": None})); continue
        missing = [k for k in chk.cfg_needs if not ctx.config.get(k)]
        if missing:
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, "not-tested"))
            detail.append((chk, {"status": f"not-tested (needs config: {','.join(missing)})",
                                  "kill_safe": None})); continue
        try:
            golden = chk.fetch_fn(ctx)
        except Exception as e:
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, INCONCLUSIVE))
            detail.append((chk, {"status": f"error:{e}", "kill_safe": False})); continue
        clean = _pred(chk, golden, ctx)
        if clean == INCONCLUSIVE:            # e.g. schema oracle unavailable -> honest skip
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, "not-tested"))
            detail.append((chk, {"status": "not-tested (oracle unavailable)",
                                  "kill_safe": None})); continue
        muts = [_expand_mut(m, ctx) for m in chk.mutations]
        survivors = [m for m in muts if _pred(chk, mutate(golden, m), ctx) != DEVIATION]
        kill_safe = (clean == CLEAN and not survivors)
        status = clean if kill_safe else (clean if clean == DEVIATION else INCONCLUSIVE)
        for rid in chk.req_ids:
            results.append(CheckResult(rid, chk.keyword, status, kill_safe))
        detail.append((chk, {"status": clean,
                             "kills": f"{len(chk.mutations)-len(survivors)}/{len(chk.mutations)}",
                             "kill_safe": kill_safe, "survivors": survivors}))
    return results, detail
