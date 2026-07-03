#!/usr/bin/env python3
"""
sandbox.py — the adversarial merchant the agent lane shops against.

A small, self-contained stdlib server that presents a merchant surface to a UCP
platform/agent AND can emit the controlled/adversarial stimuli the agent-conformance
checks need — an escalation with a continue_url IT controls (so the agent's follow is
observable and hermetic), and (Phase B.3) bad signatures / OAuth flows with a
configurable `iss`.

Run in-process by run_agent for the reference-gate (agent points here, we grade its
behavior via its own session log). The same server is what a REAL agent points at for
hosted verification later.

Conformant by default; adversarial behaviors are triggered by explicit inputs (e.g. the
`escalate_token` in a completion payment), so the default surface stays clean.
"""
import json, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager

ESCALATE_TOKEN = "escalate_token"


def _payment_tokens(body):
    insts = ((body or {}).get("payment") or {}).get("instruments") or []
    return [(i.get("credential") or {}).get("token") for i in insts if isinstance(i, dict)]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _base(self):
        host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
        return f"http://{host}"

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/.well-known/ucp":
            base = self._base()
            return self._send(200, {"ucp": {
                "version": "2026-04-08", "status": "ok",
                "services": {"dev.ucp.shopping": [
                    {"transport": "rest", "endpoint": base}]},
                "capabilities": {"dev.ucp.shopping.checkout": [{}]}}})
        if self.path.startswith("/3ds/"):
            # the escalation continue_url landing — reaching it means the agent FOLLOWED it
            self.server.observed.append(("follow", self.path))
            return self._send(200, {"escalation": "landing", "ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self._read()
        self.server.observed.append(("request", self.path, dict(self.headers)))
        if self.path == "/checkout-sessions":
            sid = "chk_" + uuid.uuid4().hex[:12]
            self.server.sessions[sid] = "incomplete"
            return self._send(201, {"ucp": {"version": "2026-04-08"}, "id": sid,
                                    "status": "incomplete", "currency": "USD",
                                    "line_items": body.get("line_items", []), "totals": []})
        if self.path.endswith("/complete") and self.path.startswith("/checkout-sessions/"):
            sid = self.path.split("/")[2]
            if ESCALATE_TOKEN in _payment_tokens(body):
                # 3DS/SCA soft-decline: requires_escalation + a continue_url WE serve, so
                # the conformant agent's follow lands back here (CHK-008).
                return self._send(200, {"ucp": {"version": "2026-04-08"}, "id": sid,
                                        "status": "requires_escalation",
                                        "continue_url": self._base() + f"/3ds/{sid}",
                                        "messages": [{"type": "error", "code": "requires_3ds"}]})
            oid = "ord_" + uuid.uuid4().hex[:12]
            return self._send(200, {"ucp": {"version": "2026-04-08"}, "id": sid,
                                    "status": "completed", "order": {"id": oid}})
        return self._send(404, {"error": "not found"})


@contextmanager
def serve():
    """Boot the sandbox on an ephemeral port; yield (base_url, server). `server.observed`
    records what the agent did (from the SERVER side, complementary to the agent log)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.observed = []
    httpd.sessions = {}
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    with serve() as (base, _srv):
        print("sandbox on", base)
        import time
        time.sleep(60)
