#!/usr/bin/env python3
"""
reference_agent.py — a minimal, CONFORMANT UCP platform/agent client.

This is the agent-side analogue of the merchant fixture: the known-good implementation
the agent-conformance checks are validated against. Its behaviors ARE the platform/agent
obligations the reverse harness grades. Injecting a `defect` produces the "mutation
agents" the kill-rate loop needs (each agent check must PASS on the clean reference agent
and FAIL on its targeted defect).

Phase A: a skeleton that does discovery + create_checkout, records everything it did
(the session log the checks assert on), and supports defect injection. Richer behaviors
(RFC 9421 signing + verification, OAuth2/PKCE, iss/mix-up validation, escalation-follow)
are Phase B — each added as a method + a matching defect.

Stdlib only. This same client hardens into the real "find & buy" agent in Phase B'.
"""
import json, urllib.request, urllib.error, uuid

# The catalogue of injectable client-side defects (grows with coverage). Each becomes the
# `kill_mutation` a Phase-B agent check must catch.
DEFECTS = {
    None: "conformant reference agent (no defect)",
    "no_ucp_agent": "omit the required UCP-Agent header",
    # Phase B: "skip_sig_verify", "skip_iss_validation", "reuse_pkce", "ignore_escalation", ...
}


class ReferenceAgent:
    PROFILE = "https://spck.dev/agent"

    def __init__(self, server, defect=None):
        assert defect in DEFECTS, f"unknown defect {defect!r}"
        self.server = server.rstrip("/")
        self.defect = defect
        self.log = []          # [{op, request:{method,path,headers,body}, response:{...}}]

    # --- client obligations (each maps to agent-side spec rows) ---
    def _headers(self, idem=None):
        h = {"Content-Type": "application/json"}
        # DISC-006 / CART-024: a conformant platform sends UCP-Agent (profile) on every request.
        if self.defect != "no_ucp_agent":
            h["UCP-Agent"] = f'profile="{self.PROFILE}"'
        h["request-signature"] = "test"     # placeholder; real RFC 9421 signing = Phase B
        h["request-id"] = str(uuid.uuid4())
        h["idempotency-key"] = idem or str(uuid.uuid4())
        return h

    def _send(self, op, method, path, body=None, headers=None):
        url = self.server + path
        h = headers if headers is not None else self._headers()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        entry = {"op": op, "request": {"method": method, "path": path, "headers": dict(h),
                                       "body": body}}
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                entry["response"] = {"status": r.status,
                                     "headers": {k.lower(): v for k, v in r.headers.items()},
                                     "body": json.loads(r.read().decode("utf-8", "replace"))}
        except urllib.error.HTTPError as e:
            entry["response"] = {"status": e.code, "body": None, "error": True}
        except Exception as e:
            entry["response"] = {"status": 0, "body": None, "error": str(e)}
        self.log.append(entry)
        return entry["response"]

    def discover(self):
        return self._send("discover", "GET", "/.well-known/ucp")

    def create_checkout(self, product_id="teapot_ceramic"):
        body = {"id": str(uuid.uuid4()), "currency": "USD",
                "line_items": [{"id": "li_1", "quantity": 1,
                                "item": {"id": product_id, "price": 1000}, "totals": []}],
                "payment": {"instruments": [], "handlers": []}, "status": "incomplete",
                "ucp": {"version": "2026-04-08"}, "totals": [], "links": []}
        return self._send("create_checkout", "POST", "/checkout-sessions", body)

    def run_flow(self, product_id="teapot_ceramic"):
        """Drive a basic conformant shopping flow; return the session log for grading."""
        self.discover()
        self.create_checkout(product_id)
        return self.log


if __name__ == "__main__":
    import sys
    srv = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8184"
    ag = ReferenceAgent(srv)
    log = ag.run_flow()
    print(f"reference agent ran {len(log)} ops against {srv}: "
          + ", ".join(f"{e['op']}->{e['response']['status']}" for e in log))
