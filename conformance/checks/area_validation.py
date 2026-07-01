#!/usr/bin/env python3
"""
area_validation.py — Phase 1 checks for the errors-validation-security area
(spec 2026-01-23). Covers the VAL/SEC requirements asserted by the official
suite (validation_test.py, invalid_input_test.py, simulation_url_security_test.py)
that are NOT already covered by v2026_01_23 core (VAL-001/003/004).

Each check issues the real triggering request against the live reference server,
asserts the exact status/shape the suite asserts, and declares mutations that MUST
break it. The engine self-validates every check by kill-rate before it can count.

Triggering requests (learned from the vendored official suite):
  VAL-005  complete a session with no fulfillment selected           -> 400
  VAL-006  400 error body carries a populated 'detail' field         -> 400 + detail
  VAL-007  PUT /orders/{id} with an invalid adjustment status literal -> 422
  VAL-008  PUT /orders/{id} with a malformed (dict) adjustments field -> 422
  VAL-009  PUT /checkout-sessions/{id} with an unknown discount code  -> 200, no discount
  SEC-001  POST /testing/simulate-shipping/{id} with no secret        -> 403
  SEC-002  POST /testing/simulate-shipping/{id} with a wrong secret   -> 403
  SEC-003  POST /testing/simulate-shipping/{id} with the correct secret-> 200
"""
import sys, pathlib, json, uuid, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Check, fetch  # noqa: E402
import v2026_01_23 as core        # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "selfcheck"))
from verdict_gate import CLEAN, DEVIATION  # noqa: E402

SIM_SECRET = "selfcheck-secret"

# ---- helpers ----------------------------------------------------------------
def _completed_order(base):
    """create -> complete -> return (order_id, order_json from GET /orders/{id})."""
    cid = (core._create(base).json or {}).get("id")
    oid = ((core._complete(base, cid).json or {}).get("order") or {}).get("id")
    g = fetch(base, f"/orders/{oid}", "GET", None, core._ucp_headers())
    return oid, (g.json or {})

# ---- VAL-005: complete without fulfillment -> 400 ---------------------------
def f_complete_no_fulfillment(base):
    p = core._create_payload()
    p["id"] = str(uuid.uuid4())
    m = p["fulfillment"]["methods"][0]
    m.pop("selected_destination_id", None)
    m["groups"][0].pop("selected_option_id", None)
    cid = (fetch(base, "/checkout-sessions", "POST", p, core._ucp_headers()).json or {}).get("id")
    return fetch(base, f"/checkout-sessions/{cid}/complete", "POST", core._PAYMENT, core._ucp_headers())

def chk_http_400(r):  # VAL-005
    return CLEAN if r.status == 400 else DEVIATION

# ---- VAL-006: 400 error body carries a populated 'detail' -------------------
def f_out_of_stock(base):
    return core._create(base, item_id="gardenias")  # seeded out-of-stock -> 400

def chk_400_with_detail(r):  # VAL-006
    if r.status != 400 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("detail") else DEVIATION

# ---- VAL-007: invalid adjustment status literal -> 422 ----------------------
def f_invalid_adjustment_status(base):
    oid, od = _completed_order(base)
    body = json.loads(json.dumps(od))
    adj = {"id": f"adj_{uuid.uuid4()}", "type": "refund",
           "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "status": "INVALID_STATUS", "amount": 500}
    if body.get("adjustments") is None:
        body["adjustments"] = []
    body["adjustments"].append(adj)
    return fetch(base, f"/orders/{oid}", "PUT", body, core._ucp_headers())

# ---- VAL-008: malformed adjustments field (dict not list) -> 422 ------------
def f_malformed_adjustments(base):
    oid, od = _completed_order(base)
    body = json.loads(json.dumps(od))
    body["adjustments"] = {"id": "adj_1", "amount": 100}
    return fetch(base, f"/orders/{oid}", "PUT", body, core._ucp_headers())

def chk_http_422(r):  # VAL-007 / VAL-008
    return CLEAN if r.status == 422 else DEVIATION

# ---- VAL-009: unknown discount code -> 200 with no discount applied ---------
def f_unknown_discount(base):
    co = core._create(base).json or {}
    cid = co.get("id")
    body = json.loads(json.dumps(co))
    body["discounts"] = {"codes": ["INVALID_CODE_123"]}
    return fetch(base, f"/checkout-sessions/{cid}", "PUT", body, core._ucp_headers())

def chk_200_no_discount(r):  # VAL-009
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    totals = r.json.get("totals") or []
    if any(isinstance(t, dict) and t.get("type") == "discount" for t in totals):
        return DEVIATION
    return CLEAN

# ---- SEC-001/002/003: simulation endpoint secret gate -----------------------
def _sim(base, secret):
    oid, _ = _completed_order(base)
    h = core._ucp_headers()
    if secret is not None:
        h["Simulation-Secret"] = secret
    return fetch(base, f"/testing/simulate-shipping/{oid}", "POST", None, h)

def f_sim_no_secret(base):    return _sim(base, None)
def f_sim_wrong_secret(base): return _sim(base, "for-sure-incorrect-secret")
def f_sim_ok_secret(base):    return _sim(base, SIM_SECRET)

def chk_http_403(r):  # SEC-001 / SEC-002
    return CLEAN if r.status == 403 else DEVIATION

def chk_http_200(r):  # SEC-003
    return CLEAN if r.status == 200 else DEVIATION

# ---- registry ---------------------------------------------------------------
_DISCOUNT_TOTAL = '[{"type":"discount","amount":-100,"display_text":"x"}]'

CHECKS = [
    Check("validation.complete_no_fulfillment", ["VAL-005"], "MUST",
          f_complete_no_fulfillment, chk_http_400,
          ["status:200", "status:201", "status:402"]),
    Check("validation.error_detail_400", ["VAL-006"], "MUST",
          f_out_of_stock, chk_400_with_detail,
          ["status:200", "drop:detail", 'set:detail=""', "empty", "corrupt-json"]),
    Check("validation.invalid_adjustment_status", ["VAL-007"], "MUST",
          f_invalid_adjustment_status, chk_http_422,
          ["status:200", "status:201", "status:400"]),
    Check("validation.malformed_adjustments", ["VAL-008"], "MUST",
          f_malformed_adjustments, chk_http_422,
          ["status:200", "status:201", "status:400"]),
    Check("validation.unknown_discount_code", ["VAL-009"], "MUST",
          f_unknown_discount, chk_200_no_discount,
          ["status:400", "status:422", "set:totals=" + _DISCOUNT_TOTAL,
           "empty", "corrupt-json"]),
    Check("security.sim_missing_secret_403", ["SEC-001"], "MUST",
          f_sim_no_secret, chk_http_403, ["status:200", "status:201"]),
    Check("security.sim_wrong_secret_403", ["SEC-002"], "MUST",
          f_sim_wrong_secret, chk_http_403, ["status:200", "status:201"]),
    Check("security.sim_correct_secret_200", ["SEC-003"], "MUST",
          f_sim_ok_secret, chk_http_200, ["status:403", "status:500"]),
]
