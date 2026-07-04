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
    # OAuth flow shape
    "implicit_grant": "use the implicit grant (response_type=token) not the code flow (IDL-010)",
    "unadvertised_auth_method": "authenticate the token request with an unadvertised method (IDL-002)",
    "request_scope_superset": "request a scope outside the advertised config.scopes (IDL-034)",
    "abort_on_future_config": "abort linking on an unrecognized future config field (IDL-057)",
    "oidc_fallback_no_abort": "do NOT abort when the OIDC-fallback discovery fetch fails (IDL-062)",
    "normalize_iss": "normalize (strip trailing slash) before comparing iss to issuer (IDL-061)",
    "skip_revocation": "unlink locally but do NOT revoke tokens at the revocation endpoint (IDL-014/055)",
    "adopt_prebaked_authz": "adopt the pre-baked OAuth request from continue_url instead of own (IDL-044)",
    "reinit_fresh_link": "on insufficient_scope, re-request the FULL scope set not just the missing (IDL-048)",
    "follow_error_description": "authorize at a decoy endpoint named in error_description prose (IDL-051)",
    # checkout completion safety
    "complete_on_mismatch": "autonomously complete a checkout with mismatched totals (CHK-055/TOT-010)",
    "use_unavailable_instrument": "pay with an instrument type not in available_instruments (PAY-009)",
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
        self.link_scopes = []        # config.scopes from the identity_linking capability
        self.unknown_config_fields = []   # unrecognized future config fields (IDL-057)
        self.issued_tokens = []      # access tokens minted this session (to revoke on unlink)
        self.current_token = None    # the Bearer token currently held
        self.current_granted = set()  # the scope set granted to current_token (IDL-048)

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
        # dev.ucp.common.identity_linking: capture config.scopes (the derived scope set) and any
        # unrecognized future config fields (IDL-034 / IDL-057).
        il = (prof.get("capabilities") or {}).get("dev.ucp.common.identity_linking") or [{}]
        cfg = (il[0] or {}).get("config") or {}
        self.link_scopes = list((cfg.get("scopes") or {}).keys())
        self.unknown_config_fields = [k for k in cfg if k != "scopes"]
        self.log[-1]["unknown_config_fields"] = list(self.unknown_config_fields)
        return r

    def _derive_link_scopes(self):
        """IDL-034: the derived scope set = the config.scopes keys (a conformant platform MAY
        request a subset; it MUST NOT request a superset). The request_scope_superset defect
        appends a scope the business never advertised."""
        scopes = list(self.link_scopes)
        if self.defect == "request_scope_superset":
            scopes.append("dev.ucp.shopping.order:admin")   # not in config.scopes
        return scopes

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
        """OIDC Discovery fallback (step 2). IDL-062: on any non-2xx/error the platform MUST
        abort the identity-linking process (do not proceed to authorization). The
        oidc_fallback_no_abort defect skips that abort and proceeds anyway."""
        r = self._send("oidc_discovery", "GET", "/.well-known/openid-configuration")
        st = r.get("status") or 0
        if 200 <= st < 300:
            self.identity = r.get("body") or self.identity
            return r
        if self.defect != "oidc_fallback_no_abort":
            self.log[-1]["aborted"] = True
        return None

    def oauth_authorize(self, scope="openid", op="authorize", prebaked=None, authorize_path=None):
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
        redirect_uri = self.PROFILE + "/cb"                # IDL-044: a redirect_uri WE own
        # IDL-044: a conformant platform constructs its OWN authorization request from the
        # challenge + discovered metadata. adopt_prebaked_authz instead copies the business's
        # pre-baked (attacker-controlled) redirect_uri / state / code_challenge.
        if prebaked and self.defect == "adopt_prebaked_authz":
            redirect_uri = prebaked.get("redirect_uri") or redirect_uri
            sent_state = prebaked.get("state") or sent_state
            challenge = prebaked.get("code_challenge") or challenge
        # IDL-010: the account-linking mechanism MUST be the OAuth 2.0 Authorization Code flow
        # (response_type=code). The implicit_grant defect requests the RFC 6749 §4.2 implicit
        # grant (response_type=token) instead.
        rt = "token" if self.defect == "implicit_grant" else "code"
        params = {"response_type": rt, "client_id": "spck-agent",
                  "redirect_uri": redirect_uri, "state": sent_state,
                  "scope": scope}
        if self.defect != "no_pkce":                       # IDL-011: PKCE S256 required
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
        # IDL-051: authorize at the DISCOVERED authorization_endpoint. authorize_path is an
        # override the follow_error_description defect uses to (wrongly) target a decoy from a
        # hostile error_description.
        ep = authorize_path or (urllib.parse.urlparse(ae).path or "/oauth2/authorize")
        resp = self._send(op, "GET", ep + "?" + urllib.parse.urlencode(params))
        b = resp.get("body") or {}
        entry = self.log[-1]
        if self.defect != "skip_state_validation":         # IDL-035: state must match sent value
            entry["state_validated"] = (b.get("state") == sent_state)
            if not entry["state_validated"]:
                entry["rejected"] = True                   # discard a mismatched state (CSRF)
        if self.defect != "skip_iss_validation":           # IDL-012/IDL-061: validate iss
            got, want = b.get("iss"), ident.get("issuer")
            if self.defect == "normalize_iss":             # IDL-061 violation: normalize first
                entry["iss_validated"] = ((got or "").rstrip("/") == (want or "").rstrip("/"))
            else:
                entry["iss_validated"] = (got == want)     # byte-for-byte (RFC 9207)
            if not entry["iss_validated"]:
                entry["rejected"] = True                   # discard a mismatched issuer
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
        if self.defect == "unadvertised_auth_method":      # IDL-002: method not in advertised set
            # authenticate with private_key_jwt (client_assertion) — a method the business's
            # token_endpoint_auth_methods_supported does NOT advertise (it lists none/basic/post)
            body["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            body["client_assertion"] = "eyJhbGciOiJFUzI1NiJ9.e30.sig"
        resp = self._send("token", "POST", "/oauth2/token", body)
        rb = resp.get("body") or {}
        tok = rb.get("access_token")
        if tok:
            self.issued_tokens.append(tok)
            self.current_token = tok
            # IDL-048: track the scope the AS granted this token (it may UNION prior grants
            # under incremental authorization), so we can request only what is still missing.
            self.current_granted = set((rb.get("scope") or "").split())
        return tok

    def unlink(self):
        """IDL-014 / IDL-055: when the user unlinks their account, the platform MUST call the
        business's RFC 7009 revocation endpoint for each identity token it holds. The
        skip_revocation defect performs the local unlink but leaves the tokens live."""
        re_ep = (self.identity or {}).get("revocation_endpoint")
        if not re_ep or self.defect == "skip_revocation":
            return
        path = urllib.parse.urlparse(re_ep).path or "/oauth2/revoke"
        for tok in self.issued_tokens:
            self._send("revoke", "POST", path,
                       {"token": tok, "token_type_hint": "access_token"})

    def fetch_gated(self, path="/orders", method="GET", max_rounds=5):
        """Access a user-authenticated (identity-gated) operation, driving the RFC 6750 §3
        WWW-Authenticate: Bearer flow. On a 401 `identity_required` (no scope) the platform
        derives an initial scope; on a 403 `insufficient_scope` it MUST extract the challenge
        scope (IDL-009) and re-authorize. IDL-048: it MUST request only the MISSING scope(s)
        (challenge − already-granted), preserving prior grants via incremental authorization —
        NOT re-initiate a fresh flow requesting the full set. The reinit_fresh_link defect
        re-lists already-granted scopes. Each challenge MUST be processed (IDL-008); each retry
        MUST carry Authorization: Bearer (IDL-007). Presents any token already held so a second
        (higher-privilege) operation upgrades it incrementally rather than starting over."""
        op = "fetch_gated"
        for _ in range(max_rounds):
            h = self._headers()
            if self.current_token and self.defect != "no_bearer_header":
                h["Authorization"] = f"Bearer {self.current_token}"   # IDL-007
            resp = self._send(op, method, path, headers=h)
            if resp.get("status") not in (401, 403):
                return resp                                # success (or unhandled) -> done
            if self.defect == "no_bearer_retry":           # IDL-008: ignore the challenge
                return resp
            wa = (resp.get("headers") or {}).get("www-authenticate", "")
            m = re.search(r'scope="([^"]*)"', wa)
            if m:                                          # IDL-009: scope from the challenge
                challenge = set(m.group(1).split())
                if self.defect == "reinit_fresh_link":     # IDL-048: re-list already-granted
                    request = challenge
                else:                                      # incremental: only the missing scope
                    request = challenge - self.current_granted
                if self.defect == "ignore_challenge_scope":
                    request = {"dev.ucp.shopping.unrelated:read"}
                scope = " ".join(sorted(request)) or " ".join(sorted(challenge))
            else:
                scope = "openid"                           # 401 has no scope -> initial default
            # IDL-044: a pre-baked OAuth request may ride in continue_url; capture it so the
            # defect can (wrongly) adopt it — the conformant agent constructs its own instead.
            prebaked = None
            cu = (resp.get("body") or {}).get("continue_url")
            if cu:
                pq = urllib.parse.parse_qs(urllib.parse.urlparse(cu).query)
                prebaked = {k: (pq.get(k) or [None])[0]
                            for k in ("redirect_uri", "state", "code_challenge")}
            # IDL-051: a conformant agent drives control-flow from the STRUCTURED scope/error,
            # never from error_description prose. follow_error_description (wrongly) extracts a
            # decoy authorization endpoint from error_description and authorizes there.
            authorize_path = None
            if self.defect == "follow_error_description":
                md = re.search(r'error_description="([^"]*)"', wa)
                um = re.search(r'https?://[^\s"]+', md.group(1)) if md else None
                if um:
                    authorize_path = urllib.parse.urlparse(um.group(0)).path
            code = self.oauth_authorize(scope=scope, op="authorize_gated", prebaked=prebaked,
                                        authorize_path=authorize_path)
            self.oauth_token(code, scope)                  # updates current_token / current_granted
            op = "fetch_gated_retry"
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

    def complete(self, sid, token="escalate_token", instrument_type="card"):
        """Complete a checkout with a payment credential (default: the 3DS/SCA soft-decline
        token, which the sandbox answers with requires_escalation + a continue_url). PAY-009:
        the credential's instrument `type` MUST be one the checkout's available_instruments
        offered — a conformant agent pays only with an authoritative type."""
        body = {"payment": {"instruments": [
            {"type": instrument_type, "credential": {"token": token}}]}}
        return self._send("complete", "POST", f"/checkout-sessions/{sid}/complete", body)

    @staticmethod
    def _available_instrument_types(checkout_body):
        """The authoritative instrument types = the union across every payment handler's
        available_instruments (ucp.payment_handlers.<name>[].available_instruments)."""
        handlers = ((checkout_body or {}).get("ucp") or {}).get("payment_handlers") or {}
        types = []
        for entries in handlers.values():
            for h in (entries or []):
                for i in (h.get("available_instruments") or []):
                    if isinstance(i, dict) and i.get("type"):
                        types.append(i["type"])
        return types

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
        # IDL-057: when config carries unrecognized future fields, a conformant platform MUST
        # IGNORE them and proceed with OAuth 2.0 + RFC 8414 discovery. The abort_on_future_config
        # defect instead chokes and stops (forward-incompatible).
        if self.unknown_config_fields and self.defect == "abort_on_future_config":
            self.log[-1]["aborted_on_unknown_config"] = True
            return self.log
        self.discover_oauth_metadata()     # RFC 8414 AS-metadata discovery (issuer/abort rules)
        # IDL-031/033/062: a discovery abort (non-404 error, or an OIDC-fallback failure) or a
        # rejected issuer MUST abort the identity-linking process — do not proceed to
        # authorization with unverified metadata.
        disc = next((e for e in reversed(self.log)
                     if e["op"] in ("as_discovery", "oidc_discovery")), None)
        if disc and (disc.get("aborted") or disc.get("rejected")):
            return self.log
        # IDL-034: request exactly the derived config.scopes set (no superset) on the linking leg.
        self.oauth_authorize(scope=" ".join(self._derive_link_scopes()) or "openid")
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
            # PAY-008/009/010: available_instruments is authoritative — pay only with an
            # offered type. The defect pays with a type the business never offered.
            avail = self._available_instrument_types(c.get("body"))
            itype = "crypto_wallet" if self.defect == "use_unavailable_instrument" \
                else (avail[0] if avail else "card")
            resp = self.complete(sid, instrument_type=itype)
            b = resp.get("body") or {}
            # CHK-008: on requires_escalation, a conformant platform follows continue_url.
            if b.get("status") == "requires_escalation" and self.defect != "ignore_escalation":
                cu = b.get("continue_url")
                if cu:
                    self.follow_continue_url(cu)
        # A user-authenticated operation (order history): ungated except in the
        # auth_challenge scenario, where it exercises the WWW-Authenticate: Bearer flow.
        self.fetch_gated()
        # IDL-048: a HIGHER-privilege operation (order cancel) — in the incremental_scope
        # scenario it 403s a superset challenge (read+manage) while the agent already holds
        # read, exercising incremental authorization (request only the missing `manage`). In
        # other scenarios this endpoint 404s and is a no-op.
        self.fetch_gated("/orders/ord_hist/cancel", method="POST")
        # IDL-014/055: simulate the user unlinking — revoke every identity token at the
        # business's RFC 7009 revocation endpoint (a no-op when no token was minted).
        self.unlink()
        return self.log


if __name__ == "__main__":
    import sys
    srv = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8184"
    ag = ReferenceAgent(srv)
    log = ag.run_flow()
    print(f"reference agent ran {len(log)} ops against {srv}: "
          + ", ".join(f"{e['op']}->{e['response']['status']}" for e in log))
