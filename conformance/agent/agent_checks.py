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

CLEAN = "CLEAN"
DEVIATION = "DEVIATION"


class ACheck:
    def __init__(self, cid, req_ids, keyword, predicate, kill_mutation,
                 versions=None, capability=None, needs=()):
        self.id = cid
        self.req_ids = list(req_ids)
        self.keyword = keyword
        self.predicate = predicate          # predicate(session_log) -> CLEAN | DEVIATION
        self.kill_mutation = kill_mutation  # the defect id that MUST make this check fail
        self.versions = tuple(versions) if versions else None
        self.capability = capability
        self.needs = tuple(needs)


# --- P0 example predicates will live here in Phase B, e.g.:
# def p_sends_ucp_agent(log):
#     reqs = [e["request"] for e in log]
#     ok = all('UCP-Agent' in (r["headers"] or {}) for r in reqs)
#     return CLEAN if ok else DEVIATION
# CHECKS = [ ACheck("agent.sends_ucp_agent", ["DISC-006"], "MUST",
#                   p_sends_ucp_agent, kill_mutation="no_ucp_agent",
#                   versions=["2026-04-08"]) ]

CHECKS = []   # Phase A: intentionally empty — the lane must run green with zero checks.
