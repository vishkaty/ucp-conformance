#!/usr/bin/env python3
"""
merchant_checks_04_08_identity.py — 2026-04-08-scoped behavioral checks for the
identity-linking area (identity-linking.md, the 04-08 OAuth 2.0 + RFC 8414 rework).

Version scoping: the IDL-016+ register ids exist ONLY in the 2026-04-08 register
(the 01-11/01-23 registers stop at IDL-013, under discount-consent-identity), so
every check is version-locked (versions=) and the file is named *_04_08_* so
coverage/matrix.py attributes its ids to 2026-04-08 only.

What is (and is not) covered here — the honest split:
  * IDL-016/017/022/058 target the RFC 8414 authorization-server metadata document
    the business MUST publish at /.well-known/oauth-authorization-server on its
    domain. The metadata DOCUMENT is merchant-observable with a plain fetch (the
    register rows note exactly this) — that is what these checks grade. The
    RUNTIME halves (IDL-022's "enforce one of the declared methods at the token
    endpoint") need a real OAuth flow and stay in the needs-oauth tier.
  * IDL-059/060 (profile capability declaration shape) are graded against the
    LIVE profile via the official oracle's nested business_schema def — the
    profile-schema check (DISC-000/validate_profile) can NOT catch these
    (the profile oracle does not recurse into capability config schemas).
  * IDL-058 reading: the spec (identity-linking.md L686-L691) says
    `authorization_response_iss_parameter_supported: true` advertises RFC 9207 and
    `code_challenge_methods_supported: ["S256"]` signals PKCE, and "Both MUST be
    present in UCP-compliant metadata" — the MUST is about those two
    advertisements, so a published `false` (advertising NON-support of the iss
    parameter the business MUST return, IDL-018) is graded as a deviation, not a
    satisfying "presence".

All checks are gated on the business declaring dev.ucp.common.identity_linking
(else not-applicable — never a deviation for merchants without the capability).

NOTE: imported lazily by merchant_checks.all_checks() — do not import this module
before merchant_checks (it pulls MCheck from there).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import fetch, CLEAN, DEVIATION                    # noqa: E402
from verdict_gate import INCONCLUSIVE                         # noqa: E402
from merchant_checks import MCheck                            # noqa: E402

V0408 = ("2026-04-08",)
IDL_CAP = "dev.ucp.common.identity_linking"
WELL_KNOWN = "/.well-known/oauth-authorization-server"


def f_oauth_metadata(ctx):
    """GET the RFC 8414 metadata from the business domain (the same base that
    serves /.well-known/ucp — identity-linking.md: 'OAuth 2.0 with RFC 8414
    discovery on the business domain'). Plain fetch, no UCP headers required."""
    return fetch(ctx.base, WELL_KNOWN, "GET")


def _md(r):
    return r.json if r.status == 200 and isinstance(r.json, dict) else None


def _str_list(v):
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) and x for x in v)


def p_metadata_published(r):
    """IDL-016: metadata is published at the well-known path and is an RFC 8414
    metadata document usable for the authorization-code flow: issuer (REQUIRED by
    RFC 8414 §2), authorization_endpoint + token_endpoint (REQUIRED for the
    authorization-code grant UCP mandates), response_types_supported (REQUIRED)."""
    md = _md(r)
    if md is None:
        return DEVIATION
    for f in ("issuer", "authorization_endpoint", "token_endpoint"):
        if not (isinstance(md.get(f), str) and md[f]):
            return DEVIATION
    return CLEAN if _str_list(md.get("response_types_supported")) else DEVIATION


def p_scopes_supported(r):
    """IDL-017: scopes_supported is POPULATED (non-empty array of scope strings) so
    platforms can detect scope mismatches before initiating an authorization flow."""
    md = _md(r)
    if md is None:
        return DEVIATION
    return CLEAN if _str_list(md.get("scopes_supported")) else DEVIATION


def p_auth_methods_declared(r):
    """IDL-022 (declaration half): token_endpoint_auth_methods_supported declares
    the accepted client authentication methods (non-empty array of method strings).
    The enforcement half ('enforce one of the declared methods at the token
    endpoint') requires a live OAuth flow — needs-oauth, NOT graded here."""
    md = _md(r)
    if md is None:
        return DEVIATION
    return CLEAN if _str_list(md.get("token_endpoint_auth_methods_supported")) else DEVIATION


def p_pkce_iss_advertised(r):
    """IDL-058: authorization_response_iss_parameter_supported: true (RFC 9207) and
    code_challenge_methods_supported including "S256" (PKCE) are both present."""
    md = _md(r)
    if md is None:
        return DEVIATION
    if md.get("authorization_response_iss_parameter_supported") is not True:
        return DEVIATION
    ccm = md.get("code_challenge_methods_supported")
    return CLEAN if isinstance(ccm, list) and "S256" in ccm else DEVIATION


def f_profile(ctx):
    """The discovered /.well-known/ucp profile (same shape as merchant_checks.profile_resp,
    local copy to keep this module import-light)."""
    import json
    from engine import Resp
    return Resp(200, {"Content-Type": "application/json"},
                json.dumps(ctx.profile.get("ucp", ctx.profile)).encode())


def p_identity_config_schema(r, ctx):
    """IDL-059/060: every declared identity_linking capability entry validates
    against the OFFICIAL nested business_schema def (config required, config.scopes
    required, scope keys match the scope_token pattern '{capability}:{scope}').
    Oracle-arbitrated; INCONCLUSIVE (-> not-tested) if the oracle isn't built."""
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / "selfcheck"))
    try:
        from schema_oracle import validate_nested_def
    except Exception:
        return INCONCLUSIVE
    caps = (r.json or {}).get("capabilities") if isinstance(r.json, dict) else None
    entries = caps.get(IDL_CAP) if isinstance(caps, dict) else None
    if not isinstance(entries, list) or not entries:
        return DEVIATION
    for e in entries:
        if not isinstance(e, dict):
            return DEVIATION
        try:
            ok, _ = validate_nested_def(e, "schemas/common/identity_linking.json",
                                        f"{IDL_CAP}/business_schema",
                                        op="read", version=ctx.version or "2026-04-08")
        except Exception:
            # OracleUnavailable or wiring glitch -> inconclusive, never a false
            # deviation; only a clean "oracle ran and rejected" deviates.
            return INCONCLUSIVE
        if not ok:
            return DEVIATION
    return CLEAN


CHECKS_04_08_IDENTITY = [
    MCheck("identity.oauth_metadata_published", ["IDL-016"], "MUST",
           f_oauth_metadata, p_metadata_published,
           ["status:404", "drop:issuer", "drop:authorization_endpoint",
            "drop:token_endpoint", "set:response_types_supported=[]",
            "corrupt-json", "empty"],
           capability=IDL_CAP, transport="rest", versions=V0408),
    MCheck("identity.oauth_metadata_scopes_supported", ["IDL-017"], "MUST",
           f_oauth_metadata, p_scopes_supported,
           ["status:404", "drop:scopes_supported", "set:scopes_supported=[]",
            "set:scopes_supported=\"dev.ucp.shopping.order:read\"",
            "set:scopes_supported=[123]", "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408),
    MCheck("identity.oauth_metadata_auth_methods", ["IDL-022"], "MUST",
           f_oauth_metadata, p_auth_methods_declared,
           ["status:404", "drop:token_endpoint_auth_methods_supported",
            "set:token_endpoint_auth_methods_supported=[]",
            "set:token_endpoint_auth_methods_supported=\"none\"",
            "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408),
    MCheck("identity.oauth_metadata_pkce_iss", ["IDL-058"], "MUST",
           f_oauth_metadata, p_pkce_iss_advertised,
           ["status:404", "drop:authorization_response_iss_parameter_supported",
            "set:authorization_response_iss_parameter_supported=false",
            "drop:code_challenge_methods_supported",
            "set:code_challenge_methods_supported=[\"plain\"]",
            "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408),
    # Profile capability declaration shape, oracle-arbitrated. Mutations replace the
    # whole capabilities object (reverse-domain keys contain dots, so dotted set:/
    # drop: paths cannot address the entry itself) — each injects one schema defect.
    MCheck("identity.capability_config_schema", ["IDL-059", "IDL-060"], "MUST",
           f_profile, p_identity_config_schema,
           ["drop:capabilities",
            "set:capabilities={\"dev.ucp.common.identity_linking\":[{\"version\":\"2026-04-08\"}]}",
            "set:capabilities={\"dev.ucp.common.identity_linking\":[{\"version\":\"2026-04-08\",\"config\":{}}]}",
            "set:capabilities={\"dev.ucp.common.identity_linking\":[{\"version\":\"2026-04-08\",\"config\":{\"scopes\":{\"order-read\":{}}}}]}",
            "set:capabilities={\"dev.ucp.common.identity_linking\":[{\"version\":\"2026-04-08\",\"config\":{\"scopes\":{\"dev.ucp.shopping.order:Read\":{}}}}]}",
            "corrupt-json"],
           capability=IDL_CAP, transport="rest", versions=V0408),
]
