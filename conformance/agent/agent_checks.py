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
]
