#!/usr/bin/env python3
"""
area_webhook.py — live order-webhook checks (2026-01-23) using the receiver harness.

Each check runs the create -> complete (-> order_placed) -> simulate-shipping
(-> order_shipped) flow with a real platform profile whose webhook_url points at a
local receiver, then evaluates the captured events. The fetch_fn returns a Resp whose
.json = {events, checkout_id, order_id}; mutations (drop/empty the events) prove the
check catches a missing/wrong webhook. These were previously `needs-receiver`.
"""
import sys, uuid, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Check, Resp, fetch, CLEAN, DEVIATION   # noqa: E402
from webhook_harness import WebhookHarness, SIM_SECRET     # noqa: E402
import v2026_01_23 as core                                 # noqa: E402

# Citation scope: these checks encode the 2026-01-11/2026-01-23 registers' semantics
# (the 2026-04-08 registers renumbered these ids onto DIFFERENT requirements — see
# coverage/matrix.py introspection, which reads this marker for attribution).
VERSIONS = ("2026-01-11", "2026-01-23")

def _hdr(profile, **extra):
    return {"UCP-Agent": f'profile="{profile}"', "request-signature": "test",
            "idempotency-key": str(uuid.uuid4()), "request-id": str(uuid.uuid4()), **extra}

def f_webhook_flow(base):
    """Drive the full order-webhook flow; return the captured events + ids as a Resp."""
    with WebhookHarness() as h:
        r = fetch(base, "/checkout-sessions", "POST", core._create_payload(), _hdr(h.profile_url))
        cid = (r.json or {}).get("id")
        c = fetch(base, f"/checkout-sessions/{cid}/complete", "POST", core._PAYMENT, _hdr(h.profile_url))
        oid = ((c.json or {}).get("order") or {}).get("id")
        fetch(base, f"/testing/simulate-shipping/{oid}", "POST", None,
              _hdr(h.profile_url, **{"Simulation-Secret": SIM_SECRET}))
        events = h.wait_events(2, timeout=8)
    body = {"events": events, "checkout_id": cid, "order_id": oid}
    import json
    return Resp(200, {"Content-Type": "application/json"}, json.dumps(body).encode())

def _events_of(r, kind):
    if not isinstance(r.json, dict):
        return []
    out = []
    for e in r.json.get("events") or []:
        p = e.get("payload") if isinstance(e, dict) else {}
        inner = p.get("payload", p) if isinstance(p, dict) else {}
        if isinstance(inner, dict) and inner.get("event_type") == kind:
            out.append(inner)
        elif isinstance(p, dict) and p.get("event_type") == kind:
            out.append(p)
    return out

def chk_stream(r):        # ORD-012/013: order_placed AND order_shipped delivered w/ matching ids
    if not isinstance(r.json, dict):
        return DEVIATION
    cid, oid = r.json.get("checkout_id"), r.json.get("order_id")
    placed, shipped = _events_of(r, "order_placed"), _events_of(r, "order_shipped")
    def matches(evs):
        return any(str(e.get("checkout_id")) == str(cid)
                   and str((e.get("order") or {}).get("id")) == str(oid) for e in evs)
    return CLEAN if placed and shipped and matches(placed) and matches(shipped) else DEVIATION

def chk_order_placed(r):  # ORD-012: order_placed event delivered, references the checkout
    cid = r.json.get("checkout_id") if isinstance(r.json, dict) else None
    placed = _events_of(r, "order_placed")
    return CLEAN if any(str(e.get("checkout_id")) == str(cid) for e in placed) else DEVIATION

_MUT = ["drop:events", "set:events=[]", "corrupt-json", "empty"]

# versions= (in addition to the module VERSIONS marker, which only scopes coverage
# attribution): these checks encode the 01-era order_placed/order_shipped EVENT-
# ENVELOPE semantics. The 2026-04-08 registers renumbered ORD-012/013 onto DIFFERENT
# requirements (order-data auth), and the pinned reference now implements the 04-08
# webhook contract — the FULL order entity as the delivery body, no event_type
# (observed 2026-08-04; samples#140). These checks therefore CANNOT clean-pass
# against what the reference implements; the served-version gate skips them there
# instead of reporting false deviations/UNSAFE. The 04-08 webhook contract is
# kill-validated by validate_webhook_reference.py (suite gate `webhook-reference`)
# and the merchant check webhook.order_created_full_entity.
CHECKS = [
    Check("webhook.event_stream", ["ORD-012", "ORD-013"], "MUST", f_webhook_flow, chk_stream,
          _MUT, versions=VERSIONS),
    Check("webhook.order_placed", ["ORD-012"], "MUST", f_webhook_flow, chk_order_placed,
          _MUT, versions=VERSIONS),
]
