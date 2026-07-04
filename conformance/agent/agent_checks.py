#!/usr/bin/env python3
"""
agent_checks.py — the agent-side conformance check registry.

An agent check asserts on a platform/agent's OBSERVED behavior — the requests it sent and
how it handled the sandbox's stimuli — captured in a ReferenceAgent-style session log.
Each check names the single `kill_mutation` (a defect id from reference_agent.DEFECTS)
that MUST make it fail: the kill-rate gate requires predicate(reference_log)==CLEAN AND
predicate(mutant_log[kill_mutation])==DEVIATION, exactly mirroring the merchant kill-rate
discipline.

Phase A: the model + an EMPTY registry (zero coverage — the foundation runs green before
any check exists). Phase B adds the P0 rounded slice (UCP-Agent, signature verification,
identity/OAuth/iss, escalation-follow).

Isolation: this tree (conformance/agent/) is NOT globbed by the merchant coverage_map
(which scans conformance/checks/*.py, non-recursive) nor the merchant collectors — so
adding agent checks here cannot move the merchant coverage numbers.
"""

import re
import urllib.parse

CLEAN = "CLEAN"
DEVIATION = "DEVIATION"


class ACheck:
    def __init__(self, cid, req_ids, keyword, predicate, kill_mutation,
                 versions=None, capability=None, needs=(), scenario="conformant"):
        self.id = cid
        self.req_ids = list(req_ids)
        self.keyword = keyword
        self.predicate = predicate          # predicate(session_log) -> CLEAN | DEVIATION
        self.kill_mutation = kill_mutation  # the defect id that MUST make this check fail
        self.versions = tuple(versions) if versions else None
        self.capability = capability
        self.needs = tuple(needs)
        # which sandbox stimulus the reference agent runs against ("conformant" |
        # "bad_signature" | ...). The reference-gate boots one sandbox per scenario.
        self.scenario = scenario


# ---- P0 predicates (each asserts on the agent's session log) ----------------
def p_sends_ucp_agent(log):
    """DISC-006: platforms MUST include their profile URI in every request. We assert it
    on the agent's API requests (the create_checkout POST), which MUST carry a UCP-Agent
    header whose value declares a profile. (The unauthenticated discovery GET is where
    the profile is first learned, so we grade the subsequent API calls.)"""
    api = [e for e in log if e["op"] != "discover"]
    if not api:
        return DEVIATION
    for e in api:
        ua = (e["request"]["headers"] or {}).get("UCP-Agent")
        if not (isinstance(ua, str) and "profile" in ua):
            return DEVIATION
    return CLEAN


def p_follows_escalation(log):
    """CHK-008: on `requires_escalation`, the platform MUST use (follow) the
    business-provided continue_url. We require the agent to have (a) received a
    requires_escalation status carrying a continue_url and (b) made a request to exactly
    that continue_url."""
    esc_url = None
    for e in log:
        b = (e.get("response") or {}).get("body") or {}
        if b.get("status") == "requires_escalation":
            esc_url = b.get("continue_url")
    if not esc_url:
        return DEVIATION      # the sandbox always escalates; not seeing it = broken flow
    followed = any(e["op"] == "follow_escalation" and e["request"]["path"] == esc_url
                   for e in log)
    return CLEAN if followed else DEVIATION


def p_verifies_business_signature(log):
    """SIG-036: a verifier MUST reconstruct the signature base and reject with
    signature_invalid if ECDSA verification fails. The platform/agent is the verifier of
    the business's RFC 9421 response signatures (SIG-001/002). Run against the
    bad_signature sandbox, a conformant agent detects the invalid signature and rejects
    (does not proceed) — we require an API response flagged sig_verified=False AND
    rejected."""
    for e in log:
        if e["op"] != "discover" and e.get("sig_verified") is False and e.get("rejected"):
            return CLEAN
    return DEVIATION


def p_uses_pkce(log):
    """IDL-011: platforms MUST use PKCE with code_challenge_method=S256 for all
    authorization-code exchanges. Assert the authorization request carries code_challenge
    and code_challenge_method=S256."""
    for e in log:
        if e["op"] == "authorize":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(e["request"]["path"]).query)
            ok = bool(q.get("code_challenge")) and q.get("code_challenge_method") == ["S256"]
            return CLEAN if ok else DEVIATION
    return DEVIATION


def p_validates_iss(log):
    """IDL-012: platforms MUST validate the iss parameter in the authorization response to
    prevent Mix-Up Attacks (RFC 9207). Run against the bad_iss sandbox, a conformant agent
    detects the mismatched issuer and rejects."""
    for e in log:
        if e["op"] == "authorize" and e.get("iss_validated") is False and e.get("rejected"):
            return CLEAN
    return DEVIATION


def _req_sig_components(entry):
    """Parse the covered components out of a request's RFC 9421 Signature-Input, or None
    when the request is unsigned."""
    si = (entry["request"]["headers"] or {}).get("Signature-Input")
    if not si:
        return None
    try:
        inner = si[si.index("(") + 1:si.index(")")]
        return [c.strip().strip('"') for c in inner.split()]
    except Exception:
        return None


def _signed_api(log):
    return [e for e in log if e["op"] in ("create_checkout", "complete")]


# NOTE ON SUBJECT: request signing itself is SHOULD (SIG-026: "Platforms SHOULD sign all
# requests ... Alternative authentication mechanisms may be used instead"), so an UNSIGNED
# request is conformant and is skipped, never failed. What binds unconditionally is: WHEN an
# agent signs, the signature MUST verify (SIG-001) and MUST cover the required components
# (SIG-014/015/016/018). The kill mutations are therefore a signing agent that corrupts or
# drops a component — never one that declines to sign.
def p_request_signature_verifies(log):
    """SIG-001: a signed request is a REAL RFC 9421 signature the receiver accepts (the
    sandbox verifies it and 401s an invalid one). Require >=1 signed request that verified
    (non-401). Mirrors the merchant SIG-001 'the signature really verifies' check."""
    accepted = False
    for e in _signed_api(log):
        if _req_sig_components(e) is None:
            continue                                   # unsigned: allowed (SIG-026), skip
        if (e.get("response") or {}).get("status") == 401:
            return DEVIATION                           # signed but did not verify
        accepted = True
    return CLEAN if accepted else DEVIATION


def _covers(log, required, body_only=False, post_only=False):
    """A signed request MUST cover `required`. Unsigned requests are skipped (SIG-026)."""
    for e in _signed_api(log):
        comps = _req_sig_components(e)
        if comps is None:
            continue
        if body_only and e["request"].get("body") is None:
            continue
        if post_only and e["request"]["method"] != "POST":
            continue
        if not required <= set(comps):
            return DEVIATION
    return CLEAN


def p_signs_core_components(log):
    """SIG-014: REST request signed components MUST include @method, @authority, @path."""
    return _covers(log, {"@method", "@authority", "@path"})


def p_signs_body_components(log):
    """SIG-015: signed components MUST include content-digest and content-type when the
    request has a body."""
    return _covers(log, {"content-digest", "content-type"}, body_only=True)


def p_signs_idempotency_key(log):
    """SIG-016: idempotency-key MUST be a signed component for POST/PUT/DELETE/PATCH."""
    return _covers(log, {"idempotency-key"}, post_only=True)


def p_signs_ucp_agent_component(log):
    """SIG-018: ucp-agent MUST be a signed component if the UCP-Agent header is present."""
    return _covers(log, {"ucp-agent"})


# ---- WWW-Authenticate: Bearer challenge handling (RFC 6750) -------------------
# The gated op 401s (identity_required) then 403s (insufficient_scope, carrying the required
# scope); a conformant agent processes each challenge, obtains/upgrades a token, and retries.
# Predicates use "vacuous CLEAN when the prerequisite step is absent" so each defect trips
# exactly one check (the missing-retry case is IDL-008's).
def _challenge_scope(log):
    """The scope advertised in any WWW-Authenticate challenge (the 403 insufficient_scope),
    or None when no challenge carried a scope ('when present' in IDL-009)."""
    scope = None
    for e in log:
        wa = ((e.get("response") or {}).get("headers") or {}).get("www-authenticate", "")
        m = re.search(r'scope="([^"]*)"', wa)
        if m:
            scope = m.group(1)
    return scope


def _gated_challenged(log):
    for e in log:
        r = e.get("response") or {}
        if e["op"] in ("fetch_gated", "fetch_gated_retry") and r.get("status") in (401, 403) \
                and "www-authenticate" in (r.get("headers") or {}):
            return True
    return False


def p_processes_auth_challenge(log):
    """IDL-008: platforms MUST process WWW-Authenticate: Bearer challenges on 401/403 to
    user-authenticated operations. Require the agent to have seen a challenge AND made a
    follow-up authenticated retry (rather than giving up)."""
    if not _gated_challenged(log):
        return DEVIATION                     # sandbox always challenges here; not seeing it = broken
    retried = any(e["op"] == "fetch_gated_retry" for e in log)
    return CLEAN if retried else DEVIATION


def p_sends_bearer_token(log):
    """IDL-007: platforms MUST include user identity tokens in the Authorization header using
    the Bearer scheme. Every gated retry MUST carry `Authorization: Bearer <token>`. (No
    retry at all is IDL-008's failure, not this one — vacuously CLEAN here.)"""
    retries = [e for e in log if e["op"] == "fetch_gated_retry"]
    for e in retries:
        auth = (e["request"]["headers"] or {}).get("Authorization", "")
        if not (auth.startswith("Bearer ") and len(auth) > len("Bearer ")):
            return DEVIATION
    return CLEAN


def p_extracts_challenge_scope(log):
    """IDL-009: platforms MUST extract the scope from the WWW-Authenticate challenge to
    construct the subsequent authorization request. Some gated re-auth MUST carry exactly the
    scope the challenge advertised (derived from the log, not hard-coded). No scoped challenge
    -> nothing to extract ('when present'); no re-auth -> IDL-008's failure — both CLEAN."""
    want = _challenge_scope(log)
    if want is None:
        return CLEAN
    for e in log:
        if e["op"] == "authorize_gated":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(e["request"]["path"]).query)
            if q.get("scope") == [want]:
                return CLEAN
    return DEVIATION


def p_validates_issuer_match(log):
    """IDL-033: the issuer in RFC 8414 metadata MUST match the discovery base URI byte-for-byte
    (no normalization). Against the bad_issuer sandbox (issuer has a trailing slash) a
    conformant agent detects the mismatch and rejects."""
    for e in log:
        if e["op"] == "as_discovery" and e.get("issuer_matched") is False and e.get("rejected"):
            return CLEAN
    return DEVIATION


def p_aborts_on_discovery_error(log):
    """IDL-031/032: on a non-404 RFC 8414 discovery response (error/timeout) the platform MUST
    abort and MUST NOT silently fall through to the OIDC fallback. Against the discovery_error
    sandbox (a 500) a conformant agent aborts and does NOT fetch the OIDC config."""
    got_error = any(e["op"] == "as_discovery"
                    and (st := (e.get("response") or {}).get("status")) is not None
                    and not (200 <= st < 300) and st != 404
                    for e in log)
    fell_through = any(e["op"] == "oidc_discovery" for e in log)
    return CLEAN if (got_error and not fell_through) else DEVIATION


def _token_bodies(log):
    return [e["request"].get("body") or {} for e in log if e["op"] == "token"]


def p_public_client_pkce_proof(log):
    """IDL-004: public clients MUST use token-endpoint auth method 'none' and rely on PKCE
    (S256) as proof-of-possession of the authorization code. Every token exchange MUST
    present a code_verifier."""
    bodies = _token_bodies(log)
    if not bodies:
        return DEVIATION                     # the flow must reach a token exchange here
    for b in bodies:
        if not b.get("code_verifier"):
            return DEVIATION
    return CLEAN


def p_no_client_secret(log):
    """IDL-005: public clients MUST NOT embed a client_secret. Reject a token request carrying
    one anywhere a client_secret can hide: the request body, an `Authorization: Basic` header
    (client_secret_basic — the spec's own token example uses this form), or the query string."""
    for e in log:
        if e["op"] != "token":
            continue
        if (e["request"].get("body") or {}).get("client_secret"):
            return DEVIATION
        if (e["request"]["headers"] or {}).get("Authorization", "").startswith("Basic "):
            return DEVIATION
        if "client_secret=" in (e["request"].get("path") or ""):
            return DEVIATION
    return CLEAN


def p_validates_oauth_state(log):
    """IDL-035: on the authorization response the platform MUST verify state matches the sent
    value and discard on mismatch. Against the bad_state sandbox a conformant agent detects
    the mismatched state and rejects."""
    for e in log:
        if e["op"] in ("authorize", "authorize_gated") \
                and e.get("state_validated") is False and e.get("rejected"):
            return CLEAN
    return DEVIATION


CHECKS = [
    ACheck("agent.sends_ucp_agent", ["DISC-006"], "MUST",
           p_sends_ucp_agent, kill_mutation="no_ucp_agent", versions=["2026-04-08"]),
    ACheck("agent.follows_escalation", ["CHK-008"], "MUST",
           p_follows_escalation, kill_mutation="ignore_escalation", versions=["2026-04-08"]),
    ACheck("agent.verifies_business_signature", ["SIG-036", "SIG-002"], "MUST",
           p_verifies_business_signature, kill_mutation="skip_sig_verify",
           versions=["2026-04-08"], scenario="bad_signature"),
    ACheck("agent.uses_pkce", ["IDL-011"], "MUST",
           p_uses_pkce, kill_mutation="no_pkce", versions=["2026-04-08"]),
    ACheck("agent.validates_iss", ["IDL-012"], "MUST",
           p_validates_iss, kill_mutation="skip_iss_validation",
           versions=["2026-04-08"], scenario="bad_iss"),
    ACheck("agent.request_signature_verifies", ["SIG-001"], "MUST",
           p_request_signature_verifies, kill_mutation="sign_corrupt", versions=["2026-04-08"]),
    ACheck("agent.signs_core_components", ["SIG-014"], "MUST",
           p_signs_core_components, kill_mutation="sign_omit_authority", versions=["2026-04-08"]),
    ACheck("agent.signs_body_components", ["SIG-015"], "MUST",
           p_signs_body_components, kill_mutation="sign_omit_digest", versions=["2026-04-08"]),
    ACheck("agent.signs_idempotency_key", ["SIG-016"], "MUST",
           p_signs_idempotency_key, kill_mutation="sign_omit_idem", versions=["2026-04-08"]),
    ACheck("agent.signs_ucp_agent_component", ["SIG-018"], "MUST",
           p_signs_ucp_agent_component, kill_mutation="ucp_agent_not_signed",
           versions=["2026-04-08"]),
    ACheck("agent.processes_auth_challenge", ["IDL-008"], "MUST",
           p_processes_auth_challenge, kill_mutation="no_bearer_retry",
           versions=["2026-04-08"], scenario="auth_challenge"),
    ACheck("agent.sends_bearer_token", ["IDL-007"], "MUST",
           p_sends_bearer_token, kill_mutation="no_bearer_header",
           versions=["2026-04-08"], scenario="auth_challenge"),
    ACheck("agent.extracts_challenge_scope", ["IDL-009"], "MUST",
           p_extracts_challenge_scope, kill_mutation="ignore_challenge_scope",
           versions=["2026-04-08"], scenario="auth_challenge"),
    ACheck("agent.public_client_pkce_proof", ["IDL-004"], "MUST",
           p_public_client_pkce_proof, kill_mutation="no_pkce_verifier",
           versions=["2026-04-08"], scenario="auth_challenge"),
    ACheck("agent.no_client_secret", ["IDL-005"], "MUST NOT",
           p_no_client_secret, kill_mutation="embed_client_secret",
           versions=["2026-04-08"], scenario="auth_challenge"),
    ACheck("agent.validates_oauth_state", ["IDL-035"], "MUST",
           p_validates_oauth_state, kill_mutation="skip_state_validation",
           versions=["2026-04-08"], scenario="bad_state"),
    ACheck("agent.validates_issuer_match", ["IDL-033"], "MUST",
           p_validates_issuer_match, kill_mutation="normalize_issuer",
           versions=["2026-04-08"], scenario="bad_issuer"),
    ACheck("agent.aborts_on_discovery_error", ["IDL-031", "IDL-032"], "MUST",
           p_aborts_on_discovery_error, kill_mutation="oidc_fallthrough_on_error",
           versions=["2026-04-08"], scenario="discovery_error"),
]
