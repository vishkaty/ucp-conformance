#!/usr/bin/env python3
"""
mcp_only_0825.py — the MCP-ONLY 2026-08-25 merchant profile fixture (D1-11, C2b).

The Shopify template observed on 10/10 sampled stores (ops L3 N30, 2026-09-09): a
2026-08-25 profile with EIGHT capabilities (incl. the vendor capability
`dev.shopify.catalog`), a shopping service reachable over MCP (08-25) plus an `embedded`
service entry pinned to 2026-04-08, `supported_versions` {04-08, 01-23}, a vendor card
handler at 2026-01-15, and NO signing keys at any location. It is the largest shape in the
fleet and our REST-scoped runner can exercise none of its shopping checks: the honest CLI
answer is `verdict.coverage: null` + `support: "rest-not-declared"` (never 0/N), and the
merchant gate grades it against a pinned NEAR-EMPTY population
(checks/expected_skips_mcp_only_0825.json: 2 run / 226 pinned).

Loopback only; serves GET /.well-known/ucp (+ the versioned leaf), a minimal JSON-RPC
endpoint at the advertised MCP path (`tools/list` = the five core checkout tools, any other
method -> -32601; W1 integration, D1-11 x D3-10: mcp.tools_list_core_checkout is graded and
kill-tested on this shape, the product-driven MCP checks pin `needs-product`), and answers
404 to everything else — a REST probe that reaches it is the D1-11 kill direction
(`has_rest` forced True -> deviations -> red).

    python3 conformance/fixtures/profiles/mcp_only_0825.py [--port 0]   # prints the base URL
"""
import argparse
import contextlib
import http.server
import json
import threading

VERSION = "2026-08-25"
# the core checkout tools tools/list must name (MCP-003 item 2 / MCP-004; the same tuple
# merchant_checks_08_25_mcp.CORE_CHECKOUT_TOOLS grades)
MCP_CORE_CHECKOUT_TOOLS = ("create_checkout", "get_checkout", "update_checkout",
                           "complete_checkout", "cancel_checkout")
CAPABILITIES = (
    "dev.ucp.shopping.checkout", "dev.ucp.shopping.order", "dev.ucp.shopping.fulfillment",
    "dev.ucp.shopping.discount", "dev.ucp.shopping.buyer_consent", "dev.ucp.shopping.cart",
    "dev.ucp.shopping.ap2_mandates", "dev.shopify.catalog",
)


def profile(base):
    """The business profile document served at {base}/.well-known/ucp."""
    caps = {c: {"version": VERSION,
                "spec": f"https://ucp.dev/specification/{c.rsplit('.', 1)[-1]}",
                "schema": f"https://ucp.dev/schemas/shopping/{c.rsplit('.', 1)[-1]}.json"}
            for c in CAPABILITIES}
    caps["dev.shopify.catalog"] = {"version": "2026-04-08",
                                   "spec": "https://shopify.dev/docs/api/ucp/catalog",
                                   "schema": "https://shopify.dev/ucp/schemas/catalog.json",
                                   "requires": {"protocol": {"min": "2026-04-08"}}}
    return {"ucp": {
        "version": VERSION,
        "services": {"dev.ucp.shopping": [
            {"version": VERSION, "transport": "mcp", "endpoint": f"{base}/api/ucp/mcp",
             "spec": "https://ucp.dev/specification/overview",
             "schema": "https://ucp.dev/schemas/shopping/service.json"},
            {"version": "2026-04-08", "transport": "embedded",
             "endpoint": f"{base}/api/ucp/embedded",
             "spec": "https://ucp.dev/specification/overview",
             "schema": "https://ucp.dev/schemas/shopping/service.json"}]},
        "capabilities": caps,
        "supported_versions": {"2026-04-08": f"{base}/.well-known/ucp/2026-04-08",
                               "2026-01-23": f"{base}/.well-known/ucp/2026-01-23"},
        "payment_handlers": {"dev.shopify.card": [{"id": "shopify_card", "version": "2026-01-15",
                                                   "spec": "https://shopify.dev/docs/api/ucp/card",
                                                   "config_schema": "https://shopify.dev/ucp/schemas/card.json",
                                                   "instrument_schemas": ["https://shopify.dev/ucp/schemas/card_instrument.json"],
                                                   "config": {}}]},
        # no `keys` / `signing_keys` anywhere (N30: zero signing keys at every location)
    }}


class _Handler(http.server.BaseHTTPRequestHandler):
    base = ""

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") == "/.well-known/ucp":
            return self._send(200, profile(self.base))
        if self.path.startswith("/.well-known/ucp/"):
            v = self.path.rsplit("/", 1)[-1]
            doc = profile(self.base)["ucp"]
            return self._send(200, {"ucp": {**doc, "version": v}})
        self._send(404, {"error": "not found"})

    def do_POST(self):          # no REST shopping transport: every non-MCP request is a 404
        if self.path.rstrip("/") == "/api/ucp/mcp":
            return self._mcp()
        self._send(404, {"error": "not found"})

    def _mcp(self):
        """The advertised MCP endpoint, minimal: JSON-RPC 2.0 `tools/list` answers the five
        core checkout tools (checkout-mcp.md); every other method is -32601 (no tools/call:
        the fixture has no products, so the product-driven MCP checks pin needs-product)."""
        n = int(self.headers.get("Content-Length") or 0)
        try:
            doc = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            doc = {}
        rid = doc.get("id") if isinstance(doc, dict) else None
        if isinstance(doc, dict) and doc.get("method") == "tools/list":
            tools = [{"name": t, "description": f"{t.replace('_', ' ')} (UCP {VERSION})",
                      "inputSchema": {"type": "object"}} for t in MCP_CORE_CHECKOUT_TOOLS]
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        self._send(200, {"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601, "message": "method not found"}})

    do_PUT = do_DELETE = do_PATCH = do_POST

    def log_message(self, *a):
        pass


def serve(port=0):
    """Start the fixture on a loopback port (0 = ephemeral); returns (httpd, base_url)."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    _Handler.base = base
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, base


@contextlib.contextmanager
def served(port=0):
    httpd, base = serve(port)
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()


def main():
    ap = argparse.ArgumentParser(description="MCP-only 2026-08-25 profile fixture (loopback)")
    ap.add_argument("--port", type=int, default=0, help="0 = ephemeral (ports.json: unlisted by design)")
    a = ap.parse_args()
    httpd, base = serve(a.port)
    print(base, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
