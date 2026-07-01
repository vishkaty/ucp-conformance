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
                 capability=None, needs=()):
        self.id, self.req_ids, self.keyword = cid, req_ids, keyword
        self.fetch_fn, self.predicate, self.mutations = fetch_fn, predicate, mutations
        self.capability, self.needs = capability, tuple(needs)

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

# ---- predicates -------------------------------------------------------------
def p_version(r):
    import re
    v = (r.json or {}).get("version")
    return CLEAN if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else DEVIATION

def p_rest_endpoint(r):
    svc = ((r.json or {}).get("services") or {}).get("dev.ucp.shopping") or []
    rest = next((s for s in svc if isinstance(s, dict) and s.get("transport") == "rest"), None)
    return CLEAN if rest and rest.get("endpoint") else DEVIATION

def p_reverse_domain(r):
    caps = (r.json or {}).get("capabilities") or {}
    import re
    ok = caps and all(re.match(r"^[a-z0-9.]+\.[a-z0-9_]+", k) for k in caps)
    return CLEAN if ok else DEVIATION

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

# ---- the merchant-agnostic check set (2026-01-23 / 2026-04-08 shared core) ---
CHECKS = [
    MCheck("discovery.version", ["DISC-013"], "MUST", profile_resp, p_version,
           ["drop:version", "corrupt-json", "empty"]),
    MCheck("discovery.rest_endpoint", ["DISC-007"], "MUST", profile_resp, p_rest_endpoint,
           ["drop:services", "set:services={}", "corrupt-json"]),
    MCheck("discovery.reverse_domain_names", ["DISC-001"], "MUST", profile_resp, p_reverse_domain,
           ["drop:capabilities", "set:capabilities={}", "corrupt-json"]),
    MCheck("checkout.create_valid", ["CHK-001"], "MUST", create_resp, p_create_ok,
           ["status:500", "drop:id", "set:status=\"bogus\"", "empty"], needs=("product",)),
    MCheck("fulfillment.method_shape", ["FUL-003"], "MUST", create_resp_ful, p_fulfillment_shape,
           ["drop:fulfillment", "drop:fulfillment.methods.0.type", "corrupt-json"],
           capability="dev.ucp.shopping.fulfillment", needs=("product",)),
]

# ---- runner: capability-gated, config-gated, kill-rate-validated -------------
def run_merchant_checks(ctx, checks=CHECKS):
    results, detail = [], []
    for chk in checks:
        if chk.capability and chk.capability not in ctx.capabilities:
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, "not-tested"))  # not-applicable
            detail.append((chk, {"status": "not-applicable", "kill_safe": None})); continue
        if "product" in chk.needs and not ctx.product_id:
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, "not-tested"))
            detail.append((chk, {"status": "not-tested (no product)", "kill_safe": None})); continue
        try:
            golden = chk.fetch_fn(ctx)
        except Exception as e:
            for rid in chk.req_ids:
                results.append(CheckResult(rid, chk.keyword, INCONCLUSIVE))
            detail.append((chk, {"status": f"error:{e}", "kill_safe": False})); continue
        clean = chk.predicate(golden)
        survivors = [m for m in chk.mutations if chk.predicate(mutate(golden, m)) != DEVIATION]
        kill_safe = (clean == CLEAN and not survivors)
        status = clean if kill_safe else (clean if clean == DEVIATION else INCONCLUSIVE)
        for rid in chk.req_ids:
            results.append(CheckResult(rid, chk.keyword, status, kill_safe))
        detail.append((chk, {"status": clean,
                             "kills": f"{len(chk.mutations)-len(survivors)}/{len(chk.mutations)}",
                             "kill_safe": kill_safe, "survivors": survivors}))
    return results, detail
