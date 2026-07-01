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
import json, threading, time, pathlib, urllib.request, urllib.error
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
