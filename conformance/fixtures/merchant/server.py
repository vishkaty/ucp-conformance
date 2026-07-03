#!/usr/bin/env python3
"""
Controlled UCP merchant fixture (spec 2026-04-08) — our OWN golden for capabilities
the official samples don't implement (catalog search/lookup, cart, checkout lifecycle).

Why this exists: neither official sample (Python Flower Shop, Node.js) declares
`catalog` or `cart`, so those requirements can't be reference-gated against them.
This fixture fills that gap. It is NOT a substitute oracle for the whole spec — its
trustworthiness comes from an INDEPENDENT anchor: every profile/response it serves is
validated against the official `ucp.json` / catalog schemas by the `ucp-schema` Rust
validator (see conformance/fixtures/merchant/selfcheck.py). So a check that clean-passes
here is anchored to the official validator, not to our own checks (no circularity).

Dependency-free (stdlib http.server), so CI can boot it in one line.
    python3 conformance/fixtures/merchant/server.py --port 8184
"""
import json, argparse, uuid, threading, base64, hashlib, hmac, time, urllib.parse
import urllib.request
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The spec version this fixture serves. Switchable (--spec-version / set_version) so the
# SAME lifecycle can be reference-gated per version: catalog/cart/MCP exist only in
# 2026-04-08; checkout/order/discount are served in every supported version with the
# pinned per-version rendering (see checkout_body's sign-convention note).
# 2026-01-11 is an OLDER envelope generation (overview.md 2026-01-11 "Discovery"):
#   * the profile nests under a top-level `ucp` member and validates against
#     ucp.json $defs/discovery_profile (not business_schema);
#   * ucp.capabilities is an ARRAY of {name, version, spec, schema[, extends]};
#   * services is an OBJECT keyed by reverse-domain service name whose value carries
#     transport bindings as keys (rest/mcp/...), not a list of transport entries;
#   * checkout responses REQUIRE a root `payment` object (payment.json: handlers[]).
VERSION = "2026-04-08"
SUPPORTED_VERSIONS = ("2026-04-08", "2026-01-23", "2026-01-11")
# MUTANT switch (--no-verify-signatures): a deliberately-NON-VERIFYING merchant —
# accepts requests with tampered/garbage RFC 9421 signatures. Exists ONLY so the
# SIG-002 check's kill-proof gate can show the check DEVIATES on such a merchant
# (adversarial-review F6). Never enabled by run_suite's goldens.
VERIFY_SIGNATURES = True
# MUTANT switch (--no-webhooks): a merchant that NEVER sends order-event webhooks.
# Exists ONLY so the webhook checks' kill-proof gate can show they DEVIATE on such
# a merchant (WEBHOOK/EVENTS area). Never enabled by run_suite's goldens.
SEND_WEBHOOKS = True

def set_version(v):
    """Switch the spec version the fixture serves (also resets lifecycle state, so a
    gate run against one version never sees sessions minted under another)."""
    global VERSION
    if v not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported spec version: {v} (supported: {SUPPORTED_VERSIONS})")
    VERSION = v
    SESSIONS.clear(); ORDERS.clear(); IDEM.clear()
    OAUTH_CODES.clear(); OAUTH_TOKENS.clear(); OAUTH_REFRESH.clear()

# ---- controlled seed catalog (stable ids the checks rely on) -----------------
def _product(pid, vid, title, price, desc):
    money = {"amount": price, "currency": "USD"}
    return {
        "id": pid, "title": title, "handle": pid.replace("_", "-"),
        "description": {"text": desc},
        "price_range": {"min": money, "max": money},
        "variants": [{"id": vid, "title": "Default", "price": money,
                      "description": {"text": desc + " (default variant)"}}],
    }

def _cfg_variant(vid, price, color, size, available=True):
    return {"id": vid, "title": f"{color} / {size}",
            "price": {"amount": price, "currency": "USD"},
            "description": {"text": f"Glazed teacup — {color}, {size}."},
            "availability": {"available": available},
            "options": [{"name": "Color", "label": color},
                        {"name": "Size", "label": size}]}

# A CONFIGURABLE product (option axes Color x Size) so get_product's selection
# semantics (product.selected, variant narrowing — lookup.md Option Selection) can
# be exercised and reference-gated. Featured variant = first (Blue/Small).
CONFIGURABLE = {
    "id": "teacup_glaze", "title": "Glazed Teacup", "handle": "teacup-glaze",
    "description": {"text": "A hand-glazed teacup in two colors and two sizes."},
    "price_range": {"min": {"amount": 1500, "currency": "USD"},
                    "max": {"amount": 1900, "currency": "USD"}},
    "options": [{"name": "Color", "values": [{"label": "Blue"}, {"label": "Red"}]},
                {"name": "Size", "values": [{"label": "Small"}, {"label": "Large"}]}],
    "variants": [_cfg_variant("teacup_glaze_blue_s", 1500, "Blue", "Small"),
                 _cfg_variant("teacup_glaze_blue_l", 1900, "Blue", "Large"),
                 _cfg_variant("teacup_glaze_red_s", 1500, "Red", "Small"),
                 _cfg_variant("teacup_glaze_red_l", 1900, "Red", "Large",
                              available=False)],
}

PRODUCTS = [
    _product("teapot_ceramic", "teapot_ceramic_v1", "Ceramic Teapot", 2500,
             "A sturdy stoneware teapot."),
    _product("mug_enamel", "mug_enamel_v1", "Enamel Mug", 1200,
             "A camp-style enamel mug."),
    _product("kettle_copper", "kettle_copper_v1", "Copper Kettle", 6800,
             "A polished copper stovetop kettle."),
    _product("trivet_cork", "trivet_cork_v1", "Cork Trivet", 900,
             "A cork trivet, currently out of stock."),
    CONFIGURABLE,
    # a seeded long tail so a match-all search EXCEEDS the default page size of 10
    # (rest.md conformance: cursor-based pagination with default limit 10) — total
    # catalog = 13 products (keep catalog.paginated_total in the golden config equal)
] + [_product(f"tin_spice_{n}", f"tin_spice_{n}_v1", f"Spice Tin No. {i+1}", 700,
              f"A lidded spice tin — {n}.")
     for i, n in enumerate(["anise", "cardamom", "clove", "cumin",
                            "fennel", "mace", "nutmeg", "sumac"])]
BY_ID = {p["id"]: p for p in PRODUCTS}
BY_VARIANT = {v["id"]: p for p in PRODUCTS for v in p["variants"]}

# Per-item available stock. Deliberately small so an over-stock quantity (the VAL-002
# probe uses 10001) is always rejected, while normal 1-3 quantity flows succeed.
# trivet_cork is the SEEDED OUT-OF-STOCK item (drives VAL-001/VAL-006 negatives).
STOCK_DEFAULT = 10
STOCK = {"trivet_cork": 0}

def _stock(iid):
    pid = BY_VARIANT[iid]["id"] if iid in BY_VARIANT else iid
    return STOCK.get(pid, STOCK_DEFAULT)

# Payment tokens the fixture recognizes (mirrors the Flower Shop golden's seeded
# success/fail tokens so the same config pattern drives both goldens).
FAIL_TOKEN = "fail_token"

# ---- PAYMENT AREA block (04-08 grind) -----------------------------------------
# Seeded 3DS/SCA soft-decline token: completing with it returns HTTP 200 with
# status=requires_escalation + continue_url (checkout.json: continue_url "MUST be
# provided when status is requires_escalation") and a requires_buyer_input error
# message (overview.md Scenario B, code requires_3ds). A retried completion with a
# normal token then succeeds — the platform-side flow after opening continue_url.
ESCALATE_TOKEN = "escalate_token"

# Seeded payment handler declaration. Profiles advertise it in the payment_handlers
# registry (ucp.json business_schema REQUIRES the key; registry keyed by
# reverse-domain name per shopping/types/reverse_domain_name.json) and checkout
# responses echo the RESOLVED runtime declaration in ucp.payment_handlers
# (ucp.json response_checkout_schema REQUIRES it; entries per
# payment_handler.json $defs/response_schema — id required via $defs/base).
# The response variant narrows available_instruments (payment-handler-guide.md
# "Resolving available_instruments": the business intersects the declarations and
# the RESPONSE value is the authoritative, possibly narrower, set).
PAYMENT_HANDLER_KEY = "dev.spck.tokenpay"
PAYMENT_HANDLER_ID = "spck_tokenpay"

def payment_handlers_registry(response=False):
    """The payment_handlers registry, keyed by reverse-domain handler name.
    Valid at every SUPPORTED_VERSION: payment_handler.json $defs/base requires
    id (+ version via ucp.json entity) at both 04-08 and 01-23;
    available_instruments is schema-declared at 04-08 (minItems 1) and an
    allowed additional property at 01-23."""
    brands = ["visa"] if response else ["visa", "mastercard"]
    return {PAYMENT_HANDLER_KEY: [{
        "id": PAYMENT_HANDLER_ID, "version": VERSION,
        "spec": "https://spck.dev/fixture/handlers/tokenpay",
        "schema": "https://spck.dev/fixture/handlers/tokenpay/schema.json",
        "available_instruments": [{"type": "card", "constraints": {"brands": brands}}],
    }]}
# ---- end PAYMENT AREA block -----------------------------------------------------

# Seeded discount rules. Codes match case-insensitively (discount.md: "Case-insensitive").
#   order_pct/order_flat -> order-level (no allocations -> totals[type=discount])
#   item_pct             -> line-item level (allocations -> line discounts + items_discount)
DISCOUNT_CODES = {
    "10OFF":   {"title": "10% off your order", "kind": "order_pct", "value": 10},
    "TEA5":    {"title": "$5 off your order", "kind": "order_flat", "value": 500},
    "MUGLOVE": {"title": "20% off enamel mugs", "kind": "item_pct", "value": 20,
                "product": "mug_enamel"},
}
# Automatic (rule-based) discount: applied with automatic:true and NO code field.
AUTO_THRESHOLD, AUTO_AMOUNT, AUTO_TITLE = 5000, 500, "Bulk saver"

def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def merchant_authorization():
    """A JWS Detached Content signature (header..signature) whose protected header
    carries the alg/kid claims PAY-035 requires. The signature bytes are not
    cryptographically real — the MUST is about the header claims and detached shape,
    which is exactly what a conformance golden needs to exhibit."""
    header = _b64url(json.dumps({"alg": "ES256", "kid": "spck-fixture-2026"},
                                separators=(",", ":")).encode())
    return header + ".." + _b64url(b"fixture-detached-signature")

# ---- identity-linking (2026-04-08 rework; identity-linking.md) ----------------
# The user-authenticated scopes this business offers (identity_linking.json:
# config.scopes — each key is a '{capability}:{scope}' scope_token, each value a
# per-scope policy object; {} = "user auth required, nothing else"). Shared by the
# profile declaration AND the RFC 8414 metadata's scopes_supported, so the two
# artifacts stay consistent (the spec's scope-mismatch fail-fast story).
IDENTITY_SCOPES = {
    "dev.ucp.shopping.order:read": {},
    "dev.ucp.shopping.order:manage": {},
    "dev.ucp.shopping.checkout:manage": {
        "description": {"text": "Create, update, and complete checkout sessions "
                                "on the user's behalf."}},
}

# The 01-era identity-linking spec (2026-01-11/01-23 identity-linking.md, identical
# text) predates the 04-08 capability-scopes rework: businesses adhere to RFC 8414,
# authenticate platforms with client_id/client_secret via HTTP Basic
# (client_secret_basic), and support the standard scope ucp:scopes:checkout_session.
IDENTITY_SCOPES_01ERA = ("ucp:scopes:checkout_session",)

def oauth_scopes_supported():
    """The OAuth scope vocabulary the fixture's AS accepts, per serving version."""
    return sorted(IDENTITY_SCOPES) if VERSION == "2026-04-08" \
        else list(IDENTITY_SCOPES_01ERA)

def oauth_authorization_server_metadata(base):
    """RFC 8414 authorization server metadata, published at
    /.well-known/oauth-authorization-server in EVERY supported version
    (04-08 identity-linking.md For Businesses [IDL-016]; 01-era identity-linking.md
    'MUST adhere to RFC 8414' [IDL-006@01-era]).
    2026-04-08: scopes_supported MUST be populated [IDL-017];
    token_endpoint_auth_methods_supported MUST declare the accepted client auth
    methods [IDL-022]; authorization_response_iss_parameter_supported (RFC 9207)
    and code_challenge_methods_supported ["S256"] (PKCE) MUST both be present
    [IDL-058]. Only methods the token endpoint actually enforces are advertised
    (client_secret_basic + none — 'none' requires PKCE S256 per the spec).
    01-era: mirrors the 01-era spec's example (client_secret_basic only, no PKCE
    advertisements, scope ucp:scopes:checkout_session)."""
    md = {
        "issuer": base,
        "authorization_endpoint": base + "/oauth2/authorize",
        "token_endpoint": base + "/oauth2/token",
        "revocation_endpoint": base + "/oauth2/revoke",
        "scopes_supported": oauth_scopes_supported(),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "service_documentation": "https://spck.dev/fixture/docs/oauth2",
    }
    if VERSION == "2026-04-08":
        md.update({
            "jwks_uri": base + "/oauth2/jwks",
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "none"],
            "authorization_response_iss_parameter_supported": True,
        })
    else:
        md["token_endpoint_auth_methods_supported"] = ["client_secret_basic"]
    return md

def oauth_protected_resource_metadata(base):
    """RFC 9728 protected resource metadata at /.well-known/oauth-protected-resource
    (identity-linking.md For Businesses SHOULD [IDL-030]; referenced by the
    resource_metadata parameter in WWW-Authenticate challenges [IDL-043])."""
    return {"resource": base,
            "authorization_servers": [base],
            "scopes_supported": oauth_scopes_supported(),
            "bearer_methods_supported": ["header"]}

# ==== OAUTH area (identity-linking OAuth 2.0 server; identity-linking.md) ========
# A minimal, spec-true OAuth 2.0 authorization server: authorization-code grant with
# PKCE S256 (RFC 6749 §4.1 + RFC 7636), RFC 9207 iss on authorization responses,
# RFC 7009 revocation, and Bearer-gated resources emitting the spec's
# identity_required / insufficient_scope challenges (RFC 6750 §3). Stdlib only.
# The consent step is AUTO-GRANTED (this is a conformance golden, not a real AS).
# TEST CREDENTIALS ONLY — committed on purpose.
#
# MUTANT flags (each is a deliberately-broken merchant used ONLY by the
# validate_oauth_checks.py kill-proof gate; never enabled by run_suite goldens):
OAUTH_ENFORCE_PKCE = True     # --oauth-no-pkce: skip PKCE requirement/verification
OAUTH_GATE = True             # --oauth-no-gate: serve gated ops without identity
OAUTH_EXACT_REDIRECT = True   # --oauth-lax-redirect: skip redirect_uri matching
OAUTH_CLIENT_AUTH = True      # --oauth-no-client-auth: skip client authentication
OAUTH_CHALLENGE_ERROR = True  # --oauth-challenge-no-error: omit the challenge's
                              #   error="invalid_token"/"insufficient_scope" param
OAUTH_VALIDATE_TOKEN = True   # --oauth-accept-any-token: accept a PRESENT Bearer
                              #   token without checking it is known, unexpired, and
                              #   unrevoked (IDL-042 expired/revoked-token kill-proof)
# ORDER area (ORD-012): --require-order-auth makes GET /orders/{id} authenticate the
# request before returning order data. Default OFF so the DEFAULT golden keeps its
# existing UNauthenticated order checks sound; only the validate_order_auth gate
# (and its no-auth mutant, which is the default OFF mode) exercise this.
REQUIRE_ORDER_AUTH = False
# IDL-013 (01-era): --require-checkout-scope gates every 01-era checkout-session
# operation behind the ucp:scopes:checkout_session capability scope, so one token
# with that scope must unlock ALL of them. --checkout-scope-partial is the mutant:
# it makes ONE operation demand an extra per-operation scope the capability scope
# does not grant (the IDL-013 violation). Both default OFF (golden untouched).
REQUIRE_CHECKOUT_SCOPE = False
CHECKOUT_SCOPE_PARTIAL = False
# DISC-014 (01-era): the capability spec/schema URLs a conformant profile advertises
# point at the namespace authority (https://ucp.dev/...), which the DISC-014 live-URL
# check can only resolve over the NETWORK. To reference-gate that check HERMETICALLY,
# --local-spec-urls repoints every spec/schema/config_schema URL to a LOOPBACK path
# this fixture serves 200 for; --break-spec-url makes ONE of them 404 (the mutant).
# Neither mode is ever used by CONTROLLED_CONFIG/run_suite goldens (which advertise
# the real authority-origin URLs and are never fetched) — ONLY by the dedicated
# DISC-014 reference gate, so no gate ever depends on the network.
LOCAL_SPEC_URLS = False
BREAK_SPEC_URL = False
_LOCAL_SPEC_KEYS = ("spec", "schema", "config_schema")
_LOCAL_BROKEN_PATH = "/__localspec/BROKEN"

# Registered platform clients. The public client uses token_endpoint_auth_method
# 'none' + PKCE (RFC 8252 §8.5); the confidential one uses client_secret_basic.
# Loopback redirect registration is scheme+host+path — the PORT is ignored at
# match time (RFC 8252 §7.3; identity-linking.md loopback exception, IDL-021).
OAUTH_CLIENTS = {
    "spck-platform-public": {
        "auth_method": "none",
        "redirect_uris": ["https://platform.spck.dev/oauth/callback",
                          "http://127.0.0.1/oauth/cb"]},
    "spck-platform-confidential": {
        "auth_method": "client_secret_basic",
        "secret": "spck-confidential-secret-2026",
        "redirect_uris": ["https://platform.spck.dev/oauth/callback"]},
}
OAUTH_CODES = {}     # code -> {client_id, redirect_uri, scopes, challenge, expires, used}
OAUTH_TOKENS = {}    # access_token -> {client_id, scopes, revoked, expires}
OAUTH_REFRESH = {}   # refresh_token -> {client_id, scopes, revoked, access_tokens}
OAUTH_CODE_TTL, OAUTH_TOKEN_TTL = 300, 3600

def _loopback_match(registered, supplied):
    """RFC 8252 §7.3 loopback comparison: for 127.0.0.1/[::1] redirect URIs the port
    component is ignored; scheme, host, and path must match."""
    r, s = urllib.parse.urlsplit(registered), urllib.parse.urlsplit(supplied)
    try:
        rh, sh = r.hostname, s.hostname
    except ValueError:
        return False
    return rh in ("127.0.0.1", "::1") and sh == rh \
        and (r.scheme, r.path) == (s.scheme, s.path)

def _redirect_matches(registered, supplied):
    """Exact string match (identity-linking.md: 'exact string matching for the
    redirect_uri parameter' — IDL-020), with the loopback port exception."""
    return registered == supplied or _loopback_match(registered, supplied)

def oauth_authorize(params, base):
    """GET /oauth2/authorize -> (status, extra_headers, payload).
    Success: 302 redirect carrying code + state + iss (RFC 9207 — the business MUST
    return iss in the authorization response, IDL-018). Errors follow RFC 6749
    §4.1.2.1: unknown client / unregistered redirect_uri are answered DIRECTLY
    (400, never a redirect — open-redirect defense); other failures redirect back
    with the RFC error code (invalid_scope for out-of-vocabulary scopes;
    invalid_request for PKCE failures per RFC 7636 §4.4.1 — plain MUST NOT be
    used and S256 is required for all flows at 2026-04-08, IDL-049/IDL-023)."""
    client = OAUTH_CLIENTS.get(params.get("client_id") or "")
    ruri = params.get("redirect_uri") or ""
    if not client or not any(_redirect_matches(u, ruri) for u in client["redirect_uris"]):
        return 400, {}, {"error": "invalid_request",
                         "error_description": "unknown client_id or unregistered "
                                              "redirect_uri"}
    state = params.get("state")
    def bounce(qs):
        if state is not None:
            qs["state"] = state
        qs["iss"] = base                       # RFC 9207: iss on EVERY authz response
        sep = "&" if "?" in ruri else "?"
        return 302, {"Location": ruri + sep + urllib.parse.urlencode(qs)}, {}
    def err(code, desc):
        return bounce({"error": code, "error_description": desc})
    if params.get("response_type") != "code":
        return err("unsupported_response_type", "only response_type=code is supported")
    scopes = [s for s in (params.get("scope") or "").split() if s]
    bad = [s for s in scopes if s not in oauth_scopes_supported()]
    if bad:
        # scope grants are limited to the advertised vocabulary (RFC 6749 §4.1.2.1)
        return err("invalid_scope", "unsupported scope(s): " + " ".join(bad))
    challenge = params.get("code_challenge")
    method = params.get("code_challenge_method", "plain" if challenge else None)
    if VERSION == "2026-04-08" and OAUTH_ENFORCE_PKCE:
        # PKCE S256 is REQUIRED for all authorization code flows; plain MUST NOT
        # be used (identity-linking.md Security Considerations)
        if not challenge:
            return err("invalid_request", "code_challenge is required (PKCE S256)")
        if method != "S256":
            return err("invalid_request",
                       "code_challenge_method must be S256 (plain MUST NOT be used)")
    code = "ac_" + uuid.uuid4().hex
    OAUTH_CODES[code] = {"client_id": params["client_id"], "redirect_uri": ruri,
                         "scopes": scopes,
                         "challenge": challenge if method == "S256" else None,
                         "expires": time.time() + OAUTH_CODE_TTL, "used": False}
    return bounce({"code": code})

def _parse_basic(auth_header):
    if not isinstance(auth_header, str) or not auth_header.startswith("Basic "):
        return None
    try:
        cid, _, secret = base64.b64decode(auth_header[6:]).decode().partition(":")
        return urllib.parse.unquote(cid), urllib.parse.unquote(secret)
    except Exception:
        return None

def _oauth_client_auth(form, headers):
    """Authenticate a token/revocation request per the client's registered method
    (identity-linking.md: requests that fail the negotiated authentication method
    MUST be rejected with invalid_client — IDL-024; 01-era: businesses MUST enforce
    Client Authentication at the Token Endpoint — IDL-007@01-era).
    Returns (client_id, None) on success, else (None, (status, headers, payload))."""
    h = {k.lower(): v for k, v in headers.items()}
    basic = _parse_basic(h.get("authorization"))
    cid = basic[0] if basic else form.get("client_id")
    client = OAUTH_CLIENTS.get(cid or "")
    def fail():
        # RFC 6749 §5.2: invalid_client; 401 + a WWW-Authenticate header matching
        # the attempted scheme when the client used the Authorization header
        hdrs = {"WWW-Authenticate": 'Basic realm="oauth2"'} if basic else {}
        return None, (401, hdrs, {"error": "invalid_client",
                                  "error_description": "client authentication failed"})
    if not client:
        return fail()
    if not OAUTH_CLIENT_AUTH:                  # MUTANT: accept any credentials
        return cid, None
    if client["auth_method"] == "client_secret_basic":
        if not basic or basic[1] != client["secret"]:
            return fail()
    elif basic and basic[1]:                   # public client presenting a secret
        return fail()
    return cid, None

def _oauth_issue(cid, scopes, refresh=None):
    """Mint an access (+refresh) token. RFC 6749 §5.1 response fields; token
    responses carry Cache-Control: no-store."""
    at = "at_" + uuid.uuid4().hex
    if refresh is None:
        refresh = "rt_" + uuid.uuid4().hex
        OAUTH_REFRESH[refresh] = {"client_id": cid, "scopes": scopes,
                                  "revoked": False, "access_tokens": []}
    OAUTH_REFRESH[refresh]["access_tokens"].append(at)
    OAUTH_TOKENS[at] = {"client_id": cid, "scopes": scopes, "revoked": False,
                        "expires": time.time() + OAUTH_TOKEN_TTL}
    return 200, {"Cache-Control": "no-store", "Pragma": "no-cache"}, {
        "access_token": at, "token_type": "Bearer", "expires_in": OAUTH_TOKEN_TTL,
        "refresh_token": refresh, "scope": " ".join(scopes)}

def oauth_token(form, headers, base):
    """POST /oauth2/token (application/x-www-form-urlencoded) -> (status, headers,
    payload). Enforces, per identity-linking.md For Businesses:
      * client authentication per the advertised method -> invalid_client (IDL-024);
      * 2026-04-08: PKCE at the token endpoint — a missing or non-verifying
        code_verifier is rejected with invalid_grant (IDL-019/023/024/036/049);
      * redirect_uri identical to the authorization request's, with the loopback
        port exception (IDL-020/021) -> invalid_grant;
      * single-use, unexpired, same-client authorization codes -> invalid_grant."""
    cid, fail = _oauth_client_auth(form, headers)
    if fail:
        return fail
    def bad(desc):
        return 400, {}, {"error": "invalid_grant", "error_description": desc}
    gt = form.get("grant_type")
    if gt == "refresh_token":
        rt = OAUTH_REFRESH.get(form.get("refresh_token") or "")
        if not rt or rt["revoked"] or rt["client_id"] != cid:
            return bad("refresh_token is unknown, revoked, or issued to another client")
        return _oauth_issue(cid, rt["scopes"], refresh=form["refresh_token"])
    if gt != "authorization_code":
        return 400, {}, {"error": "unsupported_grant_type",
                         "error_description": f"unsupported grant_type: {gt}"}
    rec = OAUTH_CODES.get(form.get("code") or "")
    if not rec or rec["used"] or rec["expires"] < time.time() \
       or rec["client_id"] != cid:
        return bad("authorization code is unknown, expired, already used, "
                   "or issued to another client")
    if OAUTH_EXACT_REDIRECT and \
       not _redirect_matches(rec["redirect_uri"], form.get("redirect_uri") or ""):
        return bad("redirect_uri does not exactly match the authorization request")
    if VERSION == "2026-04-08" and OAUTH_ENFORCE_PKCE:
        verifier = form.get("code_verifier")
        if not verifier:
            return bad("code_verifier is required (PKCE)")
        if _b64url(hashlib.sha256(verifier.encode()).digest()) != rec["challenge"]:
            return bad("code_verifier does not verify against the code_challenge")
    rec["used"] = True
    return _oauth_issue(cid, rec["scopes"])

def oauth_revoke(form, headers, base):
    """POST /oauth2/revoke (RFC 7009) -> (status, headers, payload). Authenticated
    with the SAME client credentials as the token endpoint (IDL-028; IDL-012@01-era).
    Revoking a refresh_token immediately invalidates every access_token issued from
    it (IDL-027; IDL-011@01-era). Unknown/foreign tokens still yield 200 (RFC 7009
    §2.2 — revocation is idempotent and non-revealing)."""
    cid, fail = _oauth_client_auth(form, headers)
    if fail:
        return fail
    tok = form.get("token")
    if tok is None:
        return 400, {}, {"error": "invalid_request",
                         "error_description": "the token parameter is required"}
    rt = OAUTH_REFRESH.get(tok)
    if rt and rt["client_id"] == cid:
        rt["revoked"] = True
        for at in rt["access_tokens"]:
            if at in OAUTH_TOKENS:
                OAUTH_TOKENS[at]["revoked"] = True
    at = OAUTH_TOKENS.get(tok)
    if at and at["client_id"] == cid:
        at["revoked"] = True
    return 200, {}, {}

def require_identity(headers, required_scopes, base):
    """Bearer gate for user-authenticated operations (2026-04-08 identity-linking.md
    Error Handling). Returns None when authorized, else (status, headers, payload):
      * no token         -> 401, WWW-Authenticate: Bearer realm="<issuer>" (+
        resource_metadata per RFC 9728), error param OMITTED (RFC 6750 §3.1),
        body message code identity_required + a non-OAuth onboarding continue_url
        (which MUST NOT be a pre-baked authorization request — IDL-044);
      * invalid/revoked/expired token -> 401 + error="invalid_token" (IDL-042);
      * valid token, missing scope    -> 403 + error="insufficient_scope" +
        scope="<FULL required set>" (IDL-045/046/047).
    realm always equals the issuer URI from the RFC 8414 metadata (IDL-041/052)."""
    if not OAUTH_GATE:                         # MUTANT: a non-gating merchant
        return None
    h = {k.lower(): v for k, v in headers.items()}
    def challenge(status, code, error=None, scope=None, desc=None):
        p = [f'realm="{base}"']
        if error and OAUTH_CHALLENGE_ERROR:    # MUTANT omits the error param
            p.append(f'error="{error}"')
            if desc:
                p.append(f'error_description="{desc}"')
        if scope:
            p.append(f'scope="{scope}"')
        p.append(f'resource_metadata="{base}/.well-known/oauth-protected-resource"')
        body = {"messages": [{
            "type": "error", "code": code,
            "content": ("User identity is required to access this operation."
                        if code == "identity_required" else
                        "This operation requires scopes: " + ", ".join(required_scopes)),
            "severity": "requires_buyer_review"}]}
        if code == "identity_required":
            # a hosted (non-OAuth) onboarding step — NEVER a pre-baked authz request
            body["continue_url"] = base + "/account/onboarding"
        return status, {"WWW-Authenticate": "Bearer " + ", ".join(p)}, body
    authz = h.get("authorization") or ""
    if not authz.startswith("Bearer "):
        return challenge(401, "identity_required")
    tok = OAUTH_TOKENS.get(authz[len("Bearer "):].strip())
    # IDL-025/042: a business MUST validate the token on EVERY request — reject one
    # that is unknown, revoked, or expired with the invalid_token challenge. The
    # --oauth-accept-any-token mutant skips exactly this validation (proving the
    # expired/revoked-token checks test something real).
    if OAUTH_VALIDATE_TOKEN and (not tok or tok["revoked"] or tok["expires"] < time.time()):
        return challenge(401, "identity_required", error="invalid_token",
                         desc="The access token is invalid, expired, or revoked")
    scopes = tok["scopes"] if tok else list(IDENTITY_SCOPES)   # mutant: unknown->full
    missing = [s for s in required_scopes if s not in scopes]
    if missing:
        return challenge(403, "insufficient_scope", error="insufficient_scope",
                         scope=" ".join(required_scopes))
    return None

# The gated user-authenticated operations (2026-04-08 only — config.scopes is the
# 04-08 capability shape). Scopes per order.md's well-known scope table (ORD-014):
# order:read gates read access to the user's orders; the post-purchase cancel
# probe requires BOTH order scopes, matching the spec's own insufficient_scope
# example challenge (scope="dev.ucp.shopping.order:read dev.ucp.shopping.order:manage")
# so the FULL-set rule (IDL-047) is genuinely exercised.
ORDER_READ_SCOPES = ("dev.ucp.shopping.order:read",)
ORDER_MANAGE_SCOPES = ("dev.ucp.shopping.order:read", "dev.ucp.shopping.order:manage")

def list_orders(headers, base):
    """GET /orders — the user's order history (identity-linking.md access levels:
    'full order history' is user-authenticated). Gated by order:read."""
    denied = require_identity(headers, ORDER_READ_SCOPES, base)
    if denied:
        return denied
    return 200, {}, {"ucp": {"version": VERSION},
                     "orders": [order_body(o) for o in ORDERS.values()]}

def cancel_order(oid, headers, base):
    """POST /orders/{id}/cancel — a post-purchase operation gated by order:manage
    (+ order:read). The scope GATE is the conformance surface here; actual
    cancellation effects are the ORDER area's adjustment hook territory."""
    denied = require_identity(headers, ORDER_MANAGE_SCOPES, base)
    if denied:
        return denied
    order = ORDERS.get(oid)
    if not order:
        return 404, {}, {"detail": f"order not found: {oid}"}
    return 200, {}, order_body(order)

# ---- TEST-ONLY bad-token mint hook (IDL-025/IDL-042 kill-proof, 04-08) ----------
# A platform cannot manufacture a business's EXPIRED or REVOKED access token to
# probe "the business validates the token on every request" — only the business
# can (by waiting, or by exposing a test hook). Precedent: the /testing/orders/*
# hooks and the webhooks `simulate` contract. POST /testing/oauth/mint {kind} mints
# a deterministic access token in the requested (in)validity state, gated on the
# config.identity.token_mint capability a merchant opts into. Scopes = the full
# vocabulary so the ONLY defect a probe can surface is the (in)validity itself.
def mint_test_token(kind):
    """kind: valid | expired | revoked. Returns (status, payload)."""
    scopes = list(IDENTITY_SCOPES)
    at = "at_test_" + uuid.uuid4().hex
    rec = {"client_id": "spck-platform-public", "scopes": scopes,
           "revoked": False, "expires": time.time() + OAUTH_TOKEN_TTL}
    if kind == "expired":
        rec["expires"] = time.time() - 10            # issued, but past its exp (IDL-025 exp)
    elif kind == "revoked":
        rec["revoked"] = True                        # issued, then revoked (RFC 7009)
    elif kind != "valid":
        return 400, {"error": "invalid_request",
                     "error_description": f"unsupported mint kind: {kind}"}
    OAUTH_TOKENS[at] = rec
    return 200, {"access_token": at, "token_type": "Bearer",
                 "kind": kind, "scope": " ".join(scopes)}

# ---- 01-era checkout-scope gate (IDL-013 kill-proof) ----------------------------
# IDL-013@01-era: "a scope covering a capability must grant access to ALL operations
# associated to the capability" (checkout_session: Get/Create/Update/Cancel/Complete).
# Under --require-checkout-scope every 01-era checkout-session operation demands a
# Bearer token carrying ucp:scopes:checkout_session, so ONE such token must unlock
# all of them. The --checkout-scope-partial MUTANT makes the "read" (Get) operation
# additionally demand a fictional per-operation scope the capability scope does NOT
# grant — the exact IDL-013 violation the check must catch.
_CHECKOUT_PARTIAL_OP = "read"
_CHECKOUT_EXTRA_SCOPE = "ucp:scopes:checkout_session_get"

def require_checkout_scope(headers, base, op):
    """Return None when authorized, else (status, headers, payload). Only active in
    01-era modes with REQUIRE_CHECKOUT_SCOPE set."""
    if not REQUIRE_CHECKOUT_SCOPE or VERSION == "2026-04-08":
        return None
    required = [IDENTITY_SCOPES_01ERA[0]]
    if CHECKOUT_SCOPE_PARTIAL and op == _CHECKOUT_PARTIAL_OP:
        required.append(_CHECKOUT_EXTRA_SCOPE)
    h = {k.lower(): v for k, v in headers.items()}
    authz = h.get("authorization") or ""

    def deny(status, code):
        return status, {"WWW-Authenticate": f'Bearer realm="{base}"'}, {
            "messages": [{"type": "error", "code": code,
                          "content": "Checkout operations require the "
                                     "ucp:scopes:checkout_session scope.",
                          "severity": "requires_buyer_review"}]}
    if not authz.startswith("Bearer "):
        return deny(401, "identity_required")
    tok = OAUTH_TOKENS.get(authz[len("Bearer "):].strip())
    if not tok or tok["revoked"] or tok["expires"] < time.time():
        return deny(401, "identity_required")
    if any(s not in tok["scopes"] for s in required):
        return deny(403, "insufficient_scope")
    return None
# ==== end OAUTH area ============================================================

# ---- discovery-area profile-serving policy (2026-04-08 overview.md Hosting) -----
# Profile responses MUST carry Cache-Control with `public` and max-age >= 60 and
# MUST NOT use private/no-store/no-cache (DISC-003). Served on /.well-known/ucp;
# rule-checked in selfcheck.py (headers are not schema territory, so the assertion
# is against the pinned spec text, not the oracle).
PROFILE_CACHE_CONTROL = "public, max-age=300"
# ==== SIGNATURES area (2026-04-08, signatures.md) ==============================
# RFC 9421 HTTP Message Signatures: the fixture SIGNS its responses (@status +
# content-digest + content-type, ES256, raw r||s) and VERIFIES any request that
# carries a Signature-Input header (spec verify_rest_request: key_not_found /
# digest_mismatch / signature_invalid). Pure stdlib P-256 ECDSA — the signer is
# cross-anchored against openssl in selfcheck.py (both directions), so its
# correctness does not rest on our own code alone. TEST KEYS ONLY: the private
# scalars below are committed on purpose (this is a conformance golden, not a
# production service).
_EC_P  = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_EC_A  = _EC_P - 3
_EC_B  = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
_EC_N  = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_EC_G  = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
          0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)

def _ec_add(p1, p2):
    if p1 is None: return p2
    if p2 is None: return p1
    x1, y1 = p1; x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _EC_P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + _EC_A) * pow(2 * y1, -1, _EC_P) % _EC_P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, _EC_P) % _EC_P
    x3 = (lam * lam - x1 - x2) % _EC_P
    return (x3, (lam * (x1 - x3) - y1) % _EC_P)

def _ec_mul(k, pt):
    acc = None
    while k:
        if k & 1:
            acc = _ec_add(acc, pt)
        pt = _ec_add(pt, pt)
        k >>= 1
    return acc

def ec_on_curve(pt):
    if pt is None: return False
    x, y = pt
    return (y * y - (x * x * x + _EC_A * x + _EC_B)) % _EC_P == 0

def _rfc6979_k(d, h1):
    """Deterministic ECDSA nonce (RFC 6979, SHA-256, qlen == hlen == 256)."""
    x = d.to_bytes(32, "big")
    V, K = b"\x01" * 32, b"\x00" * 32
    K = hmac.new(K, V + b"\x00" + x + h1, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    K = hmac.new(K, V + b"\x01" + x + h1, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    while True:
        V = hmac.new(K, V, hashlib.sha256).digest()
        k = int.from_bytes(V, "big")
        if 1 <= k < _EC_N:
            return k
        K = hmac.new(K, V + b"\x00", hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()

def ecdsa_p256_sign(msg, d):
    """ECDSA P-256/SHA-256 over msg bytes -> 64-byte fixed-width raw r||s
    (RFC 9421 signature encoding — NOT ASN.1/DER)."""
    h1 = hashlib.sha256(msg).digest()
    z = int.from_bytes(h1, "big")
    while True:
        k = _rfc6979_k(d, h1)
        x1, _ = _ec_mul(k, _EC_G)
        r = x1 % _EC_N
        if r:
            s = pow(k, -1, _EC_N) * (z + r * d) % _EC_N
            if s:
                return r.to_bytes(32, "big") + s.to_bytes(32, "big")
        h1 = hashlib.sha256(h1).digest()

def ecdsa_p256_verify(msg, sig, Q):
    """Verify a 64-byte raw r||s ECDSA P-256/SHA-256 signature against point Q."""
    if not isinstance(sig, (bytes, bytearray)) or len(sig) != 64 or not ec_on_curve(Q):
        return False
    r = int.from_bytes(sig[:32], "big"); s = int.from_bytes(sig[32:], "big")
    if not (1 <= r < _EC_N and 1 <= s < _EC_N):
        return False
    z = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, -1, _EC_N)
    pt = _ec_add(_ec_mul(z * w % _EC_N, _EC_G), _ec_mul(r * w % _EC_N, Q))
    return pt is not None and pt[0] % _EC_N == r

def _keypair(seed):
    d = (int.from_bytes(hashlib.sha256(seed).digest(), "big") % (_EC_N - 1)) + 1
    return d, _ec_mul(d, _EC_G)

# The fixture's own (merchant) response-signing key, derived deterministically.
SIG_KID = "spck-merchant-sig-2026"
_SIG_D, _SIG_Q = _keypair(b"spck-fixture-merchant-signing-key-2026")

def signing_jwk():
    """The fixture's PUBLIC signing key as an RFC 7517 JWK (profile signing_keys[])."""
    return {"kid": SIG_KID, "kty": "EC", "crv": "P-256",
            "x": _b64url(_SIG_Q[0].to_bytes(32, "big")),
            "y": _b64url(_SIG_Q[1].to_bytes(32, "big")),
            "use": "sig", "alg": "ES256"}

# Trusted PLATFORM test key (public part only) for request verification. The
# matching private JWK lives in CONTROLLED_CONFIG (validate_merchant_checks.py)
# so the SIG-002 check can sign requests this fixture will verify.
TRUSTED_PLATFORM_KEYS = {
    "spck-platform-sig-2026":
        (int.from_bytes(base64.urlsafe_b64decode("fdOWNX6FUcEYKQntKv0Pb0wpcIEV6HrDZK4Ud9oF_rY="), "big"),
         int.from_bytes(base64.urlsafe_b64decode("-Ie-pMb2OxUqg4GR_B6wObhra9-fRe5YWzWAAv7dNKk="), "big")),
}

def content_digest(body_bytes):
    """RFC 9530 Content-Digest over the raw body bytes, sha-256 (signatures.md)."""
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body_bytes).digest()).decode() + ":"

def _sf_split(s, seps):
    """Split a structured-field string on top-level separator chars, respecting
    quoted strings and inner lists (enough RFC 8941 for signature headers)."""
    out, cur, depth, quote, i = [], [], 0, False, 0
    while i < len(s):
        c = s[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1]); i += 1
            elif c == '"':
                quote = False
        elif c == '"':
            quote = True; cur.append(c)
        elif c == "(":
            depth += 1; cur.append(c)
        elif c == ")":
            depth -= 1; cur.append(c)
        elif c in seps and depth == 0:
            out.append("".join(cur).strip()); cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out

def parse_signature_input(value):
    """Parse a Signature-Input header -> {label: {raw, components, params}}.
    `raw` is the member value VERBATIM (what "@signature-params" must echo);
    `components` are the unquoted component identifiers; `params` maps parameter
    names to their raw values (quoted strings unquoted). None on malformed input."""
    if not isinstance(value, str) or not value.strip():
        return None
    out = {}
    for member in _sf_split(value, ","):
        label, eq, val = member.partition("=")
        label, val = label.strip(), val.strip()
        if not eq or not label or not val.startswith("("):
            return None
        depth, j = 0, 0
        for j, c in enumerate(val):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
        inner, rest = val[1:j], val[j + 1:]
        comps = []
        for tok in _sf_split(inner, " "):
            if not (tok.startswith('"') and tok.endswith('"')) or ";" in tok:
                return None            # component parameters are not supported here
            comps.append(tok[1:-1])
        params = {}
        for p in _sf_split(rest, ";"):
            if not p:
                continue
            k, _, v = p.partition("=")
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            params[k.strip()] = v
        out[label] = {"raw": val, "components": comps, "params": params}
    return out or None

def parse_signature(value):
    """Parse a Signature header -> {label: raw signature bytes}; None on malformed."""
    if not isinstance(value, str) or not value.strip():
        return None
    out = {}
    for member in _sf_split(value, ","):
        label, eq, val = member.partition("=")
        label, val = label.strip(), val.strip()
        if not eq or not val.startswith(":") or not val.endswith(":"):
            return None
        try:
            out[label] = base64.b64decode(val[1:-1], validate=True)
        except Exception:
            return None
    return out or None

def _sig_base(components, raw_params, derived, headers_l):
    """RFC 9421 signature base: one `"name": value` line per component plus the
    `"@signature-params"` line echoing the Signature-Input member verbatim.
    `derived` maps supported @-components to their values; returns None when a
    component can't be resolved."""
    lines = []
    for c in components:
        if c.startswith("@"):
            if c not in derived:
                return None
            v = derived[c]
        else:
            if c not in headers_l:
                return None
            v = headers_l[c].strip()
        lines.append(f'"{c}": {v}')
    lines.append(f'"@signature-params": {raw_params}')
    return "\n".join(lines).encode()

def sign_response(status, body_bytes):
    """RFC 9421 response signature headers for a JSON response body:
    components @status + content-digest + content-type (signatures.md REST
    Response Signing), ES256 raw r||s, keyid -> the profile's signing_keys."""
    digest = content_digest(body_bytes)
    ctype = "application/json"
    comps = ["@status", "content-digest", "content-type"]
    raw_params = ('(' + " ".join(f'"{c}"' for c in comps) + ')'
                  + f';created={int(time.time())};keyid="{SIG_KID}"')
    base = _sig_base(comps, raw_params, {"@status": str(status)},
                     {"content-digest": digest, "content-type": ctype})
    sig = ecdsa_p256_sign(base, _SIG_D)
    return {"Content-Digest": digest,
            "Signature-Input": f"sig1={raw_params}",
            "Signature": "sig1=:" + base64.b64encode(sig).decode() + ":"}

def verify_signed_request(method, path_qs, headers, raw_body):
    """Verify an incoming request that carries RFC 9421 signature headers, per the
    spec's verify_rest_request pseudocode. `headers` is a case-insensitive-ready
    mapping (we lowercase it here). Returns None when the signature verifies, else
    (http_status, error_payload) with the spec's signature error codes."""
    h = {k.lower(): v for k, v in headers.items()}
    def err(status, code, content):
        return status, {"code": code, "content": content}
    si = parse_signature_input(h.get("signature-input", ""))
    if not si:
        return err(401, "signature_missing", "Signature-Input header is missing or malformed")
    sigs = parse_signature(h.get("signature", ""))
    if not sigs:
        return err(401, "signature_missing", "Signature header is missing or malformed")
    label = next((l for l in si if l in sigs), None)
    if label is None:
        return err(401, "signature_missing", "no Signature member matches Signature-Input")
    entry = si[label]
    kid = entry["params"].get("keyid")
    pub = TRUSTED_PLATFORM_KEYS.get(kid)
    if not pub:
        return err(401, "key_not_found", f"key ID not found in signer's signing_keys: {kid}")
    if "content-digest" in entry["components"]:
        if h.get("content-digest") != content_digest(raw_body or b""):
            return err(400, "digest_mismatch",
                       "body digest doesn't match Content-Digest header")
    path, _, query = path_qs.partition("?")
    derived = {"@method": method.upper(), "@authority": h.get("host", ""), "@path": path}
    if query:
        derived["@query"] = "?" + query
    base = _sig_base(entry["components"], entry["raw"], derived, h)
    if base is None or not ecdsa_p256_verify(base, sigs[label], pub):
        return err(401, "signature_invalid",
                   f"request signature verification failed for key kid={kid}")
    return None
# ==== end SIGNATURES area ======================================================

# ==== WEBHOOK/EVENTS area (2026-04-08, order.md "Events") ======================
# Order lifecycle events: the business POSTs the FULL order entity (current-state
# snapshot, order.md "Events"/"Order Event Webhook") to the webhook URL the
# platform provides in its order capability's config (order.json
# $defs/platform_schema: webhook_url). Every webhook is signed per order.md
# "Webhook Signature Verification" + signatures.md REST Request Signing: RFC 9421
# headers (Signature, Signature-Input, Content-Digest), UCP-Agent naming the
# business profile, Standard-Webhooks headers (Webhook-Id, Webhook-Timestamp),
# signed with the key published in the profile's signing_keys[] (ES256 raw r||s).
# Failed deliveries are RETRIED (order.md Business guidelines). Signed components
# follow the NORMATIVE signatures.md request table: @method/@authority/@path
# always, @query when the URL has one, ucp-agent + idempotency-key (POST) +
# content-digest + content-type (body present). The Webhook-Id doubles as the
# Idempotency-Key so redelivery of the same event is idempotent.
WEBHOOK_RETRY_DELAYS = (0.4, 0.8)   # short backoff: this is a test golden

def _is_loopback(url):
    """True for http(s)://127.0.0.1/localhost URLs (the suite's local harness)."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")

def resolve_platform_webhook_url(agent_header):
    """The platform's order-capability webhook_url, discovered by FETCHING the
    platform profile named in UCP-Agent (order.md Webhook URL Configuration).
    OFFLINE POLICY: this golden only fetches LOOPBACK profile URLs (the suite's
    webhook harness); non-loopback URLs use the seeded simulation
    (negotiate_platform) and are never fetched. Returns webhook_url or None."""
    url = _agent_profile_url(agent_header)
    if not url or not _is_loopback(url):
        return None
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            doc = json.loads(r.read())
    except Exception:
        return None                          # unreachable harness -> no webhooks
    ucp = doc.get("ucp", doc) if isinstance(doc, dict) else {}
    caps = ucp.get("capabilities") if isinstance(ucp, dict) else None
    for entry in (caps or {}).get("dev.ucp.shopping.order") or []:
        wu = ((entry or {}).get("config") or {}).get("webhook_url")
        if isinstance(wu, str) and wu:
            return wu
    return None

def build_webhook_headers(url, body_bytes, base, webhook_id=None):
    """The signed request headers for one order-event webhook delivery (pure —
    selfcheck.py validates these artifacts in-process, no ports)."""
    u = urlsplit(url)
    agent = f'profile="{base}/.well-known/ucp"'
    wid = webhook_id or str(uuid.uuid4())
    digest = content_digest(body_bytes)
    comps = ["@method", "@authority", "@path"]
    if u.query:
        comps.append("@query")               # signatures.md: @query when present
    comps += ["ucp-agent", "idempotency-key", "content-digest", "content-type"]
    hdrs = {"Content-Type": "application/json", "UCP-Agent": agent,
            "Webhook-Id": wid, "Webhook-Timestamp": str(int(time.time())),
            "Idempotency-Key": wid, "Content-Digest": digest}
    values = {"@method": "POST", "@authority": u.netloc, "@path": u.path or "/",
              "@query": "?" + u.query, "ucp-agent": agent, "idempotency-key": wid,
              "content-digest": digest, "content-type": "application/json"}
    raw_params = ("(" + " ".join(f'"{c}"' for c in comps) + ")"
                  + f';keyid="{SIG_KID}"')
    sig_base = "\n".join([f'"{c}": {values[c]}' for c in comps]
                         + [f'"@signature-params": {raw_params}']).encode()
    sig = ecdsa_p256_sign(sig_base, _SIG_D)
    hdrs["Signature-Input"] = f"sig1={raw_params}"
    hdrs["Signature"] = "sig1=:" + base64.b64encode(sig).decode() + ":"
    return hdrs

def build_webhook_headers_0123(body_bytes, base):
    """The signed request headers for one 2026-01-23/01-11 order-event delivery:
    the signature is a DETACHED JWS (RFC 7797, b64=false + crit) over the raw
    body bytes, ES256 with the key published in the profile's signing_keys[],
    kid in the protected header, carried in Request-Signature (01-era order.md
    'Signing (Business)'). UCP-Agent identifies the business ('MUST include
    business identifier in webhook path or headers')."""
    header = _b64url(json.dumps(
        {"alg": "ES256", "kid": SIG_KID, "b64": False, "crit": ["b64"]},
        separators=(",", ":")).encode())
    signing_input = header.encode() + b"." + body_bytes    # RFC 7797 b64=false
    sig = ecdsa_p256_sign(signing_input, _SIG_D)
    return {"Content-Type": "application/json",
            "UCP-Agent": f'profile="{base}/.well-known/ucp"',
            "Request-Signature": header + ".." + _b64url(sig)}

def webhook_event_payload(order):
    """The per-version order-event payload. 2026-04-08: the order entity itself
    (rest.openapi.json webhooks.orderEvent requestBody schema = order).
    2026-01-23/01-11: the order entity plus the required event_id/created_time
    (openapi.json order_event_webhook: allOf [order, {event_id, created_time}])."""
    payload = order_body(order)
    if VERSION != "2026-04-08":
        payload["event_id"] = "evt_" + uuid.uuid4().hex[:12]
        payload["created_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return payload

def _deliver_webhook(url, payload, base):
    """POST one order event (full order entity) to the platform's webhook URL.
    First attempt is synchronous (keeps event ordering deterministic for the
    reference gates); on failure the remaining attempts run on a background
    thread with backoff — 'MUST retry failed webhook deliveries'."""
    if not (SEND_WEBHOOKS and url):
        return
    body = json.dumps(payload).encode()
    wid = str(uuid.uuid4())
    def attempt():
        hdrs = (build_webhook_headers(url, body, base, wid)
                if VERSION == "2026-04-08" else build_webhook_headers_0123(body, base))
        req = urllib.request.Request(url, data=body, method="POST", headers=hdrs)
        with urllib.request.urlopen(req, timeout=5) as r:
            return 200 <= r.status < 300
    def retries():
        for delay in WEBHOOK_RETRY_DELAYS:
            time.sleep(delay)
            try:
                if attempt():
                    return
            except Exception:
                pass
    try:
        if attempt():
            return
    except Exception:
        pass
    threading.Thread(target=retries, daemon=True).start()
# ==== end WEBHOOK/EVENTS area ==================================================
# ---- OLD-VERSIONS (01-era) capability metadata --------------------------------
# The `spec` and `schema` fields are REQUIRED for all capabilities (overview.md
# "Spec URL Binding" — DISC-002) and their URL origin MUST match the namespace
# authority (DISC-003): dev.ucp.* -> https://ucp.dev/... . Schema-required at
# 2026-01-11 (capability.json $defs/discovery); prose-required at 2026-01-23.
def _cap_meta(name, extends=None):
    short = name.rsplit(".", 1)[-1].replace("_", "-")
    entry = {"name": name, "version": VERSION,
             "spec": f"https://ucp.dev/specification/{short}",
             "schema": f"https://ucp.dev/schemas/shopping/{name.rsplit('.', 1)[-1]}.json"}
    if extends:
        entry["extends"] = extends
    return entry

# 01-era capability set (checkout/order + the extensions the lifecycle exhibits)
_CAPS_01_ERA = (
    ("dev.ucp.shopping.checkout", None),
    ("dev.ucp.shopping.order", None),
    ("dev.ucp.shopping.discount", "dev.ucp.shopping.checkout"),
    # buyer-consent.md: businesses advertise consent support in their profile;
    # the extension extends checkout.buyer with boolean consent states (DSC-019)
    ("dev.ucp.shopping.buyer_consent", "dev.ucp.shopping.checkout"),
)

def payment_handler_01era():
    """A payment handler entry per the 01-era shopping/types/payment_handler.json
    (required: id, name, version, spec, config_schema, instrument_schemas, config).
    Used by the 2026-01-11 profile's top-level `payment.handlers` AND by 01-11
    checkout responses (checkout.json REQUIRES root `payment` at 2026-01-11)."""
    return {"id": PAYMENT_HANDLER_ID, "name": PAYMENT_HANDLER_KEY,
            "version": VERSION,
            "spec": "https://spck.dev/fixture/handlers/tokenpay",
            "config_schema": "https://spck.dev/fixture/handlers/tokenpay/schema.json",
            "instrument_schemas": [
                "https://ucp.dev/schemas/shopping/types/card_payment_instrument.json"],
            "config": {}}

def profile_01_11(base):
    """The 2026-01-11 discovery document (overview.md 2026-01-11 example): the UCP
    metadata nests under `ucp` (validated against ucp.json $defs/discovery_profile:
    version + services + capabilities ARRAY, entries per capability.json
    $defs/discovery — name/version/spec/schema required) and payment handlers live
    in a sibling top-level `payment` member (payment.json: handlers[])."""
    return {"ucp": {"version": VERSION,
                    "services": {"dev.ucp.shopping": {
                        "version": VERSION,
                        "spec": "https://ucp.dev/specification/overview",
                        "rest": {"schema": "https://ucp.dev/services/shopping/rest.openapi.json",
                                 "endpoint": base}}},
                    "capabilities": [_cap_meta(n, e) for n, e in _CAPS_01_ERA]},
            "payment": {"handlers": [payment_handler_01era()]},
            # order.md@01-era webhook signing: "a key from their signing_keys array,
            # published in /.well-known/ucp" — top-level sibling, the same placement
            # every other served version uses (integration fix: the 01-11 renderer
            # predated webhook emission and returned before signing_keys attached)
            "signing_keys": [signing_jwk()]}

def profile(base):
    if VERSION == "2026-01-11":
        return profile_01_11(base)
    cap = [{"version": VERSION}]
    services = [
        {"version": VERSION, "transport": "rest", "endpoint": base,
         "spec": f"https://ucp.dev/{VERSION}/specification/shopping",
         "schema": f"https://ucp.dev/{VERSION}/services/shopping/openapi.json"}]
    capabilities = {
        "dev.ucp.shopping.checkout": cap,
        "dev.ucp.shopping.order": cap,
        "dev.ucp.shopping.discount": [
            {"version": VERSION, "extends": "dev.ucp.shopping.checkout"}],
    }
    if VERSION == "2026-01-23":
        # 01-era profile entries carry the REQUIRED spec/schema capability metadata
        # (DISC-002) with namespace-authority origins (DISC-003) + buyer_consent
        capabilities = {n: [{k: v for k, v in _cap_meta(n, e).items() if k != "name"}]
                        for n, e in _CAPS_01_ERA}
    if VERSION == "2026-04-08":              # catalog/cart/MCP exist only in 04-08
        services.append(
            {"version": VERSION, "transport": "mcp", "endpoint": base + "/ucp/mcp",
             "spec": f"https://ucp.dev/{VERSION}/specification/shopping",
             "schema": f"https://ucp.dev/{VERSION}/services/shopping/mcp.openrpc.json"})
        capabilities.update({
            "dev.ucp.shopping.catalog.search": cap,
            "dev.ucp.shopping.catalog.lookup": cap,
            "dev.ucp.shopping.cart": cap,
            # identity-linking: the business declares its user-authenticated scopes
            # in config.scopes (identity_linking.json business_schema requires
            # config + config.scopes; keys are scope_tokens). 04-08 only — the
            # identity-linking rework and its IDL register ids are 2026-04-08.
            "dev.ucp.common.identity_linking": [
                {"version": VERSION,
                 "schema": "https://ucp.dev/schemas/common/identity_linking.json",
                 "config": {"scopes": IDENTITY_SCOPES}}],
        })
    else:
        # OAUTH area: 01-era identity-linking (identity-linking.md@2026-01-23/01-11
        # is capability-plain — no config.scopes shape existed yet; the OAuth layer
        # carries the 01-era scope vocabulary, see IDENTITY_SCOPES_01ERA).
        # spec/schema carry origin-matched URLs (DISC-002/DISC-003 — every declared
        # capability needs them, integration fix after the wave-2 union).
        capabilities["dev.ucp.common.identity_linking"] = [
            {"version": VERSION,
             "spec": "https://ucp.dev/specification/identity-linking",
             "schema": "https://ucp.dev/schemas/common/identity_linking.json"}]
    out = {"version": VERSION,
           "services": {"dev.ucp.shopping": services},
           "capabilities": capabilities,
           # PAYMENT AREA: the business-profile handler declaration (PAY-001/PAY-002)
           "payment_handlers": payment_handlers_registry()}
    # Key discovery in EVERY served version: 04-08 signatures.md Key Discovery
    # (RFC 9421 response/request keys) and 01-era order.md webhook signing
    # ("a key from their signing_keys array, published in /.well-known/ucp").
    # Oracle-validated per version in selfcheck.py.
    out["signing_keys"] = [signing_jwk()]
    return out

# ---- DISC-014 loopback spec/schema URLs (reference-gate hermeticity) -------------
def _localize_spec_urls(node, base, ctr):
    """Recursively repoint every spec/schema/config_schema (+ instrument_schemas)
    URL to a LOOPBACK path this fixture serves, so the DISC-014 live-URL check can
    be reference-gated without any network. ctr is a 1-element list (mutable counter);
    the FIRST URL becomes a 404 sentinel under BREAK_SPEC_URL."""
    def loc():
        ctr[0] += 1
        if BREAK_SPEC_URL and ctr[0] == 1:
            return base + _LOCAL_BROKEN_PATH
        return f"{base}/__localspec/u{ctr[0]}"
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k in _LOCAL_SPEC_KEYS and isinstance(v, str) and "://" in v:
                node[k] = loc()
            elif k == "instrument_schemas" and isinstance(v, list):
                node[k] = [loc() if isinstance(s, str) and "://" in s else s for s in v]
            else:
                _localize_spec_urls(v, base, ctr)
    elif isinstance(node, list):
        for item in node:
            _localize_spec_urls(item, base, ctr)
    return node

def profile_served(base):
    """The profile as served on /.well-known/ucp — normally the real profile, but
    with loopback spec/schema URLs under --local-spec-urls (DISC-014 gate only)."""
    p = profile(base)
    if LOCAL_SPEC_URLS:
        p = _localize_spec_urls(json.loads(json.dumps(p)), base, [0])
    return p

# ---- version-negotiation / discovery-error simulation (2026-04-08 only) ----------
# The negotiation protocol (overview.md "Negotiation Protocol" + "Error Handling")
# requires a business to fetch the platform profile named in UCP-Agent and fail with
# the mapped negotiation error. This offline fixture cannot fetch, so it recognizes
# SEEDED platform-profile URLs and answers exactly as a fetching implementation
# would; a REAL merchant is probed with config-supplied URLs that genuinely exhibit
# each failure (the checks are config-gated). Discovery/version failures are
# TRANSPORT errors with a flat {code, content, continue_url} body (overview.md
# "Transport Bindings"); capabilities_incompatible is HTTP 200 with the error in
# the UCP body (error_response envelope). Owned by the discovery/negotiation area.
SIM_PLATFORM = "https://spck.dev/fixture/platform/"
SIM_UNREACHABLE = SIM_PLATFORM + "unreachable-profile.json"   # fetch times out / non-2xx
SIM_MALFORMED = SIM_PLATFORM + "malformed-profile.json"       # fetched body is not JSON
SIM_LEGACY_VERSION = SIM_PLATFORM + "legacy-version.json"     # profile version 1999-01-01
SIM_NO_COMMON_CAPS = SIM_PLATFORM + "no-common-caps.json"     # empty capability intersection
CONTINUE_URL = "https://spck.dev/fixture"

def _agent_profile_url(agent_header):
    """The profile URL from a UCP-Agent header (RFC 8941 dict, profile= member)."""
    import re
    m = re.search(r'profile="([^"]*)"', agent_header or "")
    return m.group(1) if m else None

def negotiate_platform(agent_header):
    """Simulated platform-profile resolution for the seeded URLs above.
    Returns (http_status, payload) for a negotiation failure, or None to proceed."""
    url = _agent_profile_url(agent_header)
    if url is None:
        return None                               # header-presence is enforced elsewhere
    def flat(code, content):                      # transport-error body per overview.md
        return {"code": code, "content": content, "continue_url": CONTINUE_URL}
    if url.startswith("http://") and not _is_loopback(url):
        # DISC-004: reject non-HTTPS profile URLs. TEST-GOLDEN ALLOWANCE: loopback
        # http URLs are exempt so the suite's local webhook harness can serve the
        # platform profile (mirrors the official Flower golden, which fetches the
        # harness profile over loopback http). Real platform profiles are never
        # loopback, so the DISC-004 checks (non-loopback http URLs) stay sound.
        return 400, flat("invalid_profile_url",
                         f"Profile URLs must use https, got: {url}")
    if url == SIM_UNREACHABLE:                    # NEG-003: resolved but fetch failed -> 424
        return 424, flat("profile_unreachable",
                         f"Unable to fetch platform profile {url}: connection timeout")
    if url == SIM_MALFORMED:                      # NEG-004: fetched content invalid -> 422
        return 422, flat("profile_malformed",
                         f"Platform profile {url} is not valid JSON")
    if url == SIM_LEGACY_VERSION:                 # NEG-001: version_unsupported -> 422
        return 422, flat("version_unsupported",
                         f"Protocol version 1999-01-01 is not supported. "
                         f"This business supports version {VERSION}.")
    if url == SIM_NO_COMMON_CAPS:                 # NEG-002: HTTP 200, error in the UCP body
        return 200, {"ucp": {"version": VERSION, "status": "error", "capabilities": {}},
                     "messages": [{"type": "error", "code": "capabilities_incompatible",
                                   "content": "No compatible capabilities in the "
                                              "platform/business intersection",
                                   "severity": "unrecoverable"}],
                     "continue_url": CONTINUE_URL}
    return None

def _unit_price(item_id):
    """Unit price (minor units) for a product or variant id, from the seed catalog."""
    if item_id in BY_ID:
        return BY_ID[item_id]["price_range"]["min"]["amount"]
    if item_id in BY_VARIANT:
        p = BY_VARIANT[item_id]
        v = next((v for v in p["variants"] if v["id"] == item_id), None)
        return (v or {}).get("price", {}).get("amount", p["price_range"]["min"]["amount"])
    return 1000

def cart_response(body, cid=None):
    """Build a spec-valid cart (checkout.json + cart_id) from requested line_items.
    The cart is computed from the REQUEST BODY ALONE (never from previously stored
    state), which is exactly the full-replacement semantics cart.md 'Update Cart'
    mandates: 'The provided resource replaces the existing cart state on the
    business side' (CART-017). Line-item and cart totals are recomputed from the
    catalog's unit prices on every call."""
    reqs = (body or {}).get("line_items") or []
    line_items, subtotal = [], 0
    for i, li in enumerate(reqs):
        iid = (li.get("item") or {}).get("id") or li.get("id")
        qty = int(li.get("quantity", 1) or 1)
        amt = _unit_price(iid) * qty
        subtotal += amt
        line_items.append({"id": f"li_{i+1}", "item": {"id": iid}, "quantity": qty,
                           "totals": [{"type": "subtotal", "amount": amt}]})
    cid = cid or "cart_" + uuid.uuid4().hex[:10]
    return {"ucp": {"version": VERSION}, "id": cid, "cart_id": cid,
            "currency": (body or {}).get("currency", "USD"), "status": "incomplete",
            "line_items": line_items,
            "totals": [{"type": "subtotal", "amount": subtotal},
                       {"type": "total", "amount": subtotal}]}

CARTS = {}          # cart id -> latest cart state (2026-04-08 only; guarded by _CART_LOCK)
_CART_LOCK = threading.Lock()

def _cart_not_found():
    """Cart not-found is a BUSINESS OUTCOME (cart-rest.md 'Business Outcomes'):
    HTTP 200 with ucp.status=error + messages[code=not_found]."""
    return 200, {"ucp": {"version": VERSION, "status": "error",
                         "capabilities": {"dev.ucp.shopping.cart": [{"version": VERSION}]}},
                 "messages": [{"type": "error", "code": "not_found",
                               "content": "Cart not found or has expired",
                               "severity": "unrecoverable"}]}

def create_cart(body, headers=None):
    """POST /carts (2026-04-08 only). Enforces the mandatory UCP-Agent header
    (cart-rest.md 'All requests MUST include the UCP-Agent header' — CART-024),
    mirroring create_checkout's enforcement for checkout (CHK-052/CHK-046).
    Stores the cart so GET/PUT /carts/{id} can act on it (CART-017).
    Returns (http_status, payload) so selfcheck.py can validate it in-process."""
    headers = headers or {}
    if not headers.get("UCP-Agent"):
        return _missing_agent_err()
    cart = cart_response(body)
    with _CART_LOCK:
        CARTS[cart["id"]] = cart
    return 201, cart

def get_cart(cid):
    """GET /carts/{id} (2026-04-08 only) — 'retrieves the latest state of a cart
    session. Returns not_found if the cart does not exist' (cart.md 'Get Cart')."""
    with _CART_LOCK:
        cart = CARTS.get(cid)
    return (200, cart) if cart else _cart_not_found()

def update_cart(cid, body, headers=None):
    """PUT /carts/{id} (2026-04-08 only) — cart.md 'Update Cart': 'Performs a full
    replacement of the cart session. The platform MUST send the entire cart
    resource. The provided resource replaces the existing cart state on the
    business side.' (CART-017). The stored state is REBUILT from the request body
    alone (no merge with the previous line_items), and all prices/totals are
    recomputed. Same UCP-Agent enforcement as create (CART-024)."""
    headers = headers or {}
    if not headers.get("UCP-Agent"):
        return _missing_agent_err()
    with _CART_LOCK:
        if cid not in CARTS:
            return _cart_not_found()
        cart = cart_response(body, cid=cid)   # replace, never merge
        CARTS[cid] = cart
    return 200, cart

# ---- checkout lifecycle (create/get/update/complete/cancel) ------------------
# Pure functions returning (http_status, payload) so selfcheck.py can validate every
# artifact against the official schemas without going through HTTP.
SESSIONS = {}       # checkout id -> session state
IDEM = {}           # idempotency-key -> (body_fingerprint, http_status, payload)
_LOCK = threading.Lock()

def _title(iid):
    if iid in BY_ID:
        return BY_ID[iid]["title"]
    if iid in BY_VARIANT:
        p = BY_VARIANT[iid]
        v = next((v for v in p["variants"] if v["id"] == iid), None)
        return f'{p["title"]} — {v["title"]}' if v else p["title"]
    return iid

def _ucp_envelope():
    """The `ucp` response envelope every checkout/order response MUST carry
    (ucp.json $defs response_checkout_schema: version + payment_handlers required).
    At 2026-04-08 the envelope also declares the discount extension so the oracle
    validates responses against the discount-composed checkout schema (the oracle
    composes by $defs['dev.ucp.shopping.checkout'], the 04-08 def-naming convention).
    2026-01-23's discount.json predates that convention ($defs is named plain
    'checkout'), so the oracle cannot compose it: the 01-23 envelope declares only
    checkout, and selfcheck.py separately anchors the discounts subtree to the
    official $defs/discounts_object — both anchors remain the official oracle.
    2026-01-11 responses declare active capabilities as an ARRAY (overview.md
    "Capability Declaration in Responses": name + version, per capability.json
    $defs/response); the schema URL is carried too (legal per $defs/base)."""
    if VERSION == "2026-01-11":
        return {"version": VERSION, "capabilities": [
            {"name": "dev.ucp.shopping.checkout", "version": VERSION,
             "schema": "https://ucp.dev/schemas/shopping/checkout.json"}]}
    caps = {"dev.ucp.shopping.checkout": [
        {"version": VERSION,
         "schema": "https://ucp.dev/schemas/shopping/checkout.json"}]}
    if VERSION == "2026-04-08":
        caps["dev.ucp.shopping.discount"] = [
            {"version": VERSION,
             "schema": "https://ucp.dev/schemas/shopping/discount.json",
             "extends": "dev.ucp.shopping.checkout"}]
    # PAYMENT AREA: checkout responses echo the resolved handler (PAY-003)
    return {"version": VERSION, "capabilities": caps,
            "payment_handlers": payment_handlers_registry(response=True)}

LINKS = [{"type": "terms_of_service", "url": "https://spck.dev/fixture/tos"},
         {"type": "privacy_policy", "url": "https://spck.dev/fixture/privacy"}]

def _missing_agent_err():
    """A request without a UCP-Agent header has a MISSING platform profile URL —
    overview.md Negotiation Errors: invalid_profile_url -> REST 400 (NEG-005).
    04-08 uses the flat transport-error body; 01-era keeps the legacy detail shape
    (its registers carry no such error-table row)."""
    if VERSION == "2026-04-08":
        return 400, {"code": "invalid_profile_url",
                     "content": "UCP-Agent header with a platform profile URL is required"}
    return 400, {"detail": "UCP-Agent header is required"}

def _err(status, detail):
    """Structured error body: a populated `detail` string (the shape VAL-006 requires
    of 400 responses, matching the reference server's error envelope)."""
    return status, {"detail": detail}

def _build_line_items(reqs):
    """Resolve requested line_items against the seed catalog + stock.
    Returns (line_items, None) or (None, (status, error_payload))."""
    if not isinstance(reqs, list) or not reqs:
        return None, _err(400, "line_items is required and must be a non-empty array")
    out = []
    for i, li in enumerate(reqs):
        if not isinstance(li, dict):
            return None, _err(400, f"line_items[{i}] must be an object")
        iid = (li.get("item") or {}).get("id")
        if not iid:
            return None, _err(400, f"line_items[{i}].item.id is required")
        if iid not in BY_ID and iid not in BY_VARIANT:
            return None, _err(400, f"Unknown item id: {iid}")
        try:
            qty = int(li.get("quantity", 1) or 1)
        except (TypeError, ValueError):
            return None, _err(400, f"line_items[{i}].quantity must be an integer")
        if qty < 1:
            return None, _err(400, f"line_items[{i}].quantity must be >= 1")
        if qty > _stock(iid):
            return None, _err(400, f"Insufficient stock for item {iid} "
                                   f"(requested {qty}, available {_stock(iid)})")
        price = _unit_price(iid)
        out.append({"id": li.get("id") or f"li_{i+1}",
                    "item": {"id": iid, "title": _title(iid), "price": price},
                    "quantity": qty,
                    "totals": [{"type": "subtotal", "display_text": "Subtotal",
                                "amount": price * qty}]})
    return out, None

def _match_code(code):
    """Case-insensitive seeded-code lookup (discount.md: codes are case-insensitive).
    Returns (canonical_key, rule) or (None, None)."""
    for k, rule in DISCOUNT_CODES.items():
        if isinstance(code, str) and code.upper() == k:
            return k, rule
    return None, None

def _compute_discounts(sess):
    """Evaluate the session's submitted codes + automatic rules.
    Returns (applied, order_disc, line_disc) — all amounts POSITIVE integers;
    line_disc maps line-item index -> discount amount."""
    lines = sess["line_items"]
    subtotal = sum(li["totals"][0]["amount"] for li in lines)
    applied, order_disc, line_disc = [], 0, {}
    for code in sess.get("codes", []):
        key, rule = _match_code(code)
        if not rule:
            continue                                    # unknown: echoed, never applied
        if rule["kind"] == "order_pct":
            amt = subtotal * rule["value"] // 100
            applied.append({"code": key, "title": rule["title"], "amount": amt})
            order_disc += amt
        elif rule["kind"] == "order_flat":
            amt = min(rule["value"], subtotal - order_disc)
            applied.append({"code": key, "title": rule["title"], "amount": amt})
            order_disc += amt
        elif rule["kind"] == "item_pct":
            allocations, total = [], 0
            for i, li in enumerate(lines):
                pid = li["item"]["id"]
                pid = BY_VARIANT[pid]["id"] if pid in BY_VARIANT else pid
                if pid == rule["product"]:
                    a = li["item"]["price"] * li["quantity"] * rule["value"] // 100
                    if a > 0:
                        allocations.append({"path": f"$.line_items[{i}]", "amount": a})
                        line_disc[i] = line_disc.get(i, 0) + a
                        total += a
            if total > 0:                               # no eligible items -> not applied
                applied.append({"code": key, "title": rule["title"], "amount": total,
                                "method": "each", "allocations": allocations})
    if subtotal >= AUTO_THRESHOLD:                      # automatic: true, NO code field
        applied.append({"title": AUTO_TITLE, "amount": AUTO_AMOUNT, "automatic": True})
        order_disc += AUTO_AMOUNT
    return applied, order_disc, line_disc

def _codes_from(body):
    """Extract discounts.codes from a request body.
    Returns (codes_list | None if not submitted, error)."""
    d = body.get("discounts")
    if d is None:
        return None, None
    if not isinstance(d, dict) or not isinstance(d.get("codes", []), list) \
       or any(not isinstance(c, str) for c in d.get("codes", [])):
        return None, _err(400, "discounts.codes must be an array of strings")
    return list(d.get("codes", [])), None

def checkout_body(sess):
    """Render a session as a spec-valid checkout response (checkout.json requires
    ucp, id, line_items, status, currency, totals, links). Discount rendering follows
    the pinned per-version sign convention: 2026-04-08 totals[] discount entries are
    NEGATIVE (schema-enforced) and item discounts appear as line-item totals entries;
    2026-01-23/01-11 amounts are positive and item discounts populate
    line_items[].discount (invariant: totals[items_discount] == sum of those)."""
    applied, order_disc, line_disc = _compute_discounts(sess)
    lines = [dict(li) for li in sess["line_items"]]
    subtotal = sum(li["totals"][0]["amount"] for li in lines)
    items_disc = sum(line_disc.values())
    totals = [{"type": "subtotal", "display_text": "Subtotal", "amount": subtotal}]
    if VERSION == "2026-04-08":
        # Sub-lines (checkout.md "Sub-Lines", 04-08 only): itemize the subtotal entry
        # per line item; the invariant sum(lines[].amount) == parent amount (TOT-017)
        # holds by construction (subtotal IS the sum of the line-item subtotals).
        totals[0]["lines"] = [{"display_text": li["item"]["title"],
                               "amount": li["totals"][0]["amount"]} for li in lines]
        for i, a in line_disc.items():
            lines[i]["totals"] = lines[i]["totals"] + [
                {"type": "items_discount", "display_text": "Item discount", "amount": -a}]
        if items_disc:
            totals.append({"type": "items_discount", "display_text": "Item discounts",
                           "amount": -items_disc})
        if order_disc:
            totals.append({"type": "discount", "display_text": "Discount",
                           "amount": -order_disc})
    else:
        for i, a in line_disc.items():
            lines[i]["discount"] = a
        if items_disc:
            totals.append({"type": "items_discount", "display_text": "Item discounts",
                           "amount": items_disc})
        if order_disc:
            totals.append({"type": "discount", "display_text": "Discount",
                           "amount": order_disc})
    totals.append({"type": "total", "display_text": "Total",
                   "amount": max(subtotal - items_disc - order_disc, 0)})
    out = {"ucp": _ucp_envelope(), "id": sess["id"], "status": sess["status"],
           "currency": sess["currency"], "line_items": lines,
           "totals": totals, "links": LINKS}
    if sess.get("codes") or applied:
        out["discounts"] = {"codes": list(sess.get("codes", [])), "applied": applied}
    # discount.md "Rejected discount code": rejection is communicated via messages[]
    # (type:warning, path pointing at the offending codes[] entry); the code is still
    # echoed in discounts.codes but never in discounts.applied.
    rejected = [(i, c) for i, c in enumerate(sess.get("codes", []))
                if _match_code(c)[0] is None]
    messages = [
        {"type": "warning", "code": "discount_code_invalid",
         "path": f"$.discounts.codes[{i}]",
         "content": f"Code '{c}' is not a valid discount code"}
        for i, c in rejected]
    # PAYMENT AREA: escalated sessions carry continue_url (checkout.json: "MUST be
    # provided when status is requires_escalation" — PAY-018) plus the soft-decline
    # error message whose requires_buyer_input severity "contributes to
    # status: requires_escalation" (types/message_error.json; overview.md Scenario B).
    if sess["status"] == "requires_escalation":
        out["continue_url"] = sess.get(
            "continue_url", f"https://spck.dev/fixture/3ds/{sess['id']}")
        messages.append(
            {"type": "error", "code": "requires_3ds",
             "content": "The bank requires additional verification "
                        "to complete this payment.",
             "severity": "requires_buyer_input"})
    if messages:
        out["messages"] = messages
    if VERSION != "2026-04-08":
        # AP2 merchant authorization on checkout responses (PAY-035 is a 01-23/01-11
        # MUST; the id does not exist in the 04-08 register, so 04-08 stays lean)
        out["ap2"] = {"merchant_authorization": merchant_authorization()}
        # buyer-consent extension (01-era, DSC-019): consent submitted as boolean
        # states inside buyer.consent on create/update is persisted and echoed back
        # (the oracle anchor is buyer_consent.json $defs/buyer — see selfcheck.py)
        if sess.get("buyer") is not None:
            out["buyer"] = sess["buyer"]
    if VERSION == "2026-01-11":
        # 2026-01-11 checkout.json REQUIRES the root `payment` object on responses
        # (payment.json: handlers[] of shopping/types/payment_handler.json entries)
        out["payment"] = {"handlers": [payment_handler_01era()]}
    if sess.get("order"):
        out["order"] = sess["order"]        # order_confirmation: id + permalink_url
    return out

def create_checkout(body, headers=None):
    """POST /checkout-sessions. Enforces: UCP-Agent required (CHK-052), line_items
    required (CHK-018), known items + stock (VAL-003/VAL-001), idempotency-key
    conflict -> 409 (IDM-004)."""
    headers = headers or {}
    if not headers.get("UCP-Agent"):
        return _missing_agent_err()
    if VERSION == "2026-04-08":
        # negotiation precedes request processing (discovery/negotiation area);
        # 04-08-scoped: the 01-era registers map these errors differently
        neg = negotiate_platform(headers.get("UCP-Agent"))
        if neg:
            return neg
    elif _agent_profile_url(headers.get("UCP-Agent")) == SIM_LEGACY_VERSION:
        # 01-era negotiation failure (overview.md "Error Handling" — NEG-013/NEG-014/
        # ERR-007): the business validates the platform's version and, when
        # incompatible, returns the spec's error envelope verbatim in shape:
        # status requires_escalation + a type:error message (the pinned example uses
        # code version_unsupported, severity requires_buyer_input). HTTP 400 follows
        # the official 01-23 conformance suite's incompatible-version assertion.
        return 400, {"status": "requires_escalation",
                     "messages": [{"type": "error", "code": "version_unsupported",
                                   "content": "Protocol version 1999-01-01 is not "
                                              f"supported. This business supports {VERSION}.",
                                   "severity": "requires_buyer_input"}]}
    if body is None or not isinstance(body, dict):
        return _err(400, "request body must be a JSON object")
    if "line_items" not in body:
        return _err(400, "line_items is required on create")
    key = headers.get("idempotency-key")
    fp = json.dumps(body, sort_keys=True)
    with _LOCK:
        if key and key in IDEM:
            prev_fp, prev_status, prev_payload = IDEM[key]
            if prev_fp != fp:
                return _err(409, "idempotency-key conflict: same key with a different body")
            return prev_status, prev_payload           # replay the original result
    line_items, err = _build_line_items(body.get("line_items"))
    if err:
        return err
    codes, err = _codes_from(body)
    if err:
        return err
    sess = {"id": "chk_" + uuid.uuid4().hex[:12], "status": "ready_for_complete",
            "currency": body.get("currency", "USD"), "line_items": line_items,
            "codes": codes or []}
    # WEBHOOK/EVENTS area (every served version): remember where THIS platform
    # wants order events (order.md Webhook URL Configuration — the platform
    # profile's order capability config, at 01-era and 04-08 alike; loopback-only
    # fetch, see resolve_platform_webhook_url). Internal keys, never rendered —
    # checkout_body builds its output explicitly.
    wu = resolve_platform_webhook_url(headers.get("UCP-Agent"))
    if wu:
        sess["webhook_url"] = wu
        sess["base"] = "http://" + headers.get("Host", "localhost")
    if VERSION != "2026-04-08" and isinstance(body.get("buyer"), dict):
        sess["buyer"] = body["buyer"]       # buyer-consent extension echo (DSC-019)
    with _LOCK:
        SESSIONS[sess["id"]] = sess
        result = 201, checkout_body(sess)
        if key:
            IDEM[key] = (fp, *result)
    return result

def get_checkout(sid, headers=None):
    """GET /checkout-sessions/{id}."""
    sess = SESSIONS.get(sid)
    if not sess:
        return _err(404, f"checkout session not found: {sid}")
    return 200, checkout_body(sess)

def update_checkout(sid, body, headers=None):
    """PUT /checkout-sessions/{id}. Enforces: line_items required (CHK-018/CHK-038),
    stock revalidation -> 400 (VAL-002), completed/canceled sessions immutable.
    Top-level id: REQUIRED on 01-era updates (CHK-016); at 2026-04-08 it is
    ucp_request:OMIT (CHK-035) — tolerated only if it matches the path id."""
    headers = headers or {}
    if not headers.get("UCP-Agent"):
        return _missing_agent_err()
    sess = SESSIONS.get(sid)
    if not sess:
        return _err(404, f"checkout session not found: {sid}")
    if body is None or not isinstance(body, dict):
        return _err(400, "request body must be a JSON object")
    if VERSION != "2026-04-08" and not body.get("id"):
        return _err(400, "top-level id is required on update requests")
    if body.get("id") and body["id"] != sid:
        return _err(400, f"body id {body['id']} does not match path id {sid}")
    if sess["status"] in ("completed", "canceled"):
        return _err(409, f"checkout session is {sess['status']} and cannot be updated")
    if "line_items" not in body:
        return _err(400, "line_items is required on update")
    line_items, err = _build_line_items(body.get("line_items"))
    if err:
        return err
    codes, err = _codes_from(body)
    if err:
        return err
    sess["line_items"] = line_items
    if codes is not None:                   # submitted codes REPLACE the previous set;
        sess["codes"] = codes               # an empty array clears them (DSC-002)
    if VERSION != "2026-04-08" and isinstance(body.get("buyer"), dict):
        sess["buyer"] = body["buyer"]       # submitted buyer state replaces (DSC-019)
    if "currency" in body:
        sess["currency"] = body["currency"]
    return 200, checkout_body(sess)

ORDERS = {}         # order id -> order state

def _payment_tokens(body):
    """Raw credential tokens inside a complete request's payment.instruments."""
    insts = ((body or {}).get("payment") or {}).get("instruments") or []
    return [t for t in ((i.get("credential") or {}).get("token") for i in insts
                        if isinstance(i, dict)) if isinstance(t, str)]

def order_body(order):
    """Render a stored order as a spec-valid order response (order.json requires ucp,
    id, checkout_id, permalink_url, line_items, fulfillment, currency, totals;
    adjustments is the post-order event log — present in every supported version).
    2026-01-11 order responses carry the array-form envelope (ucp.json
    $defs/response_order: version + capabilities ARRAY)."""
    if VERSION == "2026-01-11":
        ucp = {"version": VERSION, "capabilities": [
            {"name": "dev.ucp.shopping.order", "version": VERSION,
             "schema": "https://ucp.dev/schemas/shopping/order.json"}]}
    else:
        ucp = {"version": VERSION,
               "capabilities": {"dev.ucp.shopping.order": [
                   {"version": VERSION,
                    "schema": "https://ucp.dev/schemas/shopping/order.json"}]}}
    return {"ucp": ucp,
            **{k: order[k] for k in ("id", "checkout_id", "permalink_url", "currency",
                                     "line_items", "fulfillment", "adjustments",
                                     "totals")}}

def complete_checkout(sid, body, headers=None):
    """POST /checkout-sessions/{id}/complete -> 'completed' + an order confirmation.
    The seeded FAIL_TOKEN credential is declined with 402 (VAL-004); credentials are
    never echoed back (PAY-009)."""
    sess = SESSIONS.get(sid)
    if not sess:
        return _err(404, f"checkout session not found: {sid}")
    if sess["status"] == "canceled":
        return _err(409, "checkout session is canceled and cannot be completed")
    if sess["status"] == "completed":
        return _err(409, "checkout session is already completed")
    if FAIL_TOKEN in _payment_tokens(body):
        return _err(402, "payment declined by the payment handler")
    # PAYMENT AREA: 3DS/SCA soft-decline (overview.md Scenario B). HTTP 200 with
    # status=requires_escalation; checkout_body adds continue_url + the requires_3ds
    # message. A retried completion (normal token) then completes the session.
    if ESCALATE_TOKEN in _payment_tokens(body):
        with _LOCK:
            sess["status"] = "requires_escalation"
            sess["continue_url"] = f"https://spck.dev/fixture/3ds/{sid}"
        return 200, checkout_body(sess)
    oid = "ord_" + uuid.uuid4().hex[:12]
    permalink = f"https://spck.dev/fixture/orders/{oid}"
    checkout = checkout_body(sess)          # totals before flipping status
    order = {"id": oid, "checkout_id": sid, "permalink_url": permalink,
             "currency": sess["currency"],
             "line_items": [{"id": li["id"], "item": li["item"],
                             "quantity": {"original": li["quantity"],
                                          "total": li["quantity"], "fulfilled": 0},
                             "totals": li["totals"], "status": "processing"}
                            for li in sess["line_items"]],
             "fulfillment": {"expectations": [], "events": []},
             "adjustments": [],
             "totals": checkout["totals"]}
    with _LOCK:
        ORDERS[oid] = order
        order["_webhook_url"] = sess.get("webhook_url")   # internal, never rendered
        order["_base"] = sess.get("base")
        sess["status"] = "completed"
        sess["order"] = {"id": oid, "permalink_url": permalink}
    # WEBHOOK/EVENTS area: "MUST send 'Order created' event with fully populated
    # order entity" — the payload IS the order response snapshot (order.md Events).
    if order.get("_webhook_url"):
        _deliver_webhook(order["_webhook_url"], webhook_event_payload(order),
                         order["_base"])
    return 200, checkout_body(sess)

def get_order(oid, headers=None):
    """GET /orders/{id}."""
    order = ORDERS.get(oid)
    if not order:
        return _err(404, f"order not found: {oid}")
    return 200, order_body(order)

# ---- ORDER area: test-only post-order adjustment hook (2026-04-08 only) ------
# TEST-ONLY (precedent: the Flower golden's /testing/simulate-shipping): a real
# merchant reaches this state through its own ops tooling; the conformance golden
# needs a wire-drivable way to exhibit it. POST /testing/orders/{id}/adjust appends
# a REDUCTION adjustment (refund/cancellation/return) per the pinned 04-08 spec:
#   * adjustment line-item quantities are SIGNED — negative for reductions
#     (order.md "Quantities and amounts are signed", ORD-007);
#   * adjustment totals are SIGNED — negative for money returned (ORD-009);
#   * the affected order line item's quantity.total is reduced, its status derived
#     per order.md Status Derivation (total==0 -> "removed"), and the line item is
#     RETAINED in line_items — "all line items that ever existed" (ORD-002).
# The 04-08 branch is SIGNED semantics; the 2026-01-23/2026-01-11 adjustment schema
# is UNSIGNED (line_items[].quantity minimum 1, no totals — an optional positive
# `amount`) and the 01-era order_line_item status enum has no "removed", so the
# 01-era branch appends the append-only LOG ENTRY (ORD-007: id, type, occurred_at,
# status required) WITHOUT rewriting the order's line items — which stay retained
# verbatim as the immutable source of truth for what was ordered (ORD-006).
def simulate_order_adjustment(oid, body, headers=None):
    """POST /testing/orders/{id}/adjust  {line_item_id, quantity>=1, [type]}."""
    if VERSION != "2026-04-08":
        return _simulate_adjustment_01era(oid, body)
    order = ORDERS.get(oid)
    if not order:
        return _err(404, f"order not found: {oid}")
    body = body if isinstance(body, dict) else {}
    lid = body.get("line_item_id")
    li = next((x for x in order["line_items"] if x["id"] == lid), None)
    if not li:
        return _err(400, f"unknown line_item_id: {lid}")
    try:
        qty = int(body.get("quantity", 1))
    except (TypeError, ValueError):
        return _err(400, "quantity must be an integer")
    if qty < 1 or qty > li["quantity"]["total"]:
        return _err(400, f"quantity must be between 1 and the line item's "
                         f"remaining total ({li['quantity']['total']})")
    atype = body.get("type") or "refund"
    amount = li["item"]["price"] * qty
    with _LOCK:
        li["quantity"]["total"] -= qty
        q = li["quantity"]
        li["status"] = ("removed" if q["total"] == 0
                        else "fulfilled" if q["fulfilled"] == q["total"]
                        else "partial" if q["fulfilled"] > 0 else "processing")
        order["adjustments"].append({
            "id": "adj_" + uuid.uuid4().hex[:12], "type": atype,
            "occurred_at": "2026-04-08T12:00:00Z", "status": "completed",
            "line_items": [{"id": lid, "quantity": -qty}],   # signed: reduction < 0
            "totals": [{"type": "total", "display_text": "Refund",
                        "amount": -amount}],                  # signed: money returned
            "description": f"Test-driven {atype} of {qty} unit(s)"})
    # WEBHOOK/EVENTS area: "MUST send full order entity on updates (not
    # incremental deltas)" — the update event is the same current-state snapshot.
    if order.get("_webhook_url"):
        _deliver_webhook(order["_webhook_url"], webhook_event_payload(order),
                         order["_base"])
    return 200, order_body(order)

def _simulate_adjustment_01era(oid, body):
    """01-era adjust hook: append an adjustment per the PINNED 01-era
    types/adjustment.json — required {id, type, occurred_at, status}; line_items[]
    quantities UNSIGNED (minimum 1); `amount` an optional positive integer. The
    order's own line_items are NOT touched (ORD-006 retention)."""
    order = ORDERS.get(oid)
    if not order:
        return _err(404, f"order not found: {oid}")
    body = body if isinstance(body, dict) else {}
    lid = body.get("line_item_id")
    li = next((x for x in order["line_items"] if x["id"] == lid), None)
    if not li:
        return _err(400, f"unknown line_item_id: {lid}")
    try:
        qty = int(body.get("quantity", 1))
    except (TypeError, ValueError):
        return _err(400, "quantity must be an integer")
    if qty < 1 or qty > li["quantity"]["total"]:
        return _err(400, f"quantity must be between 1 and the line item's "
                         f"total ({li['quantity']['total']})")
    atype = body.get("type") or "refund"
    with _LOCK:
        order["adjustments"].append({
            "id": "adj_" + uuid.uuid4().hex[:12], "type": atype,
            "occurred_at": "2026-01-23T12:00:00Z", "status": "completed",
            "line_items": [{"id": lid, "quantity": qty}],   # unsigned (minimum 1)
            "amount": li["item"]["price"] * qty,            # positive minor units
            "description": f"Test-driven {atype} of {qty} unit(s)"})
    return 200, order_body(order)

# 01-era ORDER area test-only hook (precedent: the Flower golden's
# /testing/simulate-shipping): appends a fulfillment EVENT to the order's
# append-only shipment log per the pinned 01-era types/fulfillment_event.json —
# required {id, occurred_at, type, line_items}; tracking fields are carried since
# type != processing ("required if type != processing" per the field description).
def simulate_order_fulfillment(oid, body, headers=None):
    """POST /testing/orders/{id}/fulfill  {line_item_id, quantity>=1, [type]}."""
    if VERSION == "2026-04-08":
        return _err(404, "testing fulfillment hook is served in 01-era modes only")
    order = ORDERS.get(oid)
    if not order:
        return _err(404, f"order not found: {oid}")
    body = body if isinstance(body, dict) else {}
    lid = body.get("line_item_id")
    li = next((x for x in order["line_items"] if x["id"] == lid), None)
    if not li:
        return _err(400, f"unknown line_item_id: {lid}")
    try:
        qty = int(body.get("quantity", 1))
    except (TypeError, ValueError):
        return _err(400, "quantity must be an integer")
    remaining = li["quantity"]["total"] - li["quantity"]["fulfilled"]
    if qty < 1 or qty > remaining:
        return _err(400, f"quantity must be between 1 and the line item's "
                         f"unfulfilled total ({remaining})")
    etype = body.get("type") or "shipped"
    eid = "ful_" + uuid.uuid4().hex[:12]
    with _LOCK:
        order["fulfillment"].setdefault("events", []).append({
            "id": eid, "occurred_at": "2026-01-23T12:00:00Z", "type": etype,
            "line_items": [{"id": lid, "quantity": qty}],
            "tracking_number": f"SPCK{eid[-8:].upper()}",
            "tracking_url": f"https://spck.dev/fixture/track/{eid}"})
        li["quantity"]["fulfilled"] += qty
        q = li["quantity"]
        li["status"] = ("fulfilled" if q["fulfilled"] >= q["total"]
                        else "partial" if q["fulfilled"] > 0 else "processing")
    return 200, order_body(order)

def cancel_checkout(sid, headers=None):
    """POST /checkout-sessions/{id}/cancel -> status 'canceled'; a completed
    checkout is immutable (CHK-012) -> 4xx."""
    sess = SESSIONS.get(sid)
    if not sess:
        return _err(404, f"checkout session not found: {sid}")
    if sess["status"] == "completed":
        return _err(409, "checkout session is completed and cannot be canceled")
    sess["status"] = "canceled"
    return 200, checkout_body(sess)

# Lookup batch cap (lookup.md: implementations MAY enforce a maximum batch size and
# MUST reject requests exceeding it — HTTP 400 request_too_large / JSON-RPC -32602).
# 25 comfortably honors the SHOULD-accept-at-least-10.
MAX_LOOKUP_BATCH = 25

def catalog_error(capability, code, content, severity="recoverable"):
    """An error_response envelope (types/error_response.json): ucp.status=error +
    a non-empty messages[]. Used for catalog rejections (input-less search, batch cap)."""
    return {"ucp": {"version": VERSION, "status": "error",
                    "capabilities": {capability: [{"version": VERSION}]}},
            "messages": [{"type": "error", "code": code, "content": content,
                          "severity": severity}]}

def search_query_valid(query):
    """The fixture's implementation-defined search-input rule (search.md allows e.g.
    'requiring query, rejecting empty query strings'): a non-empty query string."""
    return isinstance(query, str) and bool(query.strip())

DEFAULT_PAGE_LIMIT = 10        # pagination.json: limit default 10 (rest.md item 3)

def _cursor_make(offset):
    """Opaque continuation cursor (base64 keyset token, per search.md pagination)."""
    return _b64url(f"offset:{offset}".encode())

def _cursor_offset(cursor):
    """Decode a cursor back to an offset; None cursor -> 0; garbage -> None (invalid)."""
    if cursor is None:
        return 0
    try:
        pad = "=" * (-len(cursor) % 4)
        tag, off = base64.urlsafe_b64decode(cursor + pad).decode().split(":", 1)
        if tag != "offset" or int(off) < 0:
            return None
        return int(off)
    except Exception:
        return None

def search_response(query, limit=None, cursor=None):
    """Cursor-paginated search (rest.md conformance item 3): default page size 10;
    `cursor` in the response is the NEXT-page continuation, present only when
    has_next_page is true (pagination.json if/then)."""
    q = (query or "").strip().lower()
    hits = [p for p in PRODUCTS if not q or q == "*" or q in p["title"].lower()
            or q in p["description"]["text"].lower()]
    n = DEFAULT_PAGE_LIMIT if limit is None else limit
    off = _cursor_offset(cursor) or 0
    page = hits[off:off + n]
    pagination = {"has_next_page": off + n < len(hits), "total_count": len(hits)}
    if pagination["has_next_page"]:
        pagination["cursor"] = _cursor_make(off + n)
    return {"ucp": {"version": VERSION}, "products": page, "pagination": pagination}

def _variant_matches(v, selected):
    """True when the variant carries EVERY selected {name,label} option."""
    opts = {(o.get("name"), o.get("label")) for o in (v.get("options") or [])}
    return all((s.get("name"), s.get("label")) in opts for s in selected)

def _effective_selection(prod, sel_in, preferences):
    """lookup.md Option Selection: effective selections after relaxation. Valid request
    selections are honored; when no variant matches all of them, drop options from the
    END of `preferences` (or of the selection itself) until a variant matches. With no
    request selections, the featured (first) variant's own options are the effective
    selection."""
    names = {o.get("name") for o in (prod.get("options") or [])}
    sel = [dict(s) for s in (sel_in or [])
           if isinstance(s, dict) and s.get("name") in names]
    if not sel:
        return [dict(o) for o in (prod.get("variants") or [{}])[0].get("options", [])]
    order = [p for p in (preferences or []) if p in {s["name"] for s in sel}]
    order += [s["name"] for s in sel if s["name"] not in order]
    while sel and not any(_variant_matches(v, sel) for v in prod.get("variants") or []):
        dropped = order.pop()                       # relax lowest-priority option first
        sel = [s for s in sel if s["name"] != dropped]
    return sel or [dict(o) for o in (prod.get("variants") or [{}])[0].get("options", [])]

def get_product_response(body):
    """POST /catalog/product (lookup.md get_product): single-resource product detail.
    Returns (status, payload). Product ID -> featured + selection-matching variants;
    Variant ID -> that variant FIRST (featured), selection state from its options;
    unknown id -> HTTP 200 application error (ucp.status=error, unrecoverable)."""
    cap = "dev.ucp.shopping.catalog.lookup"
    rid = body.get("id")
    if not isinstance(rid, str) or not rid:
        return 400, catalog_error(cap, "invalid_request", "id is required")
    src = BY_ID.get(rid) or BY_VARIANT.get(rid)
    if not src:
        return 200, catalog_error(cap, "not_found", f"Product not found: {rid}",
                                  severity="unrecoverable")
    prod = json.loads(json.dumps(src))
    variants = prod.get("variants") or []
    if rid in BY_VARIANT and rid != prod["id"]:
        # Variant ID: requested variant is the first element (featured); its own
        # options ARE the selection state (request `selected` is ignored per spec)
        variants.sort(key=lambda v: 0 if v["id"] == rid else 1)   # stable sort
        prod["variants"] = variants
        selected = [dict(o) for o in variants[0].get("options", [])]
    else:
        selected = _effective_selection(prod, body.get("selected"),
                                        body.get("preferences"))
        matching = [v for v in variants if _variant_matches(v, selected)]
        prod["variants"] = matching or variants[:1]
    if prod.get("options"):        # MUST include product.selected when configurable
        prod["selected"] = selected
    return 200, {"ucp": {"version": VERSION, "capabilities": {cap: [{"version": VERSION}]}},
                 "product": prod}

def _detail(p, requested):
    """Lookup returns DETAIL products whose variants carry `inputs` — one
    input_correlation entry per REQUEST identifier that resolved to the variant
    (lookup.md client correlation): `exact` for the variant's own id, `featured`
    when the parent product id resolved here."""
    d = json.loads(json.dumps(p))
    for v in d["variants"]:
        ins = [{"id": r, "match": "exact" if r == v["id"] else "featured"}
               for r in requested if r in (p["id"], v["id"])]
        v["inputs"] = ins or [{"id": p["id"], "match": "featured"}]
    return d

def lookup_response(ids):
    """Batch lookup per lookup.md: duplicate request identifiers are deduplicated,
    multiple identifiers resolving to the same product return it ONCE, unknown
    identifiers simply yield fewer products (partial result, still HTTP 200)."""
    ids = list(dict.fromkeys(ids or []))          # dedup identifiers, keep order
    seen, hits = set(), []
    for i in ids:
        p = BY_ID.get(i) or BY_VARIANT.get(i)
        if p and p["id"] not in seen:             # same product resolved twice -> once
            seen.add(p["id"]); hits.append(p)
    return {"ucp": {"version": VERSION}, "products": [_detail(p, ids) for p in hits]}

def mcp_dispatch(rpc):
    """Handle a JSON-RPC `tools/call` (the UCP MCP transport, per checkout-mcp.md):
    route to a shopping operation and wrap the UCP object in result.structuredContent.
    Reuses the exact same handlers as REST, so both transports return identical payloads."""
    rid = (rpc or {}).get("id")
    def ok(payload): return {"jsonrpc": "2.0", "id": rid,
                             "result": {"structuredContent": payload}}
    def err(code, msg): return {"jsonrpc": "2.0", "id": rid,
                                "error": {"code": code, "message": msg}}
    if (rpc or {}).get("method") != "tools/call":
        return err(-32601, "only tools/call is supported")
    params = rpc.get("params") or {}
    name, args = params.get("name"), (params.get("arguments") or {})
    if not ((args.get("meta") or {}).get("ucp-agent")):     # required on every request
        return err(-32602, "meta.ucp-agent is required")
    cat = args.get("catalog") or {}
    if name == "search_catalog":
        if not search_query_valid(cat.get("query")):    # same MUST-validate rule as REST
            return err(-32602, "search requires at least one input (non-empty query)")
        return ok(search_response(cat.get("query")))
    if name == "lookup_catalog":
        ids = cat.get("ids") or ([cat["id"]] if cat.get("id") else [])
        if len(ids) > MAX_LOOKUP_BATCH:                 # lookup.md: JSON-RPC -32602
            return err(-32602, f"lookup batch of {len(ids)} exceeds the maximum of "
                               f"{MAX_LOOKUP_BATCH} identifiers")
        return ok(lookup_response(ids))
    if name == "create_cart":
        return ok(cart_response(args.get("cart") or {}))
    return err(-32601, f"unknown tool: {name}")

class _H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj, extra_headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        if VERSION == "2026-04-08":
            # signatures.md REST Response Signing: every JSON response carries
            # Content-Digest + Signature-Input + Signature (RFC 9421, ES256)
            for hn, hv in sign_response(code, body).items():
                self.send_header(hn, hv)
        # permissive CORS: this is a TEST golden — it must be drivable from the
        # browser-based /tool (and its committed smoke tests) on any local origin
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()
        self.wfile.write(body)
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         self.headers.get("Access-Control-Request-Headers", "*") or "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()
    def _base(self):
        host = self.headers.get("Host") or f"localhost:{self.server.server_address[1]}"
        return f"http://{host}"
    def _raw(self):
        if not hasattr(self, "_raw_body"):
            n = int(self.headers.get("Content-Length", 0) or 0)
            self._raw_body = self.rfile.read(n) if n else b""
        return self._raw_body
    def _body(self):
        raw = self._raw()
        try: return json.loads(raw) if raw else {}
        except Exception: return None
    def _sig_rejected(self):
        """signatures.md request verification: when a request carries RFC 9421
        signature headers (2026-04-08), verify them; respond with the spec's
        signature error codes on failure. Unsigned requests are untouched."""
        if VERSION != "2026-04-08" or not self.headers.get("Signature-Input"):
            return False
        err = None if not VERIFY_SIGNATURES else verify_signed_request(self.command, self.path,
                                    dict(self.headers.items()), self._raw())
        if err:
            self._send(*err)
            return True
        return False
    def _checkout_scope_denied(self, op):
        """IDL-013 (01-era): enforce the checkout_session capability scope on `op`
        when --require-checkout-scope is set. Sends the challenge and returns True
        when denied, else False. No-op in 04-08 mode / when the flag is off."""
        denied = require_checkout_scope(dict(self.headers.items()), self._base(), op)
        if denied:
            st, hdrs, payload = denied
            self._send(st, payload, hdrs)
            return True
        return False

    def do_GET(self):
        path = self.path.rstrip("/").split("?")[0]
        if path == "/__echo":
            # test-only: echo the received request headers, so browser smoke tests
            # can assert what actually arrived on the wire (custom headers etc.)
            return self._send(200, {"headers": {k.lower(): v for k, v in self.headers.items()}})
        if path == "/.well-known/ucp":
            # DISC-003: profile responses carry the required Cache-Control policy.
            # profile_served applies the DISC-014 loopback repoint when --local-spec-urls
            # is set (otherwise it is the real authority-origin profile, unchanged).
            return self._send(200, profile_served(self._base()),
                              {"Cache-Control": PROFILE_CACHE_CONTROL})
        if path.startswith("/__localspec/"):
            # DISC-014 reference-gate loopback targets: every declared spec/schema URL
            # resolves 200 here (valid JSON), except the BREAK_SPEC_URL sentinel (404).
            if path == _LOCAL_BROKEN_PATH:
                return self._send(404, {"error_code": "not_found"})
            return self._send(200, {"ok": True})
        if path == "/.well-known/oauth-authorization-server":
            # identity-linking: RFC 8414 authorization server metadata on the
            # business domain (04-08 IDL-016; 01-era IDL-006 — every version)
            return self._send(200, oauth_authorization_server_metadata(self._base()))
        if path == "/.well-known/oauth-protected-resource" and VERSION == "2026-04-08":
            # RFC 9728 protected resource metadata (04-08 identity rework only)
            return self._send(200, oauth_protected_resource_metadata(self._base()))
        if path == "/oauth2/authorize":
            # OAUTH area: authorization endpoint (auto-granting test AS)
            q = dict(urllib.parse.parse_qsl(self.path.partition("?")[2]))
            st, hdrs, payload = oauth_authorize(q, self._base())
            return self._send(st, payload, hdrs)
        if path == "/orders" and VERSION == "2026-04-08":
            # OAUTH area: user order history, gated by dev.ucp.shopping.order:read
            st, hdrs, payload = list_orders(dict(self.headers.items()), self._base())
            return self._send(st, payload, hdrs)
        if path.startswith("/checkout-sessions/"):
            sid = path.split("/")[2]
            if self._checkout_scope_denied("read"):   # IDL-013 Get gate (01-era)
                return
            return self._send(*get_checkout(sid, self.headers))
        if path.startswith("/orders/"):
            # ORD-012: the business MUST authenticate requests to order data before
            # returning a response. Only enforced under --require-order-auth (04-08),
            # so the DEFAULT golden's unauthenticated order checks stay sound.
            if REQUIRE_ORDER_AUTH and VERSION == "2026-04-08":
                denied = require_identity(dict(self.headers.items()),
                                          ORDER_READ_SCOPES, self._base())
                if denied:
                    st, hdrs, payload = denied
                    return self._send(st, payload, hdrs)
            return self._send(*get_order(path.split("/")[2], self.headers))
        if path.startswith("/carts/") and path.count("/") == 2 \
           and VERSION == "2026-04-08":      # cart exists only in 04-08
            return self._send(*get_cart(path.split("/")[2]))
        self._send(404, {"error_code": "not_found"})
    def do_PUT(self):
        if self._sig_rejected():
            return
        body = self._body()
        path = self.path.rstrip("/")
        if body is None:
            return self._send(400, {"detail": "request body is not valid JSON"})
        if path.startswith("/checkout-sessions/") and path.count("/") == 2:
            sid = path.split("/")[2]
            if self._checkout_scope_denied("update"):   # IDL-013 Update gate (01-era)
                return
            return self._send(*update_checkout(sid, body, self.headers))
        if path.startswith("/carts/") and path.count("/") == 2 \
           and VERSION == "2026-04-08":      # cart exists only in 04-08 (CART-017)
            return self._send(*update_cart(path.split("/")[2], body, self.headers))
        self._send(404, {"error_code": "not_found"})
    def do_POST(self):
        if self._sig_rejected():
            return
        path = self.path.rstrip("/")
        if path in ("/oauth2/token", "/oauth2/revoke"):
            # OAUTH area: token/revocation endpoints consume
            # application/x-www-form-urlencoded (RFC 6749 §4.1.3 / RFC 7009 §2.1),
            # NOT the JSON the rest of the fixture speaks — handled before _body()
            form = dict(urllib.parse.parse_qsl(
                self._raw().decode("utf-8", "replace")))
            fn = oauth_token if path == "/oauth2/token" else oauth_revoke
            st, hdrs, payload = fn(form, dict(self.headers.items()), self._base())
            return self._send(st, payload, hdrs)
        if VERSION == "2026-04-08" and path.startswith("/orders/") \
           and path.endswith("/cancel") and path.count("/") == 3:
            # OAUTH area: post-purchase op gated by the order scopes
            st, hdrs, payload = cancel_order(path.split("/")[2],
                                             dict(self.headers.items()), self._base())
            return self._send(st, payload, hdrs)
        body = self._body()
        if body is None and path != "/checkout-sessions" \
           and not (path.startswith("/checkout-sessions/") and path.endswith(("/complete", "/cancel"))):
            return self._send(400, {"error_code": "invalid_request"})
        if VERSION == "2026-04-08":          # catalog/cart/MCP exist only in 04-08
            if path == "/catalog/search":
                # search.md: MUST validate that requests carry >=1 recognized input;
                # a validation failure is a request error (rest.md two-layer model:
                # 400 = missing required parameters), not an application outcome.
                if not search_query_valid(body.get("query")):
                    return self._send(400, catalog_error(
                        "dev.ucp.shopping.catalog.search", "invalid_request",
                        "search requires at least one input; this implementation "
                        "requires a non-empty `query` string"))
                limit, cursor = body.get("limit"), body.get("cursor")
                if limit is not None and (isinstance(limit, bool)
                                          or not isinstance(limit, int) or limit < 1):
                    return self._send(400, catalog_error(     # pagination.json minimum 1
                        "dev.ucp.shopping.catalog.search", "invalid_request",
                        "limit must be an integer >= 1"))
                if cursor is not None and _cursor_offset(cursor) is None:
                    return self._send(400, catalog_error(
                        "dev.ucp.shopping.catalog.search", "invalid_request",
                        "cursor is not a valid continuation token"))
                return self._send(200, search_response(body.get("query"), limit, cursor))
            if path == "/catalog/lookup":
                ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
                if len(ids) > MAX_LOOKUP_BATCH:
                    return self._send(400, catalog_error(
                        "dev.ucp.shopping.catalog.lookup", "request_too_large",
                        f"lookup batch of {len(ids)} exceeds the maximum of "
                        f"{MAX_LOOKUP_BATCH} identifiers"))
                return self._send(200, lookup_response(ids))
            if path == "/catalog/product":
                return self._send(*get_product_response(body))
            if path == "/carts":
                return self._send(*create_cart(body, self.headers))
        # ORDER area test-only hooks (every version; the handlers render per the
        # serving version's pinned adjustment/fulfillment_event semantics)
        if path.startswith("/testing/orders/") and path.endswith(("/adjust", "/fulfill")):
            parts = path.split("/")          # '', 'testing', 'orders', oid, action
            if len(parts) == 5:
                fn = (simulate_order_adjustment if parts[4] == "adjust"
                      else simulate_order_fulfillment)
                return self._send(*fn(parts[3], body, self.headers))
        if path == "/testing/oauth/mint":
            # TEST-ONLY bad-token mint (IDL-025/042): deterministic (in)valid tokens
            return self._send(*mint_test_token((body or {}).get("kind")))
        if path == "/checkout-sessions":
            if self._checkout_scope_denied("create"):   # IDL-013 Create gate (01-era)
                return
            return self._send(*create_checkout(body, self.headers))
        if path.startswith("/checkout-sessions/"):
            parts = path.split("/")          # '', 'checkout-sessions', sid, action
            if len(parts) == 4 and parts[3] == "complete":
                if self._checkout_scope_denied("complete"):  # IDL-013 Complete gate
                    return
                return self._send(*complete_checkout(parts[2], body, self.headers))
            if len(parts) == 4 and parts[3] == "cancel":
                if self._checkout_scope_denied("cancel"):    # IDL-013 Cancel gate
                    return
                return self._send(*cancel_checkout(parts[2], self.headers))
        if path == "/ucp/mcp" and VERSION == "2026-04-08":   # MCP (JSON-RPC tools/call)
            return self._send(200, mcp_dispatch(body))
        self._send(404, {"error_code": "not_found"})

def main():
    ap = argparse.ArgumentParser(description="Controlled UCP merchant fixture (version-switchable).")
    ap.add_argument("--port", type=int, default=8184)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-verify-signatures", action="store_true",
                    help="MUTANT: skip RFC 9421 request verification (SIG-002 kill-proof)")
    # OAUTH area mutants (validate_oauth_checks.py kill-proof gate ONLY)
    ap.add_argument("--oauth-no-pkce", action="store_true",
                    help="MUTANT: skip PKCE requirement/verification")
    ap.add_argument("--oauth-no-gate", action="store_true",
                    help="MUTANT: serve gated operations without identity")
    ap.add_argument("--oauth-lax-redirect", action="store_true",
                    help="MUTANT: skip redirect_uri exact matching at the token endpoint")
    ap.add_argument("--oauth-no-client-auth", action="store_true",
                    help="MUTANT: skip client authentication at token/revocation")
    ap.add_argument("--oauth-challenge-no-error", action="store_true",
                    help="MUTANT: omit the error param from Bearer challenges")
    ap.add_argument("--oauth-accept-any-token", action="store_true",
                    help="MUTANT: accept a present Bearer token without validating it "
                         "is known/unexpired/unrevoked (IDL-042 expired/revoked kill-proof)")
    ap.add_argument("--no-webhooks", action="store_true",
                    help="MUTANT: never send order-event webhooks (webhook kill-proof)")
    # ORD-012 / IDL-013 config-gated auth MODES (not mutants — opt-in golden modes):
    ap.add_argument("--require-order-auth", action="store_true",
                    help="ORD-012 golden mode: authenticate GET /orders/{id} (04-08)")
    ap.add_argument("--require-checkout-scope", action="store_true",
                    help="IDL-013 golden mode: gate 01-era checkout ops behind "
                         "ucp:scopes:checkout_session")
    ap.add_argument("--checkout-scope-partial", action="store_true",
                    help="MUTANT (with --require-checkout-scope): make ONE checkout op "
                         "demand an extra per-operation scope (IDL-013 violation)")
    ap.add_argument("--local-spec-urls", action="store_true",
                    help="DISC-014 gate: repoint profile spec/schema URLs to loopback "
                         "paths this fixture serves (hermetic reference-gate ONLY)")
    ap.add_argument("--break-spec-url", action="store_true",
                    help="MUTANT (with --local-spec-urls): make ONE spec/schema URL 404")
    ap.add_argument("--spec-version", default=VERSION, choices=SUPPORTED_VERSIONS,
                    help="UCP spec version to serve (default: %(default)s)")
    args = ap.parse_args()
    set_version(args.spec_version)
    if args.no_verify_signatures:
        global VERIFY_SIGNATURES
        VERIFY_SIGNATURES = False
    global OAUTH_ENFORCE_PKCE, OAUTH_GATE, OAUTH_EXACT_REDIRECT, \
        OAUTH_CLIENT_AUTH, OAUTH_CHALLENGE_ERROR, OAUTH_VALIDATE_TOKEN, \
        REQUIRE_ORDER_AUTH, REQUIRE_CHECKOUT_SCOPE, CHECKOUT_SCOPE_PARTIAL, \
        LOCAL_SPEC_URLS, BREAK_SPEC_URL
    if args.oauth_no_pkce:
        OAUTH_ENFORCE_PKCE = False
    if args.oauth_no_gate:
        OAUTH_GATE = False
    if args.oauth_lax_redirect:
        OAUTH_EXACT_REDIRECT = False
    if args.oauth_no_client_auth:
        OAUTH_CLIENT_AUTH = False
    if args.oauth_challenge_no_error:
        OAUTH_CHALLENGE_ERROR = False
    if args.oauth_accept_any_token:
        OAUTH_VALIDATE_TOKEN = False
    if args.require_order_auth:
        REQUIRE_ORDER_AUTH = True
    if args.require_checkout_scope:
        REQUIRE_CHECKOUT_SCOPE = True
    if args.checkout_scope_partial:
        CHECKOUT_SCOPE_PARTIAL = True
    if args.local_spec_urls:
        LOCAL_SPEC_URLS = True
    if args.break_spec_url:
        BREAK_SPEC_URL = True
    if args.no_webhooks:
        global SEND_WEBHOOKS
        SEND_WEBHOOKS = False
    srv = ThreadingHTTPServer((args.host, args.port), _H)
    print(f"controlled merchant on http://{args.host}:{args.port} "
          f"(checkout/order/discount lifecycle"
          f"{' + catalog/cart/mcp' if VERSION == '2026-04-08' else ''}, spec {VERSION})")
    try: srv.serve_forever()
    except KeyboardInterrupt: srv.shutdown()

if __name__ == "__main__":
    main()
