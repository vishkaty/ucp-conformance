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
import base64, json, os, sys, threading, urllib.parse, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import crypto   # noqa: E402

ESCALATE_TOKEN = "escalate_token"
ORDER_SCOPE = "dev.ucp.shopping.order:read"   # scope the gated op requires (IDL-037 pattern)

# the sandbox's own RFC 9421 response-signing key (published in its profile)
SIG_KID = "spck-sandbox-sig-2026"
_SIG_D, _SIG_Q = crypto.keypair(b"spck-agent-sandbox-signing-key-2026")


def _payment_tokens(body):
    insts = ((body or {}).get("payment") or {}).get("instruments") or []
    return [(i.get("credential") or {}).get("token") for i in insts if isinstance(i, dict)]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, obj, extra_headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for hn, hv in (extra_headers or {}).items():   # e.g. WWW-Authenticate (RFC 6750 §3)
            self.send_header(hn, hv)
        # RFC 9421 response signing (signatures.md). In the "bad_signature" scenario the
        # Signature is corrupted so a conformant agent MUST reject the response.
        sig = crypto.sign_response_headers(code, body, _SIG_D, SIG_KID)
        if self.server.scenario == "bad_signature":
            raw = base64.b64decode(sig["Signature"].split(":", 1)[1].rsplit(":", 1)[0])
            tampered = bytes([raw[0] ^ 0xFF]) + raw[1:]
            sig["Signature"] = "sig1=:" + base64.b64encode(tampered).decode() + ":"
        for hn, hv in sig.items():
            self.send_header(hn, hv)
        self.end_headers()
        self.wfile.write(body)

    def _base(self):
        host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
        return f"http://{host}"

    def _read_raw(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n)

    def _verify_request_sig(self, raw):
        """The business/receiver's obligation: verify the platform's RFC 9421 request
        signature (SIG-001/SIG-018). Enabled when the harness hands us the agent's key. An
        unsigned/invalid request is rejected 401 — so the agent's request signing is real."""
        jwks = getattr(self.server, "agent_jwks", None)
        if not jwks:
            return True
        authority = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
        ok, _reason = crypto.verify_request("POST", authority, self.path, raw,
                                            dict(self.headers), jwks)
        return ok

    def do_GET(self):
        base = self._base()
        if self.path == "/.well-known/ucp":
            return self._send(200, {"ucp": {
                "version": "2026-04-08", "status": "ok",
                "signing_keys": [crypto.jwk_from_pub(SIG_KID, _SIG_Q)],
                # OAuth2 identity-linking metadata (RFC 8414 subset) the agent uses to
                # run an authorization-code + PKCE flow and validate `iss` (RFC 9207).
                "identity": {"issuer": base,
                             "authorization_endpoint": base + "/oauth2/authorize",
                             "token_endpoint": base + "/oauth2/token",
                             "code_challenge_methods_supported": ["S256"],
                             "authorization_response_iss_parameter_supported": True},
                "services": {"dev.ucp.shopping": [
                    {"transport": "rest", "endpoint": base}]},
                "capabilities": {"dev.ucp.shopping.checkout": [{}]}}})
        if self.path == "/.well-known/oauth-authorization-server":
            # RFC 8414 authorization-server metadata discovery (identity-linking.md L236-257).
            # "discovery_error" returns a non-404 error (agent MUST abort, MUST NOT fall through
            # to OIDC); "bad_issuer" returns an issuer that does NOT byte-match the discovery
            # base URI (a trailing slash — the exact non-normalization case IDL-033 forbids).
            if self.server.scenario == "discovery_error":
                return self._send(500, {"error": "server_error"})
            issuer = base + "/" if self.server.scenario == "bad_issuer" else base
            return self._send(200, {
                "issuer": issuer,
                "authorization_endpoint": base + "/oauth2/authorize",
                "token_endpoint": base + "/oauth2/token",
                "code_challenge_methods_supported": ["S256"],
                # IDL-002: the platform MUST authenticate token requests with an advertised
                # method. A public client uses "none"; private_key_jwt is deliberately absent.
                "token_endpoint_auth_methods_supported": ["none", "client_secret_basic",
                                                          "client_secret_post"],
                "authorization_response_iss_parameter_supported": True})
        if self.path == "/.well-known/openid-configuration":
            # OIDC Discovery fallback (step 2) — only legitimately reached after a 404 above.
            return self._send(200, {"issuer": base,
                                    "authorization_endpoint": base + "/oauth2/authorize",
                                    "token_endpoint": base + "/oauth2/token"})
        if self.path.startswith("/3ds/"):
            # the escalation continue_url landing — reaching it means the agent FOLLOWED it
            self.server.observed.append(("follow", self.path))
            return self._send(200, {"escalation": "landing", "ok": True})
        if self.path.startswith("/oauth2/authorize"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self.server.observed.append(("authorize", q))
            # RFC 9207: the authorization response echoes `iss`. In "bad_iss" the auth
            # server returns a DIFFERENT issuer (Mix-Up) — a conformant agent MUST reject.
            iss = "https://mixup-attacker.example" if self.server.scenario == "bad_iss" else base
            # In "bad_state" it echoes a DIFFERENT state (CSRF/injection) — a conformant agent
            # MUST verify state matches the value it sent and discard on mismatch (IDL-035).
            state = ("tampered_" + uuid.uuid4().hex[:8] if self.server.scenario == "bad_state"
                     else (q.get("state") or [""])[0])
            return self._send(200, {"code": "authcode_" + uuid.uuid4().hex[:10],
                                    "state": state, "iss": iss})
        if self.path == "/orders":
            # A user-authenticated (identity-gated) operation. In "auth_challenge" it drives
            # the full RFC 6750 §3 flow (IDL-007/008/009): a no-token request gets a 401
            # `identity_required` challenge (no error/scope, per spec L448-450); a token
            # lacking the order scope gets a 403 `insufficient_scope` challenge carrying the
            # required scope (spec L516-519), which the platform must extract and re-authorize
            # incrementally. Other scenarios leave it ungated (a no-op).
            if self.server.scenario == "auth_challenge":
                auth = self.headers.get("Authorization") or ""
                tok = auth[7:] if auth.startswith("Bearer ") else None
                granted = self.server.tokens.get(tok) if tok else None
                if granted is None:                        # no valid token -> 401 (RFC 6750 §3.1)
                    wa = (f'Bearer realm="{base}", '
                          f'resource_metadata="{base}/.well-known/oauth-protected-resource"')
                    return self._send(401, {"messages": [{"type": "error",
                                            "code": "identity_required"}]},
                                      extra_headers={"WWW-Authenticate": wa})
                if ORDER_SCOPE not in granted.split():      # token, wrong scope -> 403
                    wa = (f'Bearer realm="{base}", error="insufficient_scope", '
                          f'scope="{ORDER_SCOPE}"')
                    return self._send(403, {"messages": [{"type": "error",
                                            "code": "insufficient_scope"}]},
                                      extra_headers={"WWW-Authenticate": wa})
            return self._send(200, {"orders": [{"id": "ord_history_1"}]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        raw = self._read_raw()
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        self.server.observed.append(("request", self.path, dict(self.headers)))
        # SIG-001/SIG-018: reject an unsigned/invalid platform request signature (401).
        if not self._verify_request_sig(raw):
            return self._send(401, {"error": "signature_invalid"})
        if self.path == "/oauth2/token":
            # OAuth2 token exchange (authorization_code). Issues a Bearer access token scoped
            # to exactly what was requested; /orders later checks the granted scope.
            tok = "tok_" + uuid.uuid4().hex[:16]
            scope = body.get("scope") or ""
            self.server.tokens[tok] = scope
            return self._send(200, {"access_token": tok, "token_type": "Bearer",
                                    "scope": scope, "expires_in": 3600})
        if self.path == "/checkout-sessions":
            sid = "chk_" + uuid.uuid4().hex[:12]
            self.server.sessions[sid] = "incomplete"
            # totals (checkout.md L800-813): sum of non-`total` entries MUST equal the `total`.
            # "mismatched_totals" returns a `total` that breaks that arithmetic so a conformant
            # agent MUST NOT autonomously complete (CHK-055 / TOT-010).
            if self.server.scenario == "mismatched_totals":
                totals = [{"type": "subtotal", "amount": 1000}, {"type": "tax", "amount": 100},
                          {"type": "total", "amount": 9999}]      # 1100 != 9999
            else:
                totals = [{"type": "subtotal", "amount": 1000}, {"type": "total", "amount": 1000}]
            return self._send(201, {
                # payment_handlers live in the ucp envelope (checkout-rest.md L83-104); each
                # handler's available_instruments is authoritative (PAY-009/010) — the platform
                # MUST pay only with a type offered here.
                "ucp": {"version": "2026-04-08", "payment_handlers": {
                    "com.spck.sandbox_pay": [{"id": "h1", "version": "2026-04-08",
                        "available_instruments": [{"type": "card"}, {"type": "digital_wallet"}],
                        "config": {}}]}},
                "id": sid, "status": "incomplete", "currency": "USD",
                "line_items": body.get("line_items", []), "totals": totals})
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
def serve(scenario="conformant", agent_jwks=None):
    """Boot the sandbox on an ephemeral port; yield (base_url, server). `scenario` selects
    the stimulus: "conformant" (default), "bad_signature" (responses carry an invalid RFC
    9421 signature, which a conformant agent MUST reject), "bad_iss" (OAuth Mix-Up),
    "bad_state" (the authorization response echoes a mismatched `state` — a conformant agent
    MUST discard it), "bad_issuer" (RFC 8414 metadata issuer doesn't byte-match the discovery
    base), "discovery_error" (RFC 8414 returns a non-404 error — MUST abort, no OIDC
    fall-through), "mismatched_totals" (the checkout's `total` breaks the totals arithmetic —
    a conformant agent MUST NOT autonomously complete), or "auth_challenge" (the gated /orders
    op emits a WWW-Authenticate: Bearer 401 until a valid Bearer token is presented).
    `agent_jwks`, when provided, makes the sandbox VERIFY the platform's request signatures
    (SIG-001/SIG-018) and 401 an unsigned/invalid one."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.observed = []
    httpd.sessions = {}
    httpd.tokens = {}                  # issued Bearer access token -> granted scope string
    httpd.scenario = scenario
    httpd.agent_jwks = agent_jwks
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
