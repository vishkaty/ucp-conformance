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
import base64, hashlib, json, os, re, sys, urllib.request, urllib.error, urllib.parse, uuid

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
    # request-signing defects — a SIGNING agent that produces a bad/incomplete signature.
    # (Not signing at all is allowed — SIG-026 is SHOULD — so there is no 'unsigned' defect.)
    "sign_corrupt": "sign, but the request signature does NOT verify (SIG-001)",
    "sign_omit_authority": "sign, but omit @authority from the covered components (SIG-014)",
    "sign_omit_digest": "sign, but omit content-digest/content-type from covered (SIG-015)",
    "sign_omit_idem": "sign, but omit idempotency-key from the covered components (SIG-016)",
    "ucp_agent_not_signed": "sign, but omit ucp-agent from the covered components (SIG-018)",
    # WWW-Authenticate: Bearer challenge handling (RFC 6750)
    "no_bearer_retry": "ignore the 401 WWW-Authenticate: Bearer challenge; do NOT retry (IDL-008)",
    "no_bearer_header": "retry the gated op WITHOUT an Authorization: Bearer header (IDL-007)",
    "ignore_challenge_scope": "do NOT derive the authz scope from the challenge (IDL-009)",
    # OAuth public-client + authorization-response hygiene
    "no_pkce_verifier": "omit the PKCE code_verifier from the token exchange (IDL-004)",
    "embed_client_secret": "embed a client_secret in the token request (public client) (IDL-005)",
    "skip_state_validation": "do NOT validate the authorization-response state (IDL-035)",
    # RFC 8414 authorization-server metadata discovery
    "normalize_issuer": "normalize (strip trailing slash) before the issuer match (IDL-033)",
    "oidc_fallthrough_on_error": "fall through to OIDC on a non-404 discovery error (IDL-031/032)",
    # checkout completion safety
    "complete_on_mismatch": "autonomously complete a checkout with mismatched totals (CHK-055/TOT-010)",
}

# request-signing defect -> the covered components it drops (sign_corrupt tampers the bytes)
_SIGN_OMIT = {
    "sign_omit_authority": {"@authority"},
    "sign_omit_digest": {"content-digest", "content-type"},
    "sign_omit_idem": {"idempotency-key"},
    "ucp_agent_not_signed": {"ucp-agent"},
}

# the reference agent's own RFC 9421 request-signing key (published in its profile)
_AGENT_SIG_KID = "spck-agent-sig-2026"
_AGENT_D, _AGENT_Q = crypto.keypair(b"spck-reference-agent-request-signing-key-2026")


class ReferenceAgent:
    PROFILE = "https://spck.dev/agent"

    def __init__(self, server, defect=None):
        assert defect in DEFECTS, f"unknown defect {defect!r}"
        self.server = server.rstrip("/")
        self.defect = defect
        self.log = []          # [{op, request:{...}, response:{...}, sig_verified?, rejected?}]
        self.jwks = []         # business signing keys, learned at discovery
        self.identity = {}     # OAuth2 identity metadata, learned at discovery
        self._pkce_verifier = None   # PKCE verifier from the last authorize (proof at /token)

    @staticmethod
    def signing_jwk():
        """The agent's public signing key (the sandbox/business fetches this to verify the
        agent's RFC 9421 request signatures)."""
        return crypto.jwk_from_pub(_AGENT_SIG_KID, _AGENT_Q)

    # --- client obligations (each maps to agent-side spec rows) ---
    def _headers(self, idem=None):
        h = {"Content-Type": "application/json"}
        # DISC-006 / CART-024: a conformant platform sends UCP-Agent (profile) on every request.
        if self.defect != "no_ucp_agent":
            h["UCP-Agent"] = f'profile="{self.PROFILE}"'
        h["request-id"] = str(uuid.uuid4())
        h["idempotency-key"] = idem or str(uuid.uuid4())
        return h

    def _sign_request(self, h, method, path, data):
        """Sign the outbound request with RFC 9421 ES256. When signing, the covered
        components MUST include @method/@authority/@path (SIG-014), content-digest+
        content-type for a body (SIG-015), idempotency-key for POST (SIG-016), and ucp-agent
        (SIG-018). The sign_* defects model a signing agent that drops a required component or
        emits a signature that does not verify."""
        authority = urllib.parse.urlparse(self.server).netloc
        sig = crypto.sign_request_headers(
            method, authority, path, data, _AGENT_D, _AGENT_SIG_KID,
            ucp_agent=h.get("UCP-Agent"), idem=h.get("idempotency-key"),
            omit=_SIGN_OMIT.get(self.defect, ()))
        if self.defect == "sign_corrupt":                  # SIG-001: signature must verify
            raw = base64.b64decode(sig["Signature"].split(":", 1)[1].rsplit(":", 1)[0])
            raw = bytes([raw[0] ^ 0xFF]) + raw[1:]
            sig["Signature"] = "sig1=:" + base64.b64encode(raw).decode() + ":"
        h.update(sig)

    def _send(self, op, method, path, body=None, headers=None):
        url = self.server + path
        h = headers if headers is not None else self._headers()
        data = json.dumps(body).encode() if body is not None else None
        self._sign_request(h, method, path, data)
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
            # A 4xx/5xx (e.g. the 401 WWW-Authenticate: Bearer challenge) still carries
            # headers + a body the agent must read (RFC 6750 §3) — capture them.
            raw = e.read() if hasattr(e, "read") else b""
            rhdrs = {k: v for k, v in e.headers.items()} if e.headers else {}
            entry["response"] = {
                "status": e.code, "error": True,
                "headers": {k.lower(): v for k, v in rhdrs.items()},
                "body": json.loads(raw.decode("utf-8", "replace") or "null") if raw else None}
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

    def discover_oauth_metadata(self):
        """RFC 8414 authorization-server metadata discovery (identity-linking.md L236-257):
        fetch {base}/.well-known/oauth-authorization-server. On 2xx the `issuer` MUST match the
        discovery base URI byte-for-byte, with NO normalization (IDL-033). On 404 fall back to
        OIDC discovery; on any OTHER non-2xx/error the platform MUST abort and MUST NOT proceed
        to the OIDC fallback (IDL-031/032)."""
        r = self._send("as_discovery", "GET", "/.well-known/oauth-authorization-server")
        entry = self.log[-1]
        st = r.get("status") or 0
        if 200 <= st < 300:
            meta = r.get("body") or {}
            issuer = meta.get("issuer")
            if self.defect == "normalize_issuer":
                matched = (issuer or "").rstrip("/") == self.server.rstrip("/")   # WRONG
            else:
                matched = (issuer == self.server)              # IDL-033: byte-for-byte
            entry["issuer_matched"] = matched
            if not matched:
                entry["rejected"] = True
                return None
            self.identity = meta                               # use the discovered metadata
            return meta
        if st == 404:
            return self.discover_oidc()                        # legitimate OIDC fallback
        entry["aborted"] = True                                # IDL-031/032: non-404 -> abort
        if self.defect == "oidc_fallthrough_on_error":
            return self.discover_oidc()                        # WRONG: silent fall-through
        return None

    def discover_oidc(self):
        return self._send("oidc_discovery", "GET", "/.well-known/openid-configuration")

    def oauth_authorize(self, scope="openid", op="authorize"):
        """Run an OAuth2 authorization-code request with PKCE S256 (IDL-011) and validate
        the `iss` in the authorization response to prevent Mix-Up (IDL-012, RFC 9207).
        Returns the authorization `code`. `scope` is carried on the request (IDL-009 derives
        it from a WWW-Authenticate challenge); `op` distinguishes the gated re-auth."""
        ident = self.identity or {}
        ae = ident.get("authorization_endpoint")
        if not ae:
            return None
        verifier = uuid.uuid4().hex + uuid.uuid4().hex
        self._pkce_verifier = verifier                     # kept to prove possession at /token
        challenge = crypto.b64url(hashlib.sha256(verifier.encode()).digest())
        sent_state = uuid.uuid4().hex
        params = {"response_type": "code", "client_id": "spck-agent",
                  "redirect_uri": self.PROFILE + "/cb", "state": sent_state,
                  "scope": scope}
        if self.defect != "no_pkce":                       # IDL-011: PKCE S256 required
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
        resp = self._send(op, "GET",
                          "/oauth2/authorize?" + urllib.parse.urlencode(params))
        b = resp.get("body") or {}
        entry = self.log[-1]
        if self.defect != "skip_state_validation":         # IDL-035: state must match sent value
            entry["state_validated"] = (b.get("state") == sent_state)
            if not entry["state_validated"]:
                entry["rejected"] = True                   # discard a mismatched state (CSRF)
        if self.defect != "skip_iss_validation":           # IDL-012: validate iss (Mix-Up)
            entry["iss_validated"] = (b.get("iss") == ident.get("issuer"))
            if not entry["iss_validated"]:
                entry["rejected"] = True                   # reject a mismatched issuer
        return b.get("code")

    def oauth_token(self, code, scope):
        """Exchange the authorization code for a Bearer access token. As a PUBLIC client the
        agent authenticates with PKCE (code_verifier, IDL-004) and MUST NOT embed a
        client_secret (IDL-005)."""
        if not (self.identity or {}).get("token_endpoint"):
            return None
        body = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": self.PROFILE + "/cb", "scope": scope}
        if self.defect != "no_pkce_verifier":              # IDL-004: PKCE proof-of-possession
            body["code_verifier"] = self._pkce_verifier
        if self.defect == "embed_client_secret":           # IDL-005 violation (public client)
            body["client_secret"] = "shhh-should-not-exist"
        resp = self._send("token", "POST", "/oauth2/token", body)
        return (resp.get("body") or {}).get("access_token")

    def fetch_gated(self, path="/orders", max_rounds=4):
        """Access a user-authenticated (identity-gated) operation, driving the RFC 6750 §3
        WWW-Authenticate: Bearer flow. On a 401 `identity_required` (no scope) the platform
        derives an initial scope; on a 403 `insufficient_scope` it MUST extract the challenge
        scope (IDL-009) and re-authorize incrementally. Each challenge MUST be processed
        (IDL-008) and each retry MUST carry Authorization: Bearer (IDL-007)."""
        resp = self._send("fetch_gated", "GET", path)      # first request: no token
        for _ in range(max_rounds):
            if resp.get("status") not in (401, 403):
                return resp                                # success (or unhandled) -> done
            if self.defect == "no_bearer_retry":           # IDL-008: ignore the challenge
                return resp
            wa = (resp.get("headers") or {}).get("www-authenticate", "")
            m = re.search(r'scope="([^"]*)"', wa)          # IDL-009: scope from the challenge
            if m:
                scope = m.group(1)
                if self.defect == "ignore_challenge_scope":
                    scope = "dev.ucp.shopping.unrelated:read"   # not derived from the challenge
            else:
                scope = "openid"                           # 401 has no scope -> initial default
            code = self.oauth_authorize(scope=scope, op="authorize_gated")
            token = self.oauth_token(code, scope)
            h = self._headers()
            if self.defect != "no_bearer_header":          # IDL-007: Authorization: Bearer
                h["Authorization"] = f"Bearer {token}"
            resp = self._send("fetch_gated_retry", "GET", path, headers=h)
        return resp

    @staticmethod
    def _totals_consistent(checkout_body):
        """checkout.md L806: the sum of every non-`total` totals entry MUST equal the `total`
        entry's amount. Returns True when consistent (or unverifiable — no `total` entry)."""
        totals = (checkout_body or {}).get("totals") or []
        total_entry = next((t for t in totals if t.get("type") == "total"), None)
        if total_entry is None:
            return True
        s = sum(t.get("amount", 0) for t in totals if t.get("type") != "total")
        return s == total_entry.get("amount")

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
        self.discover_oauth_metadata()     # RFC 8414 AS-metadata discovery (issuer/abort rules)
        # IDL-031/033: a discovery abort (non-404 error) or a rejected issuer MUST abort the
        # identity-linking process — do not proceed to authorization with unverified metadata.
        as_entry = next((e for e in reversed(self.log) if e["op"] == "as_discovery"), None)
        if as_entry and (as_entry.get("aborted") or as_entry.get("rejected")):
            return self.log
        self.oauth_authorize()             # identity-linking (OAuth2 + PKCE + iss), if advertised
        c = self.create_checkout(product_id)
        if self.log[-1].get("rejected"):   # rejected a bad business signature -> stop here
            return self.log
        # CHK-055 / TOT-010: MUST NOT autonomously complete a checkout with mismatched totals
        # (SHOULD reject/escalate for buyer review). A conformant agent verifies the totals
        # arithmetic and refuses to complete when it does not reconcile.
        if not self._totals_consistent(c.get("body")) and self.defect != "complete_on_mismatch":
            self.log[-1]["totals_mismatch"] = True
            self.log[-1]["refused_completion"] = True
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
        # A user-authenticated operation (order history): ungated except in the
        # auth_challenge scenario, where it exercises the WWW-Authenticate: Bearer flow.
        self.fetch_gated()
        return self.log


if __name__ == "__main__":
    import sys
    srv = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8184"
    ag = ReferenceAgent(srv)
    log = ag.run_flow()
    print(f"reference agent ran {len(log)} ops against {srv}: "
          + ", ".join(f"{e['op']}->{e['response']['status']}" for e in log))
