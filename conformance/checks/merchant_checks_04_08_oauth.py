#!/usr/bin/env python3
"""
merchant_checks_04_08_oauth.py — 2026-04-08 identity-linking OAuth-flow checks
(the needs-oauth tier: identity-linking.md's business-side MUSTs that require
actually RUNNING the OAuth 2.0 authorization-code + PKCE flow as the platform).

Version scoping: IDL-015+ exist ONLY in the 2026-04-08 register (the 01-11/01-23
registers stop at IDL-013 and their IDL ids name DIFFERENT requirements — e.g.
IDL-005@01-era = "Businesses must implement OAuth 2.0" vs IDL-005@04-08 = "Public
clients MUST NOT embed a client_secret"), and ORD-014/ORD-025 name webhook rules
at 01-era. Every check is therefore version-locked (versions=V0408) and the file
is named *_04_08_* so coverage attribution stays per-version.

The honest split applied here (see also the register reclassifications):
  * Business-bound MUSTs (the AS the business hosts, its token/revocation
    endpoints, its Bearer challenges) are graded below via oauth_harness.py —
    the suite acts as the platform.
  * Platform-bound MUSTs (IDL-002..014, 031/032, 034/035, 038/039, 048, 051,
    054, 056/057) bind the REQUEST AUTHOR; no merchant check can grade them
    (suite F2 policy). They are reclassified manual in the register with notes.
  * Split rows: IDL-033's business half (metadata issuer == discovery base,
    byte-for-byte) and IDL-055's business half (revoked tokens rejected) are
    graded; their platform halves (don't normalize / do revoke on unlink) are
    noted in the register rows.
  * IDL-025 is graded on its observable core — an unverifiable/tampered token
    on a user-authenticated request MUST be rejected with the invalid_token
    challenge. Whether the merchant checks specifically iss/aud/exp/azp inside
    its token validation is not black-box distinguishable; noted in the row.

Config (config.identity — the fixture's values live in CONTROLLED_CONFIG):
  client_id, redirect_uri, scopes[]           the public (auth 'none' + PKCE) client
  public_none: true                           business advertises 'none' (IDL-023)
  confidential: {client_id, client_secret}    a client_secret_basic client
  loopback_redirect                           a registered 127.0.0.1 redirect URI
  gated: {method, path, scopes[]}             an op gated by config.scopes
  gated_multi: {method, path, scopes[], have_scopes[]}   an op needing 2+ scopes
  continue_url: true                          401 bodies carry an onboarding URL
  resource_metadata: true                     challenges carry resource_metadata

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck/_hdr from there).
"""
import sys, pathlib, urllib.parse
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, Resp, CLEAN, DEVIATION, INCONCLUSIVE  # noqa: E402
from merchant_checks import MCheck, _hdr                      # noqa: E402
import oauth_harness as oh                                    # noqa: E402

V0408 = ("2026-04-08",)
IDL_CAP = "dev.ucp.common.identity_linking"


def _icfg(ctx):
    return ctx.config.get("identity") or {}


def _last_resp(fl):
    """The furthest response a (possibly partial) flow produced — grading it makes
    the earliest broken step surface as the deviation."""
    return fl["token_resp"] or fl["authorize_resp"] or fl["metadata_resp"]


def _flow(ctx, scopes=None):
    ic = _icfg(ctx)
    return oh.code_flow(ctx.base, ic.get("client_id"), ic.get("redirect_uri"),
                        scopes if scopes is not None else (ic.get("scopes") or []))


def _to_code(ctx, client="public", redirect_uri=None, **authz_over):
    """discover + authorize ONLY (no exchange — codes are single-use, so negative
    token probes must mint their own fresh code). Returns
    {metadata, code, verifier, resp} where resp is the furthest response."""
    ic = _icfg(ctx)
    cid = (ic.get("confidential") or {}).get("client_id") if client == "confidential" \
        else ic.get("client_id")
    ruri = redirect_uri or ic.get("redirect_uri")
    out = {"metadata": None, "code": None, "verifier": None, "resp": None,
           "client_id": cid, "redirect_uri": ruri}
    md_r = oh.discover(ctx.base)
    out["resp"] = md_r
    if md_r.status != 200 or not isinstance(md_r.json, dict):
        return out
    out["metadata"] = md_r.json
    verifier, challenge = oh.pkce_pair()
    kw = {"code_challenge": challenge, "code_challenge_method": "S256",
          "state": oh.state_value()}
    kw.update(authz_over)
    a = oh.authorize(md_r.json, cid, ruri, ic.get("scopes") or [], **kw)
    out["resp"], out["verifier"] = a, verifier
    out["code"] = oh.location_params(a).get("code")
    return out


def _bearer(tok):
    h = _hdr()
    h["Authorization"] = "Bearer " + tok
    return h


def _gated(ctx, key="gated", token=None):
    """Hit the config-declared gated operation, optionally with a Bearer token."""
    g = _icfg(ctx).get(key) or {}
    hdrs = _bearer(token) if token else _hdr()
    return fetch(ctx.base, g.get("path", ""), g.get("method", "GET"), None, hdrs)


# ---- IDL-001/015/029: the business implements OAuth 2.0 — the authorization-code
# + PKCE account-linking flow yields a Bearer token granting the standard scopes --
def f_code_flow(ctx):
    return _last_resp(_flow(ctx))


def p_token_ok(r, ctx):
    """A spec-true RFC 6749 §5.1 token response: access_token, token_type Bearer;
    when scope is echoed it retains the requested standard UCP scopes (IDL-029 —
    scope echo is optional per §5.1 when identical to the request)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    t = r.json
    if not (isinstance(t.get("access_token"), str) and t["access_token"]):
        return DEVIATION
    if not (isinstance(t.get("token_type"), str)
            and t["token_type"].lower() == "bearer"):
        return DEVIATION
    if t.get("scope") is not None:
        want = set(_icfg(ctx).get("scopes") or [])
        if not want <= set(str(t["scope"]).split()):
            return INCONCLUSIVE            # narrowed grant is RFC-legal (W2-F8)
    return CLEAN


# ---- IDL-018: iss on the authorization response (RFC 9207) ---------------------
def f_authz_response(ctx):
    fl = _to_code(ctx)
    return fl["resp"]


def p_authz_iss(r, ctx):
    """The authorization response carries code AND iss; iss equals the discovery
    base URI (the business domain — per IDL-033 the metadata issuer MUST equal it
    byte-for-byte, so the base is the correct comparison anchor)."""
    if not (300 <= r.status < 400):        # RFC 6749 pins no redirect status (W2-F7)
        return DEVIATION
    q = oh.location_params(r)
    if not q.get("code"):
        return DEVIATION
    want = {ctx.base}
    try:
        md = oh.discover(ctx.base)
        if isinstance(md, dict) and md.get("issuer"):
            want.add(md["issuer"])         # the metadata issuer is the true anchor
    except Exception:
        pass
    return CLEAN if q.get("iss") in want else DEVIATION


# ---- IDL-033 (business half): metadata issuer == discovery base, byte-for-byte --
def f_metadata(ctx):
    return fetch(ctx.base, oh.WELL_KNOWN, "GET")


def p_issuer_exact(r, ctx):
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("issuer") == ctx.base else DEVIATION


# ---- IDL-050 (business half): client_secret_basic MUST NOT be the only method ---
def p_not_secret_basic_only(r, ctx):
    """IDL-050's MUST NOT is CONDITIONAL ('when serving native or agent
    platforms'). Config identity.serves_public_clients asserts the condition holds
    for this merchant; only then may basic-only deviate (W2-F1)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    methods = r.json.get("token_endpoint_auth_methods_supported")
    if not isinstance(methods, list) or not methods:
        return DEVIATION
    return DEVIATION if set(methods) == {"client_secret_basic"} else CLEAN


# ---- IDL-019/024/036: PKCE enforced at the token endpoint -----------------------
def f_pkce_enforced(ctx):
    """Probe 1: redeem a fresh code with NO code_verifier; probe 2: redeem another
    fresh code with a WRONG verifier. Return the first improperly-handled response,
    else the wrong-verifier rejection (both MUST be invalid_grant)."""
    ic = _icfg(ctx)
    fl = _to_code(ctx)
    if not fl["code"]:
        return fl["resp"]
    r1 = oh.token_request(fl["metadata"], fl["code"], ic.get("redirect_uri"),
                          None, client_id=ic.get("client_id"))
    if not (r1.status == 400 and isinstance(r1.json, dict)
            and r1.json.get("error") == "invalid_grant"):
        return r1
    fl2 = _to_code(ctx)
    if not fl2["code"]:
        return fl2["resp"]
    return oh.token_request(fl2["metadata"], fl2["code"], ic.get("redirect_uri"),
                            fl2["verifier"] + "x", client_id=ic.get("client_id"))


def p_invalid_grant(r, ctx):
    """The spec pins the PKCE-failure error code: 'requests that fail PKCE MUST be
    rejected with invalid_grant' (IDL-024)."""
    if r.status != 400 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("error") == "invalid_grant" else DEVIATION


# ---- IDL-049 / IDL-023: plain PKCE and challenge-less requests rejected ---------
def f_pkce_plain(ctx):
    """An authorization request using code_challenge_method=plain (the verifier IS
    the challenge). Plain MUST NOT be used."""
    verifier, _ = oh.pkce_pair()
    fl = _to_code(ctx, code_challenge=verifier, code_challenge_method="plain")
    return fl["resp"]


def f_no_challenge(ctx):
    """An authorization request with NO code_challenge from the public ('none')
    client — when 'none' is advertised the business MUST require PKCE S256."""
    fl = _to_code(ctx, code_challenge=None, code_challenge_method=None)
    return fl["resp"]


def p_authz_rejected(r, ctx):
    """The authorization request was NOT granted: either answered directly with a
    4xx, or redirected back with an RFC 6749 §4.1.2.1 error (and no code). A code
    grant — or an un-inspectable 200 — means the requirement wasn't enforced."""
    if 400 <= r.status < 500:
        return CLEAN
    if r.status in (302, 303):
        q = oh.location_params(r)
        return CLEAN if q.get("error") and not q.get("code") else DEVIATION
    return DEVIATION


# ---- IDL-020: exact redirect_uri matching at the token endpoint -----------------
def f_redirect_exact(ctx):
    """Authorize with the registered redirect_uri, then redeem the code with a
    trailing-slash variant (the classic normalization bug). MUST be rejected."""
    ic = _icfg(ctx)
    fl = _to_code(ctx)
    if not fl["code"]:
        return fl["resp"]
    return oh.token_request(fl["metadata"], fl["code"],
                            (ic.get("redirect_uri") or "") + "/",
                            fl["verifier"], client_id=ic.get("client_id"))


# ---- IDL-021: loopback redirect URIs match with the port ignored ----------------
def _swap_port(uri, port):
    parts = urllib.parse.urlsplit(uri)
    return urllib.parse.urlunsplit(parts._replace(
        netloc=f"{parts.hostname}:{port}"))


def f_loopback(ctx):
    """Authorize with the registered loopback redirect on one port; redeem with a
    DIFFERENT ephemeral port (RFC 8252 §7.3). MUST succeed."""
    ic = _icfg(ctx)
    lb = ic.get("loopback_redirect") or ""
    fl = _to_code(ctx, redirect_uri=_swap_port(lb, 7711))
    if not fl["code"]:
        return fl["resp"]
    return oh.token_request(fl["metadata"], fl["code"], _swap_port(lb, 7929),
                            fl["verifier"], client_id=ic.get("client_id"))


def p_token_issued(r, ctx):
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if isinstance(r.json.get("access_token"), str) \
        and r.json["access_token"] else DEVIATION


# ---- IDL-024 (invalid_client half): failed client authentication ----------------
def f_bad_client_auth(ctx):
    """The confidential client redeems a fresh code with a WRONG client_secret via
    HTTP Basic — MUST be rejected with invalid_client."""
    ic = _icfg(ctx)
    cc = ic.get("confidential") or {}
    fl = _to_code(ctx, client="confidential")
    if not fl["code"]:
        return fl["resp"]
    return oh.token_request(fl["metadata"], fl["code"], ic.get("redirect_uri"),
                            fl["verifier"],
                            basic=(cc.get("client_id"),
                                   (cc.get("client_secret") or "") + "-wrong"))


def p_invalid_client(r, ctx):
    if r.status not in (400, 401) or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("error") == "invalid_client" else DEVIATION


# ---- IDL-026/040/041/052: the identity_required challenge (no token) ------------
def f_gated_no_token(ctx):
    return _gated(ctx)


def _body_code(r, code):
    msgs = (r.json or {}).get("messages") if isinstance(r.json, dict) else None
    return isinstance(msgs, list) and any(
        isinstance(m, dict) and m.get("code") == code for m in msgs)


def p_identity_required(r, ctx):
    """401 + WWW-Authenticate: Bearer with realm == the business issuer URI (which
    IDL-033 pins to the discovery base) + a UCP body message code
    identity_required."""
    if r.status != 401:
        return DEVIATION
    ch = oh.bearer_challenge(r)
    if ch is None or ch.get("realm") != ctx.base:
        return DEVIATION
    return CLEAN if _body_code(r, "identity_required") else DEVIATION


# ---- IDL-025/042: invalid/unverifiable tokens rejected with invalid_token -------
def f_gated_bad_token(ctx):
    return _gated(ctx, token="spck_not_a_real_token_" + oh.state_value())


def p_invalid_token_challenge(r, ctx):
    if r.status != 401:
        return DEVIATION
    ch = oh.bearer_challenge(r)
    if ch is None or ch.get("realm") != ctx.base:
        return DEVIATION
    if ch.get("error") != "invalid_token":
        return DEVIATION
    return CLEAN if _body_code(r, "identity_required") else DEVIATION


# ---- IDL-042 (expired/revoked half): the business rejects an ISSUED-but-invalid
# token on a user-authenticated request. A platform cannot manufacture the
# business's expired/revoked token, so these probes use the config-gated
# /testing/oauth/mint hook (identity.token_mint) to obtain a deterministic
# expired / revoked access token, then present it on the gated op — the business
# MUST answer with the invalid_token challenge (IDL-042 quote names "expired").
# This is the fixture scenario the wave-2 review (W2-F5) flagged as needed; it
# strengthens IDL-042's kill-proof from "syntactic garbage rejected" to "the
# business actually validates exp/revocation on every request". It does NOT close
# IDL-025 (full RFC 9068 iss/aud/azp claim validation is not black-box
# distinguishable for an OPAQUE-token business — see the register note).
def _mint_bad(ctx, kind):
    r = fetch(ctx.base, "/testing/oauth/mint", "POST", {"kind": kind}, _hdr())
    return ((r.json or {}).get("access_token") if r.status == 200 else None), r


def f_expired_token(ctx):
    tok, r = _mint_bad(ctx, "expired")
    return _gated(ctx, token=tok) if tok else r


def f_revoked_token(ctx):
    tok, r = _mint_bad(ctx, "revoked")
    return _gated(ctx, token=tok) if tok else r


# ---- IDL-043 (SHOULD): resource_metadata pointer on the challenge ---------------
def p_resource_metadata(r, ctx):
    if r.status != 401:
        return DEVIATION
    ch = oh.bearer_challenge(r)
    if ch is None:
        return DEVIATION
    rm = ch.get("resource_metadata")
    return CLEAN if isinstance(rm, str) and "://" in rm else DEVIATION


# ---- IDL-044 (MUST NOT): continue_url is never a pre-baked authz request --------
_AUTHZ_PARAMS = {"response_type", "client_id", "code_challenge",
                 "code_challenge_method", "redirect_uri", "scope", "state"}


def p_continue_url_not_prebaked(r, ctx):
    """When the identity_required body offers a continue_url (config promises one),
    it must be an onboarding URL — NOT an OAuth authorization request (the platform
    constructs its own, with PKCE/state/redirect_uri it owns)."""
    if r.status != 401 or not isinstance(r.json, dict):
        return DEVIATION
    cu = r.json.get("continue_url")
    if not isinstance(cu, str) or not cu:
        return DEVIATION
    qs = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(cu).query))
    return DEVIATION if _AUTHZ_PARAMS & set(qs) else CLEAN


# ---- IDL-026/045/046/047: the insufficient_scope challenge ----------------------
def f_underscoped(ctx):
    """Link with only the have_scopes subset, then call the operation that needs
    the full gated_multi.scopes set."""
    g = _icfg(ctx).get("gated_multi") or {}
    fl = _flow(ctx, scopes=g.get("have_scopes") or [])
    if not fl["token"]:
        return _last_resp(fl)
    return _gated(ctx, key="gated_multi", token=fl["token"]["access_token"])


def p_insufficient_scope(r, ctx):
    """403 + realm == issuer + error=insufficient_scope + scope listing the FULL
    required set (IDL-047: not just the missing scopes) + body code
    insufficient_scope."""
    if r.status != 403:
        return DEVIATION
    ch = oh.bearer_challenge(r)
    if ch is None or ch.get("realm") != ctx.base:
        return DEVIATION
    if ch.get("error") != "insufficient_scope":
        return DEVIATION
    want = set((_icfg(ctx).get("gated_multi") or {}).get("scopes") or [])
    if not want or set((ch.get("scope") or "").split()) != want:
        return DEVIATION
    return CLEAN if _body_code(r, "insufficient_scope") else DEVIATION


# ---- IDL-029 companion: a granted standard scope actually unlocks its operation -
def f_scoped_access(ctx):
    g = _icfg(ctx).get("gated") or {}
    fl = _flow(ctx, scopes=g.get("scopes") or [])
    if not fl["token"]:
        return _last_resp(fl)
    return _gated(ctx, token=fl["token"]["access_token"])


def p_200(r, ctx):
    return CLEAN if r.status == 200 else DEVIATION


# ---- IDL-027/028/055: revocation (same client creds; refresh kills access) ------
def f_revocation(ctx):
    """Link, prove the access token unlocks the gated op, revoke the REFRESH token
    (same 'none' client identity as the token endpoint), then present the access
    token again — it MUST now be rejected."""
    ic = _icfg(ctx)
    g = ic.get("gated") or {}
    fl = _flow(ctx, scopes=g.get("scopes") or [])
    if not fl["token"]:
        return _last_resp(fl)
    tok = fl["token"]
    before = _gated(ctx, token=tok["access_token"])
    if before.status != 200:
        return before                      # scenario failed: token never worked
    if not tok.get("refresh_token"):
        # the cited IDL-027 refresh->access cascade cannot be exercised without a
        # refresh token — grade not-tested rather than a partial pass (W2-F9)
        return Resp(0, {}, b'{"probe":"no refresh_token issued; revocation cascade untestable"}')
    rev = oh.revoke(fl["metadata"], tok["refresh_token"],
                    client_id=ic.get("client_id"))
    if rev.status != 200:
        return rev                         # revocation itself failed (IDL-028)
    return _gated(ctx, token=tok["access_token"])


def p_revoked_rejected(r, ctx):
    if r.status != 401:
        return DEVIATION
    ch = oh.bearer_challenge(r)
    if ch is None or ch.get("error") != "invalid_token":
        return DEVIATION
    return CLEAN if _body_code(r, "identity_required") else DEVIATION


# ---- ORD-025 (SHOULD, order-rest.md): order REST responses are signed -----------
def f_order_get(ctx):
    from merchant_checks import order_get_resp
    return order_get_resp(ctx)


def p_order_signed(r, ctx):
    """The order response carries the RFC 9421 signature trio (Signature-Input +
    Signature + Content-Digest); cryptographic validity is the SIGNATURES area's
    check (signature.response_verifies) — this grades the order-rest SHOULD that
    order responses are signed at all."""
    if r.status != 200:
        return DEVIATION
    return CLEAN if all(oh.header(r, h) for h in
                        ("Signature-Input", "Signature", "Content-Digest")) \
        else DEVIATION


_MUT_CB = "https://platform.spck.dev/oauth/callback"

CHECKS_04_08_OAUTH = [
    MCheck("identity.oauth_code_flow", ["IDL-001", "IDL-015", "IDL-029"], "MUST",
           f_code_flow, p_token_ok,
           ["status:400", "drop:access_token", "set:token_type=\"mac\"",
            "empty", "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.client_id", "identity.redirect_uri",
                      "identity.scopes")),
    MCheck("identity.authz_response_iss", ["IDL-018"], "MUST",
           f_authz_response, p_authz_iss,
           ["status:400", "hdrop:Location",
            f"hset:Location={_MUT_CB}?code=ac_mutant&state=s",
            f"hset:Location={_MUT_CB}?code=ac_mutant&state=s&iss=https%3A%2F%2Fevil.example"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.client_id", "identity.redirect_uri")),
    MCheck("identity.metadata_issuer_exact", ["IDL-033"], "MUST",
           f_metadata, p_issuer_exact,
           ["status:404", "drop:issuer", "set:issuer=\"https://evil.example\"",
            "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408),
    # cfg identity.serves_public_clients asserts the conditional clause of the
    # MUST NOT ("when serving native or agent platforms") — a business serving only
    # confidential server-side platforms may legitimately offer client_secret_basic
    # alone (adversarial-review W2-F1)
    MCheck("identity.public_client_method_offered", ["IDL-050"], "MUST",
           f_metadata, p_not_secret_basic_only,
           ["status:404", "drop:token_endpoint_auth_methods_supported",
            "set:token_endpoint_auth_methods_supported=[]",
            "set:token_endpoint_auth_methods_supported=[\"client_secret_basic\"]",
            "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408),
    MCheck("identity.token_pkce_enforced", ["IDL-019", "IDL-024", "IDL-036"], "MUST",
           f_pkce_enforced, p_invalid_grant,
           ["status:200", "set:error=\"invalid_client\"", "drop:error",
            "empty", "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.serves_public_clients",)),
    MCheck("identity.pkce_plain_rejected", ["IDL-049"], "MUST NOT",
           f_pkce_plain, p_authz_rejected,
           ["status:200", f"hset:Location={_MUT_CB}?code=ac_mutant&iss=x",
            "hdrop:Location"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.client_id", "identity.redirect_uri")),
    MCheck("identity.none_requires_pkce", ["IDL-023"], "MUST",
           f_no_challenge, p_authz_rejected,
           ["status:200", f"hset:Location={_MUT_CB}?code=ac_mutant&iss=x",
            "hdrop:Location"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.public_none", "identity.client_id",
                      "identity.redirect_uri")),
    MCheck("identity.redirect_uri_exact", ["IDL-020"], "MUST",
           f_redirect_exact, p_invalid_grant,
           ["status:200", "set:error=\"server_error\"", "drop:error", "empty"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.client_id", "identity.redirect_uri")),
    MCheck("identity.loopback_port_ignored", ["IDL-021"], "MUST",
           f_loopback, p_token_issued,
           ["status:400", "drop:access_token", "empty", "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.client_id", "identity.loopback_redirect")),
    MCheck("identity.client_auth_enforced", ["IDL-024"], "MUST",
           f_bad_client_auth, p_invalid_client,
           ["status:200", "set:error=\"invalid_grant\"", "drop:error", "empty"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.confidential.client_id",
                      "identity.confidential.client_secret",
                      "identity.redirect_uri")),
    MCheck("identity.identity_required_challenge",
           ["IDL-026", "IDL-040", "IDL-041", "IDL-052"], "MUST",
           f_gated_no_token, p_identity_required,
           ["status:200", "status:403", "hdrop:WWW-Authenticate",
            "hset:WWW-Authenticate=Basic realm=\"oauth2\"",
            "hset:WWW-Authenticate=Bearer realm=\"https://wrong.example\"",
            "set:messages=[]", "drop:messages", "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated",)),
    # IDL-025 (full RFC 9068 claims validation) is NOT cited: only the observable
    # garbage-token core is exercised here; counting the row would overstate
    # coverage (W2-F5). It stays GAP pending an expired/wrong-aud token scenario.
    MCheck("identity.invalid_token_challenge", ["IDL-042"], "MUST",
           f_gated_bad_token, p_invalid_token_challenge,
           ["status:200", "hdrop:WWW-Authenticate",
            "hset:WWW-Authenticate=Bearer realm=\"https://wrong.example\", error=\"invalid_token\"",
            "hset:WWW-Authenticate=Bearer error=\"invalid_token\"",
            "set:messages=[]"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated",)),
    # IDL-042 explicitly names an EXPIRED token: mint one via the test hook and
    # confirm the gated op answers with the invalid_token challenge. The
    # --oauth-accept-any-token mutant (validate_oauth_checks) skips exactly the
    # token-validity check, so this DEVIATES there — proving the probe tests real
    # exp enforcement, not just syntactic-garbage rejection.
    MCheck("identity.expired_token_rejected", ["IDL-042"], "MUST",
           f_expired_token, p_invalid_token_challenge,
           ["status:200", "hdrop:WWW-Authenticate",
            "hset:WWW-Authenticate=Bearer realm=\"https://wrong.example\", error=\"invalid_token\"",
            "hset:WWW-Authenticate=Bearer error=\"invalid_token\"",
            "set:messages=[]"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated", "identity.token_mint")),
    MCheck("identity.revoked_token_rejected", ["IDL-042"], "MUST",
           f_revoked_token, p_invalid_token_challenge,
           ["status:200", "hdrop:WWW-Authenticate",
            "hset:WWW-Authenticate=Bearer realm=\"https://wrong.example\", error=\"invalid_token\"",
            "hset:WWW-Authenticate=Bearer error=\"invalid_token\"",
            "set:messages=[]"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated", "identity.token_mint")),
    MCheck("identity.challenge_resource_metadata", ["IDL-043"], "SHOULD",
           f_gated_no_token, p_resource_metadata,
           ["status:200", "hdrop:WWW-Authenticate",
            "hset:WWW-Authenticate=Bearer realm=\"https://wrong.example\""],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated", "identity.resource_metadata")),
    MCheck("identity.continue_url_not_prebaked", ["IDL-044"], "MUST NOT",
           f_gated_no_token, p_continue_url_not_prebaked,
           ["status:200",
            "set:continue_url=\"https://merchant.example/oauth2/authorize?response_type=code&client_id=x&code_challenge=y\"",
            "drop:continue_url"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated", "identity.continue_url")),
    MCheck("identity.insufficient_scope_challenge",
           ["IDL-026", "IDL-045", "IDL-046", "IDL-047"], "MUST",
           f_underscoped, p_insufficient_scope,
           ["status:200", "status:401", "hdrop:WWW-Authenticate",
            "hset:WWW-Authenticate=Bearer realm=\"https://wrong.example\", error=\"insufficient_scope\", scope=\"spck:mutant:not_the_full_set\"",
            "hset:WWW-Authenticate=Bearer error=\"insufficient_scope\", scope=\"\"",
            "set:messages=[]"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated_multi", "identity.client_id")),
    MCheck("identity.scope_grants_access", ["ORD-014"], "MAY",
           f_scoped_access, p_200,
           ["status:401", "status:403", "status:500"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated", "identity.client_id")),
    MCheck("identity.revocation_invalidates", ["IDL-027", "IDL-028", "IDL-055"],
           "MUST", f_revocation, p_revoked_rejected,
           ["status:200", "hdrop:WWW-Authenticate",
            "hset:WWW-Authenticate=Basic realm=\"x\"", "set:messages=[]"],
           capability=IDL_CAP, transport="rest", versions=V0408,
           cfg_needs=("identity.gated", "identity.client_id")),
    MCheck("order.responses_signed", ["ORD-025"], "SHOULD",
           f_order_get, p_order_signed,
           ["hdrop:Signature", "hdrop:Signature-Input", "hdrop:Content-Digest"],
           capability="dev.ucp.shopping.order", needs=("product",),
           transport="rest", versions=V0408,
           cfg_needs=("complete_payment", "signature.responses")),
]
