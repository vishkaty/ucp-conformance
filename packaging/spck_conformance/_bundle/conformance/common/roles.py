#!/usr/bin/env python3
"""
roles.py — the single source of the register's `role` vocabulary and the lane each
role enters (PLAN-v3 §2.4/§2.5 / A4, task D2-08; decision 27 for the three
non-business/non-platform roles).

Every mandatory register row carries `role` (who the obligation binds) and
`role_provenance` (how the role was assigned). Both lanes read the SAME field —
matrix.py's per-role denominators and agent_matrix.agent_rows() — so the two
lanes' denominators partition the MUSTs by construction (W1-4):

    merchant + agent − both + other == musts

Roles (ROLES) and their lane (decision 27, CLOSED 2026-09-10):
  business     the Business / merchant / server            -> merchant lane
  platform     the Platform / agent / client / consumer    -> agent lane
  both         binds both parties (CAT-043 two-role)       -> both lanes
  handler      payment handler / PSP / tokenizer / issuer  -> merchant lane (reference-impl evidence from our stubs)
  host         host page / iframe / webview (embedded UI)  -> agent lane (platform side)
  spec-author  specification / extension / schema authors -> other (speclint `register-selfcheck`, D2-18)

Provenance (PROVENANCE, plus `review:<batch>` for an adjudicated row — the batch names
a review_signoffs.json entry that carries the >=10% human sample):
  agent-lock              seeded from agent_denominator_lock.json (the reviewed agent denominator)
  not-agent-bound         seeded from the retired NOT_AGENT_BOUND override (business-only by audit)
  client-bound-exemption  seeded from a `client-bound` exemption at any version
  subject                 the last actor noun before the mandatory keyword (assign_roles.py)
  direction               passive row resolved by message direction (request -> platform, response -> business)
  backported              copied with the row from another version (D2-15 LOY rows keep the source row's role)

Import pattern (matches conformance/common/keywords.py):
    sys.path.insert(0, <path to conformance/>)
    from common.roles import ROLES, MERCHANT_LANE, AGENT_LANE, lane_of
"""

ROLES = ("business", "platform", "both", "handler", "spec-author", "host")

PROVENANCE = ("agent-lock", "not-agent-bound", "client-bound-exemption", "subject",
              "direction", "backported")
REVIEW_PREFIX = "review:"

MERCHANT_LANE = frozenset({"business", "both", "handler"})
AGENT_LANE = frozenset({"platform", "both", "host"})
OTHER_LANE = frozenset({"spec-author"})


def valid_provenance(p):
    """True for an enum provenance or `review:<non-empty batch>`."""
    return p in PROVENANCE or (isinstance(p, str) and p.startswith(REVIEW_PREFIX)
                               and len(p) > len(REVIEW_PREFIX))


def lanes_of(role):
    """The lane names a role enters: subset of {merchant, agent, other}."""
    out = set()
    if role in MERCHANT_LANE:
        out.add("merchant")
    if role in AGENT_LANE:
        out.add("agent")
    if role in OTHER_LANE:
        out.add("other")
    return out
