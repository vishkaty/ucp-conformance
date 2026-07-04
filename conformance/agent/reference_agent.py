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
import hashlib, json, os, sys, urllib.request, urllib.error, urllib.parse, uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import crypto   # noqa: E402

# The catalogue of injectable client-side defects (grows with coverage). Each becomes the
# `kill_mutation` a Phase-B agent check must catch.
DEFECTS = {
    None: "conformant reference agent (no defect)",
    "no_ucp_agent": "omit the required UCP-Agent header",
    "ignore_escalation": "do NOT follow continue_url on requires_escalation",
    "skip_sig_verify": "do NOT verify the business's RFC 9421 response signature",
    "no_pkce": "omit PKCE (code_challenge / S256) from the authorization request",
    "skip_iss_validation": "do NOT validate the iss in the authorization response",
}


class ReferenceAgent:
    PROFILE = "https://spck.dev/agent"

    def __init__(self, server, defect=None):
        assert defect in DEFECTS, f"unknown defect {defect!r}"
        self.server = server.rstrip("/")
        self.defect = defect
        self.log = []          # [{op, request:{...}, response:{...}, sig_verified?, rejected?}]
        self.jwks = []         # business signing keys, learned at discovery
        self.identity = {}     # OAuth2 identity metadata, learned at discovery

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
                raw = r.read()
                rhdrs = {k: v for k, v in r.headers.items()}
                entry["response"] = {
                    "status": r.status,
                    "headers": {k.lower(): v for k, v in rhdrs.items()},
                    "body": json.loads(raw.decode("utf-8", "replace") or "null")}
                self._verify_sig(entry, r.status, raw, rhdrs)
        except urllib.error.HTTPError as e:
            entry["response"] = {"status": e.code, "body": None, "error": True}
        except Exception as e:
            entry["response"] = {"status": 0, "body": None, "error": str(e)}
        self.log.append(entry)
        return entry["response"]

    def _verify_sig(self, entry, status, raw, rhdrs):
        """SIG-002/SIG-036: an implementation MUST verify RFC 9421 (ES256) response
        signatures and reject with signature_invalid when ECDSA verification fails. A
        conformant agent rejects a response whose signature is missing/invalid. The
        skip_sig_verify defect omits this check entirely."""
        if entry["op"] == "discover" or self.defect == "skip_sig_verify" or not self.jwks:
            return
        ok, reason = crypto.verify_response(status, raw, rhdrs, self.jwks)
        entry["sig_verified"] = ok
        entry["sig_reason"] = reason
        if not ok:
            entry["rejected"] = True     # abort on a bad business signature

    def discover(self):
        r = self._send("discover", "GET", "/.well-known/ucp")
        prof = (r.get("body") or {}).get("ucp") or {}
        self.jwks = prof.get("signing_keys") or []
        self.identity = prof.get("identity") or {}
        return r

    def oauth_authorize(self):
        """Run an OAuth2 authorization-code request with PKCE S256 (IDL-011) and validate
        the `iss` in the authorization response to prevent Mix-Up (IDL-012, RFC 9207)."""
        ident = self.identity or {}
        ae = ident.get("authorization_endpoint")
        if not ae:
            return None
        verifier = uuid.uuid4().hex + uuid.uuid4().hex
        challenge = crypto.b64url(hashlib.sha256(verifier.encode()).digest())
        params = {"response_type": "code", "client_id": "spck-agent",
                  "redirect_uri": self.PROFILE + "/cb", "state": uuid.uuid4().hex,
                  "scope": "openid"}
        if self.defect != "no_pkce":                       # IDL-011: PKCE S256 required
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
        resp = self._send("authorize", "GET",
                          "/oauth2/authorize?" + urllib.parse.urlencode(params))
        b = resp.get("body") or {}
        if self.defect != "skip_iss_validation":           # IDL-012: validate iss (Mix-Up)
            entry = self.log[-1]
            entry["iss_validated"] = (b.get("iss") == ident.get("issuer"))
            if not entry["iss_validated"]:
                entry["rejected"] = True                   # reject a mismatched issuer
        return resp

    def create_checkout(self, product_id="teapot_ceramic"):
        body = {"id": str(uuid.uuid4()), "currency": "USD",
                "line_items": [{"id": "li_1", "quantity": 1,
                                "item": {"id": product_id, "price": 1000}, "totals": []}],
                "payment": {"instruments": [], "handlers": []}, "status": "incomplete",
                "ucp": {"version": "2026-04-08"}, "totals": [], "links": []}
        return self._send("create_checkout", "POST", "/checkout-sessions", body)

    def complete(self, sid, token="escalate_token"):
        """Complete a checkout with a payment credential (default: the 3DS/SCA soft-decline
        token, which the sandbox answers with requires_escalation + a continue_url)."""
        body = {"payment": {"instruments": [{"credential": {"token": token}}]}}
        return self._send("complete", "POST", f"/checkout-sessions/{sid}/complete", body)

    def follow_continue_url(self, url):
        """Open the business-provided continue_url (CHK-008: platform MUST use it on
        requires_escalation). Recorded as an op so the check can see the follow."""
        import urllib.request
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        entry = {"op": "follow_escalation", "request": {"method": "GET", "path": url,
                                                        "headers": dict(self._headers())}}
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                entry["response"] = {"status": r.status}
        except Exception as e:
            entry["response"] = {"status": 0, "error": str(e)}
        self.log.append(entry)
        return entry["response"]

    def run_flow(self, product_id="teapot_ceramic"):
        """Drive a conformant shopping flow (discover -> create -> complete -> handle
        escalation); return the session log for grading."""
        self.discover()
        self.oauth_authorize()             # identity-linking (OAuth2 + PKCE + iss), if advertised
        c = self.create_checkout(product_id)
        if self.log[-1].get("rejected"):   # rejected a bad business signature -> stop here
            return self.log
        sid = (c.get("body") or {}).get("id")
        if sid:
            resp = self.complete(sid)
            b = resp.get("body") or {}
            # CHK-008: on requires_escalation, a conformant platform follows continue_url.
            if b.get("status") == "requires_escalation" and self.defect != "ignore_escalation":
                cu = b.get("continue_url")
                if cu:
                    self.follow_continue_url(cu)
        return self.log


if __name__ == "__main__":
    import sys
    srv = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8184"
    ag = ReferenceAgent(srv)
    log = ag.run_flow()
    print(f"reference agent ran {len(log)} ops against {srv}: "
          + ", ".join(f"{e['op']}->{e['response']['status']}" for e in log))
