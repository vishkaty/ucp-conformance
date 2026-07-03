#!/usr/bin/env python3
"""
webhook_harness.py — receiver + platform-profile servers to exercise order webhooks.

Order-webhook requirements are `needs-receiver`: the business POSTs order events to a
platform-provided `webhook_url`. To test delivery we stand up two local threaded
servers:
  * a RECEIVER that captures POSTed events, and
  * a PLATFORM-PROFILE server that serves a UCP platform profile whose
    dev.ucp.shopping.order capability `config.webhook_url` points at the receiver.
We then drive create -> complete (fires `order_placed`) and
POST /testing/simulate-shipping/{id} (fires `order_shipped`), and return the
captured events so a check can assert delivery + payload correctness. Mutations of
the captured events (drop/corrupt) prove the check catches missing/wrong webhooks.

Profile template is the official suite's shopping-agent-test.json (spec 2026-01-23).
"""
import json, threading, time, pathlib, base64, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VENDOR = pathlib.Path(__file__).resolve().parents[1] / ".vendor"
PROFILE_TEMPLATE = (VENDOR / "conformance" / "shopping-agent-test.json").read_text()
SIM_SECRET = "selfcheck-secret"

class _Receiver(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        try: payload = json.loads(raw)
        except Exception: payload = {"_unparsed": raw.decode("latin1")}
        self.server.events.append({"path": self.path, "payload": payload})
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers()
        self.wfile.write(b"{}")

class _Profile(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.rstrip("/").endswith("shopping-agent.json"):
            body = self.server.profile.encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

class WebhookHarness:
    def __init__(self, recv_port=8190, profile_port=8191):
        self.recv_port, self.profile_port = recv_port, profile_port
        self.recv = ThreadingHTTPServer(("127.0.0.1", recv_port), _Receiver)
        self.recv.events = []
        self.prof = ThreadingHTTPServer(("127.0.0.1", profile_port), _Profile)
        self.prof.profile = PROFILE_TEMPLATE.replace("{webhook_port}", str(recv_port))
        self._threads = []
    def __enter__(self):
        for srv in (self.recv, self.prof):
            t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
            self._threads.append(t)
        return self
    def __exit__(self, *a):
        self.recv.shutdown(); self.prof.shutdown()
    @property
    def profile_url(self):
        return f"http://127.0.0.1:{self.profile_port}/profiles/shopping-agent.json"
    def wait_events(self, n=1, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end and len(self.recv.events) < n:
            time.sleep(0.15)
        return list(self.recv.events)


# ==== 2026-04-08 order-event harness (WEBHOOK/EVENTS area) =====================
# At 2026-04-08 the webhook payload is the FULL order entity (rest.openapi.json
# webhooks.orderEvent: requestBody schema = order) and the delivery request is
# RFC 9421-signed (order.md "Webhook Signature Verification"). The suite IS the
# receiving platform, so the receiver captures everything a verifying platform
# needs: raw body bytes (base64) + all request headers + method/path/query/
# authority — enough to recompute Content-Digest and the signature base.
#
# Both servers bind port 0 (OS-assigned): checks can run in parallel workers
# without port coordination. `fail_first=N` makes the receiver answer 500 to the
# first N deliveries (still recorded) so retry behavior (ORD-031) is observable.
# The webhook_url deliberately carries a QUERY STRING (platform-specific format,
# order.md) so @query must be a signed component (signatures.md, SIG-017).

class _Receiver0408(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
        path, _, query = self.path.partition("?")
        self.server.events.append({
            "method": "POST", "path": path, "query": query,
            "authority": self.headers.get("Host", ""),
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body_b64": base64.b64encode(raw).decode(), "payload": payload})
        if self.server.fail_remaining > 0:        # induced failure -> must retry
            self.server.fail_remaining -= 1
            self.send_response(500); self.send_header("Content-Length", "2")
            self.end_headers(); self.wfile.write(b"{}")
            return
        body = json.dumps({"ucp": {"version": "2026-04-08"}}).encode()
        self.send_response(200)                   # ack per order.md (2xx quickly)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

class _Profile0408(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.split("?")[0].rstrip("/").endswith("platform-profile.json"):
            body = self.server.profile.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

def platform_profile_0408(webhook_url, version="2026-04-08"):
    """The suite's PLATFORM profile document naming the receiver as webhook_url in
    the order capability's config (order.json $defs/platform_schema; order.md
    Webhook URL Configuration). Oracle-validated against ucp.json platform_schema
    in conformance/fixtures/merchant/selfcheck.py."""
    spec = f"https://ucp.dev/{version}/specification/shopping"
    return {
        "version": version,
        "services": {"dev.ucp.shopping": [
            {"version": version, "transport": "rest",
             "endpoint": "https://spck.dev/suite",
             "spec": spec,
             "schema": f"https://ucp.dev/{version}/services/shopping/openapi.json"}]},
        "capabilities": {
            "dev.ucp.shopping.checkout": [
                {"version": version, "spec": spec,
                 "schema": "https://ucp.dev/schemas/shopping/checkout.json"}],
            "dev.ucp.shopping.order": [
                {"version": version, "spec": spec,
                 "schema": "https://ucp.dev/schemas/shopping/order.json",
                 "config": {"webhook_url": webhook_url}}],
        },
        "payment_handlers": {"dev.spck.tokenpay": [
            {"id": "spck_tokenpay", "version": version,
             "spec": "https://spck.dev/fixture/handlers/tokenpay",
             "schema": "https://spck.dev/fixture/handlers/tokenpay/schema.json"}]},
    }

class Harness0408:
    """Receiver + platform-profile servers for 2026-04-08 order-event webhooks."""
    def __init__(self, fail_first=0):
        self.recv = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver0408)
        self.recv.events = []
        self.recv.fail_remaining = fail_first
        self.webhook_url = (f"http://127.0.0.1:{self.recv.server_address[1]}"
                            "/webhooks/ucp/orders?channel=spck-harness")
        self.prof = ThreadingHTTPServer(("127.0.0.1", 0), _Profile0408)
        self.prof.profile = json.dumps(platform_profile_0408(self.webhook_url))
        self._threads = []
    def __enter__(self):
        for srv in (self.recv, self.prof):
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            self._threads.append(t)
        return self
    def __exit__(self, *a):
        self.recv.shutdown(); self.prof.shutdown()
        self.recv.server_close(); self.prof.server_close()
    @property
    def profile_url(self):
        return (f"http://127.0.0.1:{self.prof.server_address[1]}"
                "/profiles/platform-profile.json")
    def wait_events(self, n=1, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end and len(self.recv.events) < n:
            time.sleep(0.15)
        return list(self.recv.events)


class _Profile0123(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.rstrip("/").endswith("shopping-agent.json"):
            body = self.server.profile.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

class Harness0123:
    """CAPTURE-FULL receiver + platform-profile servers for the 01-era webhook
    format (official shopping-agent-test.json template; the webhook_url lives in
    the platform profile's order capability config, same discovery as 04-08).
    Unlike the original WebhookHarness (payload-only, fixed ports), this captures
    raw body + headers on port 0 so the Request-Signature detached JWS
    (ORD-014/015) and retries (ORD-016) are verifiable."""
    def __init__(self, fail_first=0):
        self.recv = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver0408)
        self.recv.events = []
        self.recv.fail_remaining = fail_first
        self.prof = ThreadingHTTPServer(("127.0.0.1", 0), _Profile0123)
        self.prof.profile = PROFILE_TEMPLATE.replace(
            "{webhook_port}", str(self.recv.server_address[1]))
        self._threads = []
    def __enter__(self):
        for srv in (self.recv, self.prof):
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            self._threads.append(t)
        return self
    def __exit__(self, *a):
        self.recv.shutdown(); self.prof.shutdown()
        self.recv.server_close(); self.prof.server_close()
    @property
    def profile_url(self):
        return (f"http://127.0.0.1:{self.prof.server_address[1]}"
                "/profiles/shopping-agent.json")
    def wait_events(self, n=1, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end and len(self.recv.events) < n:
            time.sleep(0.15)
        return list(self.recv.events)
