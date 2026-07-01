#!/usr/bin/env python3
"""
Controlled UCP merchant fixture (spec 2026-04-08) — our OWN golden for capabilities
the official samples don't implement (catalog search/lookup; cart next).

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
import json, argparse
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
]
BY_ID = {p["id"]: p for p in PRODUCTS}
BY_VARIANT = {v["id"]: p for p in PRODUCTS for v in p["variants"]}

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
        if self.path.rstrip("/") == "/.well-known/ucp":
            return self._send(200, profile(self._base()))
        self._send(404, {"error_code": "not_found"})
    def do_POST(self):
        body = self._body()
        if body is None:
            return self._send(400, {"error_code": "invalid_request"})
        if self.path.rstrip("/") == "/catalog/search":
            return self._send(200, search_response(body.get("query")))
        if self.path.rstrip("/") == "/catalog/lookup":
            ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
            return self._send(200, lookup_response(ids))
        if self.path.rstrip("/") == "/carts":
            return self._send(201, cart_response(body))
        if self.path.rstrip("/") == "/ucp/mcp":         # MCP transport (JSON-RPC tools/call)
            return self._send(200, mcp_dispatch(body))
        self._send(404, {"error_code": "not_found"})

def main():
    ap = argparse.ArgumentParser(description="Controlled UCP merchant fixture (2026-04-08).")
    ap.add_argument("--port", type=int, default=8184)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), _H)
    print(f"controlled merchant on http://{args.host}:{args.port} "
          f"(catalog.search + catalog.lookup, spec {VERSION})")
    try: srv.serve_forever()
    except KeyboardInterrupt: srv.shutdown()

if __name__ == "__main__":
    main()
