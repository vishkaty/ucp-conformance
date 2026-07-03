#!/usr/bin/env python3
"""
oauth_harness.py — the PLATFORM side of the identity-linking OAuth 2.0 flow.

Identity-linking requirements are `needs-oauth`: the business hosts an OAuth 2.0
authorization server (RFC 8414 discovery on the business domain, authorization-code
grant with PKCE S256, RFC 7009 revocation) and gates user-authenticated operations
behind Bearer tokens. To grade those business-side MUSTs the suite must ACT as the
platform: discover metadata, send authorization requests, capture the redirect
(without following it — the redirect target is the platform's own callback), and
exchange/revoke codes and tokens with form-encoded requests.

This module is that driver (shape precedent: webhook_harness.py — a reusable helper
the checks modules import; engine.fetch stays JSON-only, so the form-encoded and
redirect-capturing requests live here). Stdlib only.
"""
import base64, hashlib, os, urllib.parse, urllib.request, urllib.error, sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp                                     # noqa: E402

WELL_KNOWN = "/.well-known/oauth-authorization-server"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the authorization response IS the 3xx we must grade
    (its Location carries code/state/iss), and its target is not resolvable here."""
    def redirect_request(self, *a, **kw):
        return None


def request(url, method="GET", data=None, headers=None, timeout=10):
    """One HTTP exchange -> engine.Resp; 3xx and 4xx/5xx are captured, not raised."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return Resp(r.status, r.getheaders(), r.read())
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.headers.items(), e.read())
    except Exception as e:
        return Resp(0, {}, str(e).encode())


def form_post(url, fields, basic=None, headers=None):
    """POST application/x-www-form-urlencoded (RFC 6749 §4.1.3 token requests /
    RFC 7009 §2.1 revocation requests), optionally with HTTP Basic client auth."""
    h = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    if basic:
        h["Authorization"] = "Basic " + base64.b64encode(
            f"{basic[0]}:{basic[1]}".encode()).decode()
    return request(url, "POST", urllib.parse.urlencode(fields).encode(), h)


def discover(base):
    """RFC 8414 discovery on the business domain (identity-linking.md Discovery)."""
    return request(base.rstrip("/") + WELL_KNOWN)


def pkce_pair():
    """A fresh RFC 7636 verifier/S256-challenge pair."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def state_value():
    return base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()


def authorize(md, client_id, redirect_uri, scopes, code_challenge=None,
              code_challenge_method=None, state=None, response_type="code"):
    """Send the authorization request (spec Account Linking Flow step 2); returns
    the raw (unfollowed) response — normally a 302 whose Location carries the
    authorization response parameters (step 3)."""
    q = {"response_type": response_type, "client_id": client_id,
         "redirect_uri": redirect_uri, "scope": " ".join(scopes)}
    if code_challenge:
        q["code_challenge"] = code_challenge
    if code_challenge_method:
        q["code_challenge_method"] = code_challenge_method
    if state is not None:
        q["state"] = state
    ep = (md or {}).get("authorization_endpoint") or ""
    sep = "&" if "?" in ep else "?"
    return request(ep + sep + urllib.parse.urlencode(q))


def location_params(resp):
    """The authorization-response parameters from the redirect's Location query."""
    loc = header(resp, "location")
    if not loc:
        return {}
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(loc).query))


def header(resp, name):
    """Case-insensitive response-header lookup on an engine.Resp."""
    for k, v in (resp.headers or {}).items():
        if k.lower() == name.lower():
            return v
    return None


def bearer_challenge(resp):
    """Parse a WWW-Authenticate Bearer challenge -> params dict (RFC 6750 §3),
    or None when the header is absent or not the Bearer scheme."""
    v = header(resp, "www-authenticate")
    if not isinstance(v, str) or not v.strip().startswith("Bearer"):
        return None
    out = {}
    for part in v.strip()[len("Bearer"):].split(","):
        k, eq, val = part.strip().partition("=")
        if eq:
            out[k.strip().lower()] = val.strip().strip('"')
    return out


def token_request(md, code, redirect_uri, verifier, client_id=None, basic=None):
    """Exchange an authorization code (flow step 4). Public clients authenticate as
    'none' (client_id in the body); confidential ones via HTTP Basic. Passing
    verifier/redirect_uri as None OMITS them (negative probes)."""
    fields = {"grant_type": "authorization_code", "code": code}
    if redirect_uri is not None:
        fields["redirect_uri"] = redirect_uri
    if verifier is not None:
        fields["code_verifier"] = verifier
    if client_id and not basic:
        fields["client_id"] = client_id
    return form_post((md or {}).get("token_endpoint") or "", fields, basic=basic)


def revoke(md, token, client_id=None, basic=None):
    """RFC 7009 revocation with the same client credentials as the token endpoint."""
    fields = {"token": token}
    if client_id and not basic:
        fields["client_id"] = client_id
    return form_post((md or {}).get("revocation_endpoint") or "", fields, basic=basic)


def code_flow(base, client_id, redirect_uri, scopes, basic=None):
    """The whole platform-side account-linking flow (spec steps 1-5). Returns a dict:
      metadata_resp/metadata, authorize_resp, params (authorization response),
      code, verifier, state, token_resp, token (parsed JSON on success).
    Stops populating at the first failing step — callers return the last Resp they
    care about, so a broken step surfaces as that check's deviation."""
    out = {"metadata": None, "code": None, "token": None,
           "verifier": None, "state": None,
           "metadata_resp": None, "authorize_resp": None, "token_resp": None,
           "params": {}}
    md_r = discover(base)
    out["metadata_resp"] = md_r
    if md_r.status != 200 or not isinstance(md_r.json, dict):
        return out
    md = md_r.json
    out["metadata"] = md
    verifier, challenge = pkce_pair()
    st = state_value()
    out["verifier"], out["state"] = verifier, st
    a = authorize(md, client_id, redirect_uri, scopes,
                  code_challenge=challenge, code_challenge_method="S256", state=st)
    out["authorize_resp"] = a
    params = location_params(a)
    out["params"] = params
    if a.status not in (302, 303) or not params.get("code"):
        return out
    out["code"] = params["code"]
    t = token_request(md, params["code"], redirect_uri, verifier,
                      client_id=client_id, basic=basic)
    out["token_resp"] = t
    if t.status == 200 and isinstance(t.json, dict):
        out["token"] = t.json
    return out
