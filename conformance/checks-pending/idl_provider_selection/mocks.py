#!/usr/bin/env python3
"""
mocks.py: the three loopback actors of the provider selection reference test system
(ucp #667 Gap 2). Stdlib only; signing via conformance/common/crypto (ES256).

  HonestIdP  : the IdP the platform is registered with. RFC 8414 metadata whose issuer
               is its own base URL, an RFC 8693 token exchange endpoint that checks client
               auth and the subject token it minted, a JWKS endpoint, a signed JWT grant.
  HostileAS  : an authorization server the business controls. Same shape; its request
               log is THE ORACLE: any request here is a leak of the platform's egress,
               any POST /token here carries the subject_token and client credential.
  Business   : serves a UCP profile whose config.providers is chosen per scenario, its
               own RFC 8414 metadata (the direct OAuth fallback path the platform starts
               when nothing matches) and a jwt-bearer token endpoint that enforces the
               closed allowlist (IDL-071) and verifies the grant signature.
"""
import json, os, sys, threading, time, urllib.parse, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import crypto   # noqa: E402

WELL_KNOWN = "/.well-known/oauth-authorization-server"
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
JWT_TYPE = "urn:ietf:params:oauth:token-type:jwt"


class Recorder:
    def __init__(self):
        self.requests, self._lock = [], threading.Lock()

    def add(self, entry):
        with self._lock:
            self.requests.append(entry)

    def posts(self, path=None):
        return [r for r in self.requests if r["method"] == "POST" and (path is None or r["path"] == path)]


def _b64json(s):
    return json.loads(crypto.b64url_decode(s))


class _Actor:
    """Base: an HTTP actor bound to an ephemeral loopback port. `issuer` is its base URL."""
    kind = "actor"

    def __init__(self, seed):
        self.recorder = Recorder()
        self.d, self.Q = crypto.keypair(seed)
        self.kid = self.kind + "-key-1"
        self.issuer = None
        self._srv = None

    def start(self):
        actor = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                actor.recorder.add({"method": "GET", "path": self.path})
                self._reply(*actor.get(self.path))

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
                auth = self.headers.get("Authorization")
                actor.recorder.add({"method": "POST", "path": self.path, "form": form, "authorization": auth})
                self._reply(*actor.post(self.path, form, auth))

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.issuer = "http://127.0.0.1:%d" % self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    def metadata(self):
        return {"issuer": self.issuer, "token_endpoint": self.issuer + "/token",
                "authorization_endpoint": self.issuer + "/authorize",
                "jwks_uri": self.issuer + "/jwks",
                "grant_types_supported": [TOKEN_EXCHANGE, "authorization_code"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic"]}

    def jwks(self):
        return {"keys": [crypto.jwk_from_pub(self.kid, self.Q)]}

    def grant(self, sub, aud):
        now = int(time.time())
        payload = {"iss": self.issuer, "sub": sub, "aud": aud, "jti": uuid.uuid4().hex,
                   "iat": now, "exp": now + 60, "email": sub + "@example.test"}
        return crypto.jws_compact_sign({"typ": "JWT"}, json.dumps(payload).encode(), self.d, self.kid)

    def get(self, path):
        if path == WELL_KNOWN:
            return 200, self.metadata()
        if path == "/jwks":
            return 200, self.jwks()
        return 404, {"error": "not_found"}


class HonestIdP(_Actor):
    kind = "honest-idp"

    def __init__(self):
        super().__init__(b"spck-pending-honest-idp-2026")
        self.clients = {}          # client_id -> client_secret (the platform's registration)
        self.subject_tokens = {}   # access token -> user

    def register(self, client_id, secret):
        self.clients[client_id] = secret

    def mint(self, user):
        tok = "idp-access-token-for-" + user
        self.subject_tokens[tok] = user
        return tok

    def post(self, path, form, auth):
        if path != "/token":
            return 404, {"error": "not_found"}
        cid, _, sec = crypto_basic(auth)
        if self.clients.get(cid) != sec:
            return 401, {"error": "invalid_client"}
        if form.get("grant_type") != TOKEN_EXCHANGE or form.get("subject_token") not in self.subject_tokens:
            return 400, {"error": "invalid_grant"}
        aud = form.get("audience") or form.get("resource")
        if not aud:
            return 400, {"error": "invalid_request"}
        user = self.subject_tokens[form["subject_token"]]
        return 200, {"access_token": self.grant(user, aud), "issued_token_type": JWT_TYPE,
                     "token_type": "N_A", "expires_in": 60}


class HostileAS(_Actor):
    """Accepts anything and answers plausibly so a naive platform proceeds; logs all."""
    kind = "hostile-as"

    def __init__(self):
        super().__init__(b"spck-pending-hostile-as-2026")

    def post(self, path, form, auth):
        if path != "/token":
            return 404, {"error": "not_found"}
        return 200, {"access_token": self.grant("victim", form.get("audience", "")),
                     "issued_token_type": JWT_TYPE, "token_type": "N_A", "expires_in": 60}


def crypto_basic(auth):
    import base64
    if not auth or not auth.startswith("Basic "):
        return None, None, None
    try:
        raw = base64.b64decode(auth[6:]).decode()
        cid, sec = raw.split(":", 1)
        return cid, ":", sec
    except Exception:
        return None, None, None


class Business(_Actor):
    """A business whose profile lists config.providers chosen per scenario. Its jwt-bearer
    token endpoint enforces the closed allowlist (iss must match a listed auth_url, IDL-071)
    and verifies the grant signature against <iss>/jwks."""
    kind = "business"
    VERSION = "2026-08-25"

    def __init__(self):
        super().__init__(b"spck-pending-business-2026")
        self.providers = {}

    def profile(self):
        return {"ucp": {"version": self.VERSION, "services": {},
                "capabilities": {"dev.ucp.common.identity_linking": [{
                    "version": self.VERSION,
                    "spec": "https://ucp.dev/%s/specification/common/identity-linking/" % self.VERSION,
                    "schema": "https://ucp.dev/%s/schemas/common/identity_linking.json" % self.VERSION,
                    "config": {"providers": self.providers,
                               "scopes": {"dev.ucp.shopping.order:read": {}}}}]},
                "payment_handlers": {}}}

    def listed_auth_urls(self):
        return {e["auth_url"] for entries in self.providers.values() for e in entries
                if e.get("type") == "oauth2" and "auth_url" in e}

    def get(self, path):
        if path == "/.well-known/ucp":
            return 200, self.profile()
        return super().get(path)

    def post(self, path, form, auth):
        if path != "/token":
            return 404, {"error": "not_found"}
        if form.get("grant_type") != JWT_BEARER or not form.get("assertion"):
            return 400, {"error": "unsupported_grant_type"}
        try:
            payload = _b64json(form["assertion"].split(".")[1])
        except Exception:
            return 400, {"error": "invalid_grant", "error_description": "malformed assertion"}
        iss = payload.get("iss")
        if iss not in self.listed_auth_urls():
            return 400, {"error": "invalid_grant", "error_description": "unrecognized provider"}
        try:
            with urllib.request.urlopen(iss.rstrip("/") + "/jwks", timeout=5) as r:
                jwks = json.loads(r.read().decode())
            Q = crypto.pub_from_jwk(jwks["keys"][0])
        except Exception:
            return 400, {"error": "invalid_grant", "error_description": "jwks unavailable"}
        if crypto.jws_compact_verify(form["assertion"], Q) is None:
            return 400, {"error": "invalid_grant", "error_description": "bad signature"}
        if payload.get("aud") != self.issuer:
            return 400, {"error": "invalid_grant", "error_description": "aud mismatch"}
        return 200, {"access_token": "business-token-" + uuid.uuid4().hex[:8], "token_type": "Bearer",
                     "expires_in": 3600, "scope": form.get("scope", "")}


def scenario_providers(name, honest_issuer, hostile_issuer, familiar_key="app.example.login"):
    """The business profile variants. `familiar_key` is the key a key matching platform
    associates with the honest IdP; every hostile entry sits under it."""
    honest = {"type": "oauth2", "auth_url": honest_issuer}
    hostile = {"type": "oauth2", "auth_url": hostile_issuer, "required_claims": ["email"]}
    if name == "hostile_first":
        return {familiar_key: [hostile, honest]}
    if name == "familiar_key_hostile_only":
        return {familiar_key: [hostile], "org.unknown.x": [honest]}
    if name == "lookalike_slash":
        return {familiar_key: [{"type": "oauth2", "auth_url": honest_issuer + "/"}]}
    if name == "nothing_matches":
        return {familiar_key: [hostile], "org.unknown.x": [dict(hostile)]}
    raise KeyError(name)


SCENARIOS = ("hostile_first", "familiar_key_hostile_only", "lookalike_slash", "nothing_matches")
