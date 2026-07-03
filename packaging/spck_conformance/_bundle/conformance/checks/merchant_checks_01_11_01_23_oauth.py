#!/usr/bin/env python3
"""
merchant_checks_01_11_01_23_oauth.py — 01-era identity-linking OAuth checks
(discount-consent-identity.json IDL family at 2026-01-11/2026-01-23).

The 01-era identity-linking spec (identity-linking.md, TEXTUALLY IDENTICAL at
2026-01-11 and 2026-01-23 — verified by diff, only markdown list formatting
differs) predates the 04-08 capability-scopes rework: businesses adhere to
RFC 8414 on the business domain, authenticate platforms with client_id/
client_secret over HTTP Basic (client_secret_basic), grant the standard scope
vocabulary (ucp:scopes:checkout_session), and implement RFC 7009 revocation
authenticated with the same client credentials. Because both registers carry the
SAME rows, each check cites its id for BOTH versions (versions=; precedent:
FUL-026 / tls_check_01_11_01_23.py) and is reference-gated on the controlled
fixture in 2026-01-23 mode.

ID-DRIFT NOTE: the 04-08 register REUSES IDL-005..013 for different requirements
(e.g. IDL-005@04-08 = public clients MUST NOT embed a client_secret). Nothing
here may ever be graded at 2026-04-08 — hence the hard versions= lock.

Covered (business-bound): IDL-005 (implement OAuth 2.0) + IDL-009 (standard UCP
scopes granted) via the code flow; IDL-006 (RFC 8414 metadata); IDL-007 (client
authentication enforced at the token endpoint); IDL-010/011/012 (RFC 7009
revocation with the same client credentials; the revoked token is dead).
Also covered: IDL-013 (a scope must grant ALL of its capability's operations) —
wave-3 added the fixture's --require-checkout-scope mode (config-gated on
identity.checkout_scope_gated, default OFF so the golden's unauthenticated checks
stay sound) so one checkout_session token can be observed granting Create+Get+Cancel;
the --checkout-scope-partial mutant (a per-operation scope) is the IDL-013 violation
the check catches. IDL-001..004 bind the PLATFORM — reclassified manual in both
registers.

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck from there).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, Resp, CLEAN, DEVIATION              # noqa: E402
from merchant_checks import MCheck, _hdr, _create_payload     # noqa: E402
import oauth_harness as oh                                    # noqa: E402

V01ERA = ("2026-01-11", "2026-01-23")
IDL_CAP = "dev.ucp.common.identity_linking"


def _icfg(ctx):
    return ctx.config.get("identity") or {}


def _scope01(ctx):
    return _icfg(ctx).get("scope_01era")


def _basic_creds(ctx):
    cc = _icfg(ctx).get("confidential") or {}
    return cc.get("client_id"), cc.get("client_secret")


def _to_code01(ctx):
    """discover + authorize with the confidential client (01-era flows carry no
    PKCE requirement; none is sent). Returns {metadata, code, resp}."""
    cid, _ = _basic_creds(ctx)
    ic = _icfg(ctx)
    out = {"metadata": None, "code": None, "resp": None}
    md_r = oh.discover(ctx.base)
    out["resp"] = md_r
    if md_r.status != 200 or not isinstance(md_r.json, dict):
        return out
    out["metadata"] = md_r.json
    a = oh.authorize(md_r.json, cid, ic.get("redirect_uri"),
                     [_scope01(ctx)], state=oh.state_value())
    out["resp"] = a
    out["code"] = oh.location_params(a).get("code")
    return out


# ---- IDL-005/009: the business implements OAuth 2.0; the standard scope grants --
def f_flow01(ctx):
    ic = _icfg(ctx)
    creds = _basic_creds(ctx)
    fl = _to_code01(ctx)
    if not fl["code"]:
        return fl["resp"]
    return oh.token_request(fl["metadata"], fl["code"], ic.get("redirect_uri"),
                            None, basic=creds)


def p_token_ok01(r, ctx):
    """RFC 6749 §5.1 token response; when scope is echoed it retains the standard
    UCP scope (IDL-009 — scope echo is optional per §5.1 when identical)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    t = r.json
    if not (isinstance(t.get("access_token"), str) and t["access_token"]):
        return DEVIATION
    if not (isinstance(t.get("token_type"), str)
            and t["token_type"].lower() == "bearer"):
        return DEVIATION
    if t.get("scope") is not None and _scope01(ctx) not in str(t["scope"]).split():
        return DEVIATION
    return CLEAN


# ---- IDL-006: RFC 8414 metadata on the business domain --------------------------
def f_metadata01(ctx):
    return fetch(ctx.base, oh.WELL_KNOWN, "GET")


def p_metadata01(r, ctx):
    """The metadata document declares the OAuth endpoint locations (issuer is
    REQUIRED by RFC 8414 §2; authorization_endpoint + token_endpoint are what the
    authorization-code flow the spec mandates needs)."""
    if r.status != 200 or not isinstance(r.json, dict):
        return DEVIATION
    for f in ("issuer", "authorization_endpoint", "token_endpoint"):
        if not (isinstance(r.json.get(f), str) and r.json[f]):
            return DEVIATION
    return CLEAN


# ---- IDL-007: client authentication enforced at the token endpoint --------------
def f_bad_secret01(ctx):
    """Redeem a fresh code with a WRONG client_secret over HTTP Basic. RFC 6749
    §5.2 pins the rejection code: invalid_client."""
    ic = _icfg(ctx)
    cid, secret = _basic_creds(ctx)
    fl = _to_code01(ctx)
    if not fl["code"]:
        return fl["resp"]
    return oh.token_request(fl["metadata"], fl["code"], ic.get("redirect_uri"),
                            None, basic=(cid, (secret or "") + "-wrong"))


def p_invalid_client01(r, ctx):
    if r.status not in (400, 401) or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("error") == "invalid_client" else DEVIATION


# ---- IDL-010/011/012: RFC 7009 revocation, same client credentials --------------
def f_revocation01(ctx):
    """Link, revoke the refresh_token with the SAME Basic credentials used at the
    token endpoint (IDL-012), then try to USE the revoked refresh_token — the
    business must treat it as dead (IDL-010/011): invalid_grant."""
    ic = _icfg(ctx)
    creds = _basic_creds(ctx)
    fl = _to_code01(ctx)
    if not fl["code"]:
        return fl["resp"]
    t = oh.token_request(fl["metadata"], fl["code"], ic.get("redirect_uri"),
                         None, basic=creds)
    if t.status != 200 or not isinstance(t.json, dict) \
       or not t.json.get("refresh_token"):
        return t
    rt = t.json["refresh_token"]
    rev = oh.revoke(fl["metadata"], rt, basic=creds)
    if rev.status != 200:
        return rev                          # same-creds revocation failed (IDL-012)
    return oh.form_post(fl["metadata"].get("token_endpoint") or "",
                        {"grant_type": "refresh_token", "refresh_token": rt},
                        basic=creds)


def p_revoked_grant01(r, ctx):
    if r.status != 400 or not isinstance(r.json, dict):
        return DEVIATION
    return CLEAN if r.json.get("error") == "invalid_grant" else DEVIATION


# ---- IDL-013: a scope covering a capability MUST grant access to ALL operations
# associated to it (checkout_session: Get/Create/Update/Cancel/Complete). Observing
# this needs a scope-GATED checkout lifecycle: gating the DEFAULT golden would
# contradict every existing unauthenticated 01-era check, so it lives behind the
# fixture's --require-checkout-scope mode (config.identity.checkout_scope_gated). The
# check drives THREE distinct operations (Create, Get, Cancel) with ONE
# checkout_session token and shows they are all granted; the --checkout-scope-partial
# MUTANT makes one operation demand an extra per-operation scope (the IDL-013
# violation), which this check catches. Kill-proof: selfcheck/validate_checkout_scope_check.py.
def _mint_scoped_01(ctx):
    ic = _icfg(ctx)
    fl = _to_code01(ctx)
    if not fl["code"]:
        return None
    t = oh.token_request(fl["metadata"], fl["code"], ic.get("redirect_uri"),
                         None, basic=_basic_creds(ctx))
    return (t.json or {}).get("access_token") if t.status == 200 else None


def _bearer(tok):
    h = _hdr()
    h["Authorization"] = "Bearer " + tok
    return h


def f_capability_scope_grants_ops(ctx):
    tok = _mint_scoped_01(ctx)
    if not tok:
        return Resp(596, {}, b'{"probe":"could not mint a checkout_session-scoped token"}')
    base = ctx.shopping_endpoint
    # negative: an UNauthenticated checkout op MUST be refused (the gate is real)
    noauth = fetch(base, "/checkout-sessions", "POST", _create_payload(ctx), _hdr())
    if noauth.status not in (401, 403):
        return Resp(597, {}, b'{"probe":"checkout op not scope-gated (unauth accepted)"}')
    bh = _bearer(tok)
    # positive: the SINGLE capability scope grants Create, then Get, then Cancel
    cr = fetch(base, "/checkout-sessions", "POST", _create_payload(ctx), bh)
    if cr.status not in (200, 201):
        return cr
    sid = (cr.json or {}).get("id")
    gt = fetch(base, f"/checkout-sessions/{sid}", "GET", None, bh)
    if gt.status != 200:
        return gt
    return fetch(base, f"/checkout-sessions/{sid}/cancel", "POST", None, bh)


def p_scope_grants_all_ops(r, ctx):
    """One ucp:scopes:checkout_session token unlocked Create+Get+Cancel; the returned
    response is the terminal Cancel (200). A merchant that scopes operations
    individually (the IDL-013 violation) refuses one op with 401/403 — that response
    is what gets returned, and it fails here."""
    return CLEAN if r.status == 200 and isinstance(r.json, dict) else DEVIATION


CHECKS_01_11_01_23_OAUTH = [
    MCheck("identity01.capability_scope_grants_ops", ["IDL-013"], "MUST",
           f_capability_scope_grants_ops, p_scope_grants_all_ops,
           ["status:401", "status:403", "status:500"],
           capability=IDL_CAP, transport="rest", versions=V01ERA,
           cfg_needs=("identity.confidential.client_id",
                      "identity.confidential.client_secret",
                      "identity.scope_01era", "identity.redirect_uri",
                      "identity.checkout_scope_gated")),
    MCheck("identity01.oauth_code_flow", ["IDL-005", "IDL-009"], "MUST",
           f_flow01, p_token_ok01,
           ["status:400", "drop:access_token", "set:token_type=\"mac\"",
            "set:scope=\"spck:mutant:scope\"", "empty", "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V01ERA,
           cfg_needs=("identity.confidential.client_id",
                      "identity.confidential.client_secret",
                      "identity.scope_01era", "identity.redirect_uri")),
    MCheck("identity01.metadata_published", ["IDL-006"], "MUST",
           f_metadata01, p_metadata01,
           ["status:404", "drop:issuer", "drop:authorization_endpoint",
            "drop:token_endpoint", "corrupt-json", "empty"],
           capability=IDL_CAP, transport="rest", versions=V01ERA),
    MCheck("identity01.token_client_auth", ["IDL-007"], "MUST",
           f_bad_secret01, p_invalid_client01,
           ["status:200", "set:error=\"invalid_grant\"", "drop:error", "empty"],
           capability=IDL_CAP, transport="rest", versions=V01ERA,
           cfg_needs=("identity.confidential.client_id",
                      "identity.confidential.client_secret",
                      "identity.scope_01era", "identity.redirect_uri")),
    MCheck("identity01.revocation", ["IDL-010", "IDL-011", "IDL-012"], "MUST",
           f_revocation01, p_revoked_grant01,
           ["status:200", "set:error=\"invalid_client\"", "drop:error", "empty"],
           capability=IDL_CAP, transport="rest", versions=V01ERA,
           cfg_needs=("identity.confidential.client_id",
                      "identity.confidential.client_secret",
                      "identity.scope_01era", "identity.redirect_uri")),
]
