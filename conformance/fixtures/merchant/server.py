#!/usr/bin/env python3
"""
Controlled UCP merchant fixture (spec 2026-04-08) — our OWN golden for capabilities
the official samples don't implement (catalog search/lookup, cart, checkout lifecycle).

Why this exists: neither official sample (Python Flower Shop, Node.js) declares
`catalog` or `cart`, so those requirements can't be reference-gated against them.
This fixture fills that gap. It is NOT a substitute oracle for the whole spec — its
trustworthiness comes from an INDEPENDENT anchor: every profile/response it serves is
validated against the official `ucp.json` / catalog schemas by the `ucp-schema` Rust
validator (see conformance/fixtures/merchant/selfcheck.py). So a check that clean-passes
here is anchored to the official validator, not to our own checks (no circularity).

Dependency-free (stdlib http.server), so CI can boot it in one line.
    python3 conformance/fixtures/merchant/server.py --port 8184
"""
import json, argparse, uuid, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2026-04-08"

# ---- controlled seed catalog (stable ids the checks rely on) -----------------
def _product(pid, vid, title, price, desc):
    money = {"amount": price, "currency": "USD"}
    return {
        "id": pid, "title": title, "handle": pid.replace("_", "-"),
        "description": {"text": desc},
        "price_range": {"min": money, "max": money},
        "variants": [{"id": vid, "title": "Default", "price": money,
                      "description": {"text": desc + " (default variant)"}}],
    }

PRODUCTS = [
    _product("teapot_ceramic", "teapot_ceramic_v1", "Ceramic Teapot", 2500,
             "A sturdy stoneware teapot."),
    _product("mug_enamel", "mug_enamel_v1", "Enamel Mug", 1200,
             "A camp-style enamel mug."),
    _product("kettle_copper", "kettle_copper_v1", "Copper Kettle", 6800,
             "A polished copper stovetop kettle."),
    _product("trivet_cork", "trivet_cork_v1", "Cork Trivet", 900,
             "A cork trivet, currently out of stock."),
]
BY_ID = {p["id"]: p for p in PRODUCTS}
BY_VARIANT = {v["id"]: p for p in PRODUCTS for v in p["variants"]}

# Per-item available stock. Deliberately small so an over-stock quantity (the VAL-002
# probe uses 10001) is always rejected, while normal 1-3 quantity flows succeed.
# trivet_cork is the SEEDED OUT-OF-STOCK item (drives VAL-001/VAL-006 negatives).
STOCK_DEFAULT = 10
STOCK = {"trivet_cork": 0}

def _stock(iid):
    pid = BY_VARIANT[iid]["id"] if iid in BY_VARIANT else iid
    return STOCK.get(pid, STOCK_DEFAULT)

# Payment tokens the fixture recognizes (mirrors the Flower Shop golden's seeded
# success/fail tokens so the same config pattern drives both goldens).
FAIL_TOKEN = "fail_token"

def profile(base):
    cap = [{"version": VERSION}]
    return {
        "version": VERSION,
        "services": {"dev.ucp.shopping": [
            {"version": VERSION, "transport": "rest", "endpoint": base,
             "spec": "https://ucp.dev/2026-04-08/specification/shopping",
             "schema": "https://ucp.dev/2026-04-08/services/shopping/openapi.json"},
            {"version": VERSION, "transport": "mcp", "endpoint": base + "/ucp/mcp",
             "spec": "https://ucp.dev/2026-04-08/specification/shopping",
             "schema": "https://ucp.dev/2026-04-08/services/shopping/mcp.openrpc.json"}]},
        "capabilities": {
            "dev.ucp.shopping.catalog.search": cap,
            "dev.ucp.shopping.catalog.lookup": cap,
            "dev.ucp.shopping.cart": cap,
            "dev.ucp.shopping.checkout": cap,
            "dev.ucp.shopping.order": cap,
        },
        "payment_handlers": {},
    }

def _unit_price(item_id):
    """Unit price (minor units) for a product or variant id, from the seed catalog."""
    if item_id in BY_ID:
        return BY_ID[item_id]["price_range"]["min"]["amount"]
    if item_id in BY_VARIANT:
        p = BY_VARIANT[item_id]
        v = next((v for v in p["variants"] if v["id"] == item_id), None)
        return (v or {}).get("price", {}).get("amount", p["price_range"]["min"]["amount"])
    return 1000

def cart_response(body):
    """Build a spec-valid cart (checkout.json + cart_id) from requested line_items."""
    reqs = (body or {}).get("line_items") or []
    line_items, subtotal = [], 0
    for i, li in enumerate(reqs):
        iid = (li.get("item") or {}).get("id") or li.get("id")
        qty = int(li.get("quantity", 1) or 1)
        amt = _unit_price(iid) * qty
        subtotal += amt
        line_items.append({"id": f"li_{i+1}", "item": {"id": iid}, "quantity": qty,
                           "totals": [{"type": "subtotal", "amount": amt}]})
    cid = "cart_" + ((reqs[0].get("item") or {}).get("id", "empty") if reqs else "empty")
    return {"ucp": {"version": VERSION}, "id": cid, "cart_id": cid,
            "currency": (body or {}).get("currency", "USD"), "status": "incomplete",
            "line_items": line_items,
            "totals": [{"type": "subtotal", "amount": subtotal},
                       {"type": "total", "amount": subtotal}]}

# ---- checkout lifecycle (create/get/update/complete/cancel) ------------------
# Pure functions returning (http_status, payload) so selfcheck.py can validate every
# artifact against the official schemas without going through HTTP.
SESSIONS = {}       # checkout id -> session state
IDEM = {}           # idempotency-key -> (body_fingerprint, http_status, payload)
_LOCK = threading.Lock()

def _title(iid):
    if iid in BY_ID:
        return BY_ID[iid]["title"]
    if iid in BY_VARIANT:
        p = BY_VARIANT[iid]
        v = next((v for v in p["variants"] if v["id"] == iid), None)
        return f'{p["title"]} — {v["title"]}' if v else p["title"]
    return iid

def _ucp_envelope():
    """The `ucp` response envelope every checkout/order response MUST carry
    (ucp.json $defs response_checkout_schema: version + payment_handlers required)."""
    return {"version": VERSION,
            "capabilities": {"dev.ucp.shopping.checkout": [
                {"version": VERSION,
                 "schema": "https://ucp.dev/schemas/shopping/checkout.json"}]},
            "payment_handlers": {}}

LINKS = [{"type": "terms_of_service", "url": "https://spck.dev/fixture/tos"},
         {"type": "privacy_policy", "url": "https://spck.dev/fixture/privacy"}]

def _err(status, detail):
    """Structured error body: a populated `detail` string (the shape VAL-006 requires
    of 400 responses, matching the reference server's error envelope)."""
    return status, {"detail": detail}

def _build_line_items(reqs):
    """Resolve requested line_items against the seed catalog + stock.
    Returns (line_items, None) or (None, (status, error_payload))."""
    if not isinstance(reqs, list) or not reqs:
        return None, _err(400, "line_items is required and must be a non-empty array")
    out = []
    for i, li in enumerate(reqs):
        if not isinstance(li, dict):
            return None, _err(400, f"line_items[{i}] must be an object")
        iid = (li.get("item") or {}).get("id")
        if not iid:
            return None, _err(400, f"line_items[{i}].item.id is required")
        if iid not in BY_ID and iid not in BY_VARIANT:
            return None, _err(400, f"Unknown item id: {iid}")
        try:
            qty = int(li.get("quantity", 1) or 1)
        except (TypeError, ValueError):
            return None, _err(400, f"line_items[{i}].quantity must be an integer")
        if qty < 1:
            return None, _err(400, f"line_items[{i}].quantity must be >= 1")
        if qty > _stock(iid):
            return None, _err(400, f"Insufficient stock for item {iid} "
                                   f"(requested {qty}, available {_stock(iid)})")
        price = _unit_price(iid)
        out.append({"id": li.get("id") or f"li_{i+1}",
                    "item": {"id": iid, "title": _title(iid), "price": price},
                    "quantity": qty,
                    "totals": [{"type": "subtotal", "display_text": "Subtotal",
                                "amount": price * qty}]})
    return out, None

def checkout_body(sess):
    """Render a session as a spec-valid checkout response (checkout.json requires
    ucp, id, line_items, status, currency, totals, links)."""
    subtotal = sum(li["totals"][0]["amount"] for li in sess["line_items"])
    out = {"ucp": _ucp_envelope(), "id": sess["id"], "status": sess["status"],
           "currency": sess["currency"], "line_items": sess["line_items"],
           "totals": [{"type": "subtotal", "display_text": "Subtotal", "amount": subtotal},
                      {"type": "total", "display_text": "Total", "amount": subtotal}],
           "links": LINKS}
    if sess.get("order"):
        out["order"] = sess["order"]        # order_confirmation: id + permalink_url
    return out

def create_checkout(body, headers=None):
    """POST /checkout-sessions. Enforces: UCP-Agent required (CHK-052), line_items
    required (CHK-018), known items + stock (VAL-003/VAL-001), idempotency-key
    conflict -> 409 (IDM-004)."""
    headers = headers or {}
    if not headers.get("UCP-Agent"):
        return _err(400, "UCP-Agent header is required")
    if body is None or not isinstance(body, dict):
        return _err(400, "request body must be a JSON object")
    if "line_items" not in body:
        return _err(400, "line_items is required on create")
    key = headers.get("idempotency-key")
    fp = json.dumps(body, sort_keys=True)
    with _LOCK:
        if key and key in IDEM:
            prev_fp, prev_status, prev_payload = IDEM[key]
            if prev_fp != fp:
                return _err(409, "idempotency-key conflict: same key with a different body")
            return prev_status, prev_payload           # replay the original result
    line_items, err = _build_line_items(body.get("line_items"))
    if err:
        return err
    sess = {"id": "chk_" + uuid.uuid4().hex[:12], "status": "ready_for_complete",
            "currency": body.get("currency", "USD"), "line_items": line_items}
    with _LOCK:
        SESSIONS[sess["id"]] = sess
        result = 201, checkout_body(sess)
        if key:
            IDEM[key] = (fp, *result)
    return result

def get_checkout(sid, headers=None):
    """GET /checkout-sessions/{id}."""
    sess = SESSIONS.get(sid)
    if not sess:
        return _err(404, f"checkout session not found: {sid}")
    return 200, checkout_body(sess)

def update_checkout(sid, body, headers=None):
    """PUT /checkout-sessions/{id}. Enforces: top-level id required on update
    (CHK-016), line_items required (CHK-018), stock revalidation -> 400 (VAL-002),
    completed/canceled sessions immutable."""
    headers = headers or {}
    if not headers.get("UCP-Agent"):
        return _err(400, "UCP-Agent header is required")
    sess = SESSIONS.get(sid)
    if not sess:
        return _err(404, f"checkout session not found: {sid}")
    if body is None or not isinstance(body, dict):
        return _err(400, "request body must be a JSON object")
    if not body.get("id"):
        return _err(400, "top-level id is required on update requests")
    if body["id"] != sid:
        return _err(400, f"body id {body['id']} does not match path id {sid}")
    if sess["status"] in ("completed", "canceled"):
        return _err(409, f"checkout session is {sess['status']} and cannot be updated")
    if "line_items" not in body:
        return _err(400, "line_items is required on update")
    line_items, err = _build_line_items(body.get("line_items"))
    if err:
        return err
    sess["line_items"] = line_items
    if "currency" in body:
        sess["currency"] = body["currency"]
    return 200, checkout_body(sess)

ORDERS = {}         # order id -> order state

def _payment_tokens(body):
    """Raw credential tokens inside a complete request's payment.instruments."""
    insts = ((body or {}).get("payment") or {}).get("instruments") or []
    return [t for t in ((i.get("credential") or {}).get("token") for i in insts
                        if isinstance(i, dict)) if isinstance(t, str)]

def order_body(order):
    """Render a stored order as a spec-valid order response (order.json requires ucp,
    id, checkout_id, permalink_url, line_items, fulfillment, currency, totals)."""
    return {"ucp": {"version": VERSION,
                    "capabilities": {"dev.ucp.shopping.order": [
                        {"version": VERSION,
                         "schema": "https://ucp.dev/schemas/shopping/order.json"}]}},
            **{k: order[k] for k in ("id", "checkout_id", "permalink_url", "currency",
                                     "line_items", "fulfillment", "totals")}}

def complete_checkout(sid, body, headers=None):
    """POST /checkout-sessions/{id}/complete -> 'completed' + an order confirmation.
    The seeded FAIL_TOKEN credential is declined with 402 (VAL-004); credentials are
    never echoed back (PAY-009)."""
    sess = SESSIONS.get(sid)
    if not sess:
        return _err(404, f"checkout session not found: {sid}")
    if sess["status"] == "canceled":
        return _err(409, "checkout session is canceled and cannot be completed")
    if sess["status"] == "completed":
        return _err(409, "checkout session is already completed")
    if FAIL_TOKEN in _payment_tokens(body):
        return _err(402, "payment declined by the payment handler")
    oid = "ord_" + uuid.uuid4().hex[:12]
    permalink = f"https://spck.dev/fixture/orders/{oid}"
    checkout = checkout_body(sess)          # totals before flipping status
    order = {"id": oid, "checkout_id": sid, "permalink_url": permalink,
             "currency": sess["currency"],
             "line_items": [{"id": li["id"], "item": li["item"],
                             "quantity": {"original": li["quantity"],
                                          "total": li["quantity"], "fulfilled": 0},
                             "totals": li["totals"], "status": "processing"}
                            for li in sess["line_items"]],
             "fulfillment": {"expectations": [], "events": []},
             "totals": checkout["totals"]}
    with _LOCK:
        ORDERS[oid] = order
        sess["status"] = "completed"
        sess["order"] = {"id": oid, "permalink_url": permalink}
    return 200, checkout_body(sess)

def get_order(oid, headers=None):
    """GET /orders/{id}."""
    order = ORDERS.get(oid)
    if not order:
        return _err(404, f"order not found: {oid}")
    return 200, order_body(order)

def cancel_checkout(sid, headers=None):
    """POST /checkout-sessions/{id}/cancel -> status 'canceled'; a completed
    checkout is immutable (CHK-012) -> 4xx."""
    sess = SESSIONS.get(sid)
    if not sess:
        return _err(404, f"checkout session not found: {sid}")
    if sess["status"] == "completed":
        return _err(409, "checkout session is completed and cannot be canceled")
    sess["status"] = "canceled"
    return 200, checkout_body(sess)

def search_response(query):
    q = (query or "").strip().lower()
    hits = [p for p in PRODUCTS if not q or q == "*" or q in p["title"].lower()
            or q in p["description"]["text"].lower()]
    return {"ucp": {"version": VERSION}, "products": hits,
            "pagination": {"has_next_page": False, "total_count": len(hits)}}

def _detail(p, requested):
    """Lookup returns DETAIL products whose variants carry `inputs` — an input_correlation
    per variant tying it to the requested id and how it matched (search omits this)."""
    d = json.loads(json.dumps(p))
    for v in d["variants"]:
        rid = next((r for r in requested if r in (p["id"], v["id"])), p["id"])
        v["inputs"] = [{"id": rid, "match": "exact" if rid == v["id"] else "product"}]
    return d

def lookup_response(ids):
    ids = ids or []
    hits = [BY_ID[i] for i in ids if i in BY_ID] + \
           [BY_VARIANT[i] for i in ids if i in BY_VARIANT and i not in BY_ID]
    return {"ucp": {"version": VERSION}, "products": [_detail(p, ids) for p in hits]}

def mcp_dispatch(rpc):
    """Handle a JSON-RPC `tools/call` (the UCP MCP transport, per checkout-mcp.md):
    route to a shopping operation and wrap the UCP object in result.structuredContent.
    Reuses the exact same handlers as REST, so both transports return identical payloads."""
    rid = (rpc or {}).get("id")
    def ok(payload): return {"jsonrpc": "2.0", "id": rid,
                             "result": {"structuredContent": payload}}
    def err(code, msg): return {"jsonrpc": "2.0", "id": rid,
                                "error": {"code": code, "message": msg}}
    if (rpc or {}).get("method") != "tools/call":
        return err(-32601, "only tools/call is supported")
    params = rpc.get("params") or {}
    name, args = params.get("name"), (params.get("arguments") or {})
    if not ((args.get("meta") or {}).get("ucp-agent")):     # required on every request
        return err(-32602, "meta.ucp-agent is required")
    cat = args.get("catalog") or {}
    if name == "search_catalog":
        return ok(search_response(cat.get("query")))
    if name == "lookup_catalog":
        ids = cat.get("ids") or ([cat["id"]] if cat.get("id") else [])
        return ok(lookup_response(ids))
    if name == "create_cart":
        return ok(cart_response(args.get("cart") or {}))
    return err(-32601, f"unknown tool: {name}")

class _H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def _base(self):
        host = self.headers.get("Host") or f"localhost:{self.server.server_address[1]}"
        return f"http://{host}"
    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try: return json.loads(self.rfile.read(n)) if n else {}
        except Exception: return None
    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/.well-known/ucp":
            return self._send(200, profile(self._base()))
        if path.startswith("/checkout-sessions/"):
            sid = path.split("/")[2]
            return self._send(*get_checkout(sid, self.headers))
        if path.startswith("/orders/"):
            return self._send(*get_order(path.split("/")[2], self.headers))
        self._send(404, {"error_code": "not_found"})
    def do_PUT(self):
        body = self._body()
        path = self.path.rstrip("/")
        if body is None:
            return self._send(400, {"detail": "request body is not valid JSON"})
        if path.startswith("/checkout-sessions/") and path.count("/") == 2:
            sid = path.split("/")[2]
            return self._send(*update_checkout(sid, body, self.headers))
        self._send(404, {"error_code": "not_found"})
    def do_POST(self):
        body = self._body()
        path = self.path.rstrip("/")
        if body is None and path != "/checkout-sessions" \
           and not (path.startswith("/checkout-sessions/") and path.endswith(("/complete", "/cancel"))):
            return self._send(400, {"error_code": "invalid_request"})
        if path == "/catalog/search":
            return self._send(200, search_response(body.get("query")))
        if path == "/catalog/lookup":
            ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
            return self._send(200, lookup_response(ids))
        if path == "/carts":
            return self._send(201, cart_response(body))
        if path == "/checkout-sessions":
            return self._send(*create_checkout(body, self.headers))
        if path.startswith("/checkout-sessions/"):
            parts = path.split("/")          # '', 'checkout-sessions', sid, action
            if len(parts) == 4 and parts[3] == "complete":
                return self._send(*complete_checkout(parts[2], body, self.headers))
            if len(parts) == 4 and parts[3] == "cancel":
                return self._send(*cancel_checkout(parts[2], self.headers))
        if path == "/ucp/mcp":               # MCP transport (JSON-RPC tools/call)
            return self._send(200, mcp_dispatch(body))
        self._send(404, {"error_code": "not_found"})

def main():
    ap = argparse.ArgumentParser(description="Controlled UCP merchant fixture (2026-04-08).")
    ap.add_argument("--port", type=int, default=8184)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), _H)
    print(f"controlled merchant on http://{args.host}:{args.port} "
          f"(catalog + cart + checkout lifecycle, spec {VERSION})")
    try: srv.serve_forever()
    except KeyboardInterrupt: srv.shutdown()

if __name__ == "__main__":
    main()
