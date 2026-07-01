#!/usr/bin/env python3
"""
v2026_01_23.py — Phase 1 conformance checks for spec 2026-01-23 (first slice).

Each check cites register requirement id(s), evaluates a real server response,
and declares the mutations that must break it. Run against the live Flower Shop
reference server, the engine self-validates each check (kill-rate) and the verdict
gate produces an honest, coverage-gated report. With only a few checks implemented
the aggregate MUST be `incomplete` (most MUSTs not-tested) — never a false green.

This slice is discovery-only; lifecycle/idempotency/order/payment checks follow the
same Check(...) pattern and are added incrementally, each kill-rate-validated.
"""
import sys, pathlib
from urllib.parse import urlparse
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Check, fetch, run_report  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "selfcheck"))
from verdict_gate import CLEAN, DEVIATION  # noqa: E402

LOOPBACK = {"localhost", "127.0.0.1", "::1"}

def _discovery(base):
    return fetch(base, "/.well-known/ucp")

def chk_profile_ok(r):
    # DISC-013: profile MUST return 200 with the expected structure (version present).
    if r.status != 200: return DEVIATION
    d = r.json
    if not isinstance(d, dict): return DEVIATION
    return CLEAN if isinstance(d.get("version"), str) else DEVIATION

def chk_rest_endpoint(r):
    # DISC-007: a service endpoint MUST be a valid https URL. Tolerate http on
    # loopback (dev servers) per AMB; a non-loopback non-https endpoint -> deviation.
    if r.status != 200 or not isinstance(r.json, dict): return DEVIATION
    svcs = (r.json.get("services") or {}).get("dev.ucp.shopping")
    if not isinstance(svcs, list): return DEVIATION
    rest = next((s for s in svcs if isinstance(s, dict) and s.get("transport") == "rest"), None)
    if not rest or not rest.get("endpoint"): return DEVIATION
    u = urlparse(rest["endpoint"])
    if not u.scheme or not u.netloc: return DEVIATION
    host = u.hostname or ""
    return CLEAN if (u.scheme == "https" or host in LOOPBACK) else DEVIATION

# ---- stateful checkout-lifecycle + idempotency helpers ---------------------
import uuid  # noqa: E402

STATUS_ENUM = {"incomplete", "requires_escalation", "ready_for_complete",
               "complete_in_progress", "completed", "canceled"}

def _ucp_headers(idem=None):
    return {"UCP-Agent": 'profile="https://example.com/platform"',
            "request-signature": "test",
            "idempotency-key": idem or str(uuid.uuid4()),
            "request-id": str(uuid.uuid4())}

def _create_payload(quantity=1, item_id="bouquet_roses"):
    return {"id": str(uuid.uuid4()), "currency": "USD",
            "line_items": [{"id": "line_item_123", "quantity": quantity,
                            "item": {"id": item_id, "price": 1000}, "totals": []}],
            "payment": {"instruments": [], "handlers": [
                {"id": "google_pay", "name": "google.pay", "version": "2026-01-23",
                 "spec": "https://example.com/spec", "config_schema": "https://example.com/schema",
                 "instrument_schemas": ["https://example.com/instrument_schema"], "config": {}}]},
            "fulfillment": {"methods": [{"id": "method_1", "type": "shipping",
                "destinations": [{"id": "dest_1", "address_country": "US"}],
                "line_item_ids": ["line_item_123"], "selected_destination_id": "dest_1",
                "groups": [{"id": "group_1", "line_item_ids": ["line_item_123"],
                            "selected_option_id": "std-ship"}]}]},
            "status": "incomplete", "ucp": {"version": "2026-01-23"},
            "totals": [], "links": []}

def _create(base, idem=None, quantity=1, item_id="bouquet_roses"):
    return fetch(base, "/checkout-sessions", "POST", _create_payload(quantity, item_id),
                 _ucp_headers(idem))

def f_create(base):
    return _create(base)

def f_get(base):
    r = _create(base)
    cid = (r.json or {}).get("id")
    return fetch(base, f"/checkout-sessions/{cid}", "GET", None, _ucp_headers())

def f_cancel(base):
    r = _create(base)
    cid = (r.json or {}).get("id")
    return fetch(base, f"/checkout-sessions/{cid}/cancel", "POST", None, _ucp_headers())

def f_idem_conflict(base):
    k = str(uuid.uuid4())
    _create(base, idem=k, quantity=1)              # first body
    return _create(base, idem=k, quantity=2)        # SAME key, DIFFERENT body -> must 409

# ---- stateful predicates ---------------------------------------------------
def chk_create(r):       # CHK-001
    if r.status not in (200, 201) or not isinstance(r.json, dict): return DEVIATION
    return CLEAN if r.json.get("id") and r.json.get("status") in STATUS_ENUM else DEVIATION

def chk_get(r):          # CHK-002
    if r.status != 200 or not isinstance(r.json, dict): return DEVIATION
    return CLEAN if r.json.get("id") and r.json.get("status") in STATUS_ENUM else DEVIATION

def chk_cancel(r):       # CHK-005
    if r.status != 200 or not isinstance(r.json, dict): return DEVIATION
    return CLEAN if r.json.get("status") == "canceled" else DEVIATION

def chk_idem_409(r):     # IDM-004
    return CLEAN if r.status == 409 else DEVIATION

def f_out_of_stock(base):  return _create(base, item_id="gardenias")     # seeded out-of-stock
def f_nonexistent(base):   return _create(base, item_id="pink_wumpus")   # seeded non-existent
def chk_http_400(r):       # VAL-001 / VAL-003 (validation failures MUST 400)
    return CLEAN if r.status == 400 else DEVIATION

# --- complete + completed-immutability -------------------------------------
_PAYMENT = {"payment": {"instruments": [{"id": "instr_1", "handler_id": "mock_payment_handler",
    "type": "card", "display": {"brand": "Visa", "last_digits": "1234"},
    "credential": {"type": "token", "token": "success_token"},
    "billing_address": {"street_address": "123 Main St", "address_locality": "Anytown",
        "address_region": "CA", "address_country": "US", "postal_code": "12345"}}]},
    "risk_signals": {}}

def _complete(base, cid):
    return fetch(base, f"/checkout-sessions/{cid}/complete", "POST", _PAYMENT, _ucp_headers())

def f_complete(base):
    r = _create(base); return _complete(base, (r.json or {}).get("id"))

def f_completed_immutable(base):
    cid = (_create(base).json or {}).get("id")
    _complete(base, cid)
    return fetch(base, f"/checkout-sessions/{cid}/cancel", "POST", None, _ucp_headers())

def chk_complete(r):       # CHK-004 + CHK-008
    if r.status != 200 or not isinstance(r.json, dict): return DEVIATION
    return CLEAN if r.json.get("status") == "completed" and r.json.get("order") else DEVIATION

def chk_rejected_4xx(r):   # CHK-012: mutating a completed checkout MUST be rejected
    return CLEAN if 400 <= r.status < 500 else DEVIATION

_FAILPAY = {"payment": {"instruments": [{"id": "instr_fail", "handler_id": "mock_payment_handler",
    "type": "card", "display": {"brand": "Visa", "last_digits": "0000"},
    "credential": {"type": "token", "token": "fail_token"},
    "billing_address": {"street_address": "1 A St", "address_locality": "X",
        "address_region": "CA", "address_country": "US", "postal_code": "12345"}}]},
    "risk_signals": {}}

def f_payment_fail(base):     # VAL-004: known failing token -> 402
    cid = (_create(base).json or {}).get("id")
    return fetch(base, f"/checkout-sessions/{cid}/complete", "POST", _FAILPAY, _ucp_headers())
def chk_http_402(r):
    return CLEAN if r.status == 402 else DEVIATION

def chk_fulfil_options(r):    # FUL-008: each fulfillment option MUST have id, title, totals
    if r.status not in (200, 201) or not isinstance(r.json, dict): return DEVIATION
    try:
        opts = r.json["fulfillment"]["methods"][0]["groups"][0]["options"]
    except (KeyError, IndexError, TypeError):
        return DEVIATION
    if not opts: return DEVIATION
    ok = all(o.get("id") and o.get("title") and ("totals" in o) for o in opts)
    return CLEAN if ok else DEVIATION

CHECKS = [
    Check("disc.profile_200", ["DISC-013"], "MUST", _discovery, chk_profile_ok,
          ["status:404", "status:500", "drop:version", "corrupt-json", "empty"]),
    Check("disc.rest_endpoint", ["DISC-007"], "MUST", _discovery, chk_rest_endpoint,
          ["drop:services", "set:services={}", "corrupt-json", "status:500"]),
    Check("checkout.create", ["CHK-001"], "MUST", f_create, chk_create,
          ["status:500", "drop:id", "drop:status", "set:status=\"bogus\"", "empty"]),
    Check("checkout.get", ["CHK-002"], "MUST", f_get, chk_get,
          ["status:404", "drop:id", "drop:status", "empty", "corrupt-json"]),
    Check("checkout.cancel", ["CHK-005"], "MUST", f_cancel, chk_cancel,
          ["status:500", "set:status=\"incomplete\"", "drop:status", "corrupt-json"]),
    Check("idempotency.conflict_409", ["IDM-004"], "MUST", f_idem_conflict, chk_idem_409,
          ["status:200", "status:201"]),
    Check("validation.out_of_stock", ["VAL-001"], "MUST", f_out_of_stock, chk_http_400,
          ["status:200", "status:201"]),
    Check("validation.nonexistent_product", ["VAL-003"], "MUST", f_nonexistent, chk_http_400,
          ["status:200", "status:201"]),
    Check("checkout.complete", ["CHK-004", "CHK-008"], "MUST", f_complete, chk_complete,
          ["status:500", "set:status=\"incomplete\"", "drop:order", "drop:status"]),
    Check("checkout.completed_immutable", ["CHK-012"], "MUST", f_completed_immutable, chk_rejected_4xx,
          ["status:200", "status:201"]),
    Check("validation.payment_failure", ["VAL-004"], "MUST", f_payment_fail, chk_http_402,
          ["status:200", "status:201"]),
    Check("fulfillment.option_shape", ["FUL-008"], "MUST", f_create, chk_fulfil_options,
          ["status:500", "drop:fulfillment",
           "drop:fulfillment.methods.0.groups.0.options.0.title", "corrupt-json"]),
]

SCOPE_STAMP = {
    "spec_version": "2026-01-23",
    "spec_commit": "dcf7eac71fc370dcc8768fcdbc5aa737037cca05",
    "tool": "spck.dev conformance (dev)",
    "methodology": "register-driven; kill-rate-gated; ucp-schema oracle",
}
DISCLAIMER = ("Independent, unofficial tool. Not affiliated with, endorsed by, or a "
             "substitute for the official UCP conformance suite. Results are limited "
             "to the checks listed and reflect the checks actually run.")

def main(base="http://localhost:8182"):
    rep, details = run_report(CHECKS, base, "2026-01-23", SCOPE_STAMP, DISCLAIMER)
    print(f"target: {base}\n")
    print(f"{'check':22} {'clean':6} {'kill':6} {'kill_safe':9}")
    for chk, det in details:
        print(f"{chk.id:22} {str(det['clean']):6} {det['kills']:6} {str(det['kill_safe']):9}"
              + (f"  survivors={det['survivors']}" if det.get("survivors") else ""))
    c = rep.counts
    print(f"\n--- REPORT (spec 2026-01-23 @ {SCOPE_STAMP['spec_commit'][:7]}) ---")
    print(f"aggregate: {rep.aggregate.upper()}")
    print(f"MUST coverage: {c['musts_clean_pass']}/{c['inscope_musts']} "
          f"({round(100*rep.coverage)}%)   deviations: {c['deviations']}   "
          f"blocking(not-tested/etc): {c['blocking']}")
    print("first blocking items:", "; ".join(rep.blocking[:3]),
          f"... (+{max(0,len(rep.blocking)-3)} more)")
    print("disclaimer present:", bool(rep.scope_stamp))
    assert rep.aggregate != "pass", "FALSE GREEN with partial coverage — gate failed!"
    print("\nOK: partial coverage correctly yields a non-green verdict (no false certification).")
    return 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
