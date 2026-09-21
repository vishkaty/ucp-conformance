#!/usr/bin/env python3
"""
checks.py: the four provider selection checks (ucp #667 Gap 2), graded on a harness run
record. Shape follows agent/agent_checks.py (predicate over an observed session log, one
named kill_mutation that MUST make the check fail). Each check is a list of named
assertions so killtest.py can excise them one by one and prove none is decoration.

Register rows: requirements-drafts/next/identity-linking-provider-selection.json (pending
ids IDL-P01..IDL-P05, quoting the proposed clause text at the upstream branch commit),
plus the existing 2026-08-25 rows IDL-067 (fallback MUST) and IDL-073 the rule sits beside.
"""
CLEAN, DEVIATION = "CLEAN", "DEVIATION"


def _derived_from(url, base):
    return url.startswith(base.rstrip("/") + "/") or url == base or url.startswith(base + "/")


def _exchanges(run):
    return [e for e in run["session"] if e["op"] == "token_exchange"]


def _grant_iss(assertion):
    import json, base64
    try:
        p = assertion.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("iss")
    except Exception:
        return None


class PCheck:
    def __init__(self, cid, req_ids, keyword, scenario, kill_mutation, assertions, clause):
        self.id, self.req_ids, self.keyword = cid, list(req_ids), keyword
        self.scenario, self.kill_mutation = scenario, kill_mutation
        self.assertions = list(assertions)      # [(name, fn(run) -> evidence or None)]
        self.clause = clause                    # the proposed clause, for the report

    def predicate(self, run):
        for name, fn in self.assertions:
            ev = fn(run)
            if ev:
                return DEVIATION, "%s: %s" % (name, ev)
        return CLEAN, ""


# ---- assertions ---------------------------------------------------------------------
def a_hostile_log_empty(run):
    h = run["oracle"]["hostile"]
    return "hostile AS received %d request(s): %s" % (len(h), h) if h else None


def a_no_session_request_to_hostile(run):
    bad = [e for e in run["session"] if _derived_from(e["url"], run["hostile_issuer"])]
    return "platform sent %s to endpoints derived from the hostile auth_url" % [e["op"] for e in bad] if bad else None


def a_exactly_one_exchange_at_honest(run):
    ex = _exchanges(run)
    if len(ex) != 1:
        return "expected exactly one token exchange, saw %d" % len(ex)
    if not _derived_from(ex[0]["url"], run["honest_issuer"]):
        return "token exchange went to %s, not the recognized issuer" % ex[0]["url"]
    return None


def a_subject_token_is_the_honest_one(run):
    ex = _exchanges(run)
    bad = [e for e in ex if e["form"].get("subject_token") != run["subject_token"]]
    return "subject_token presented was not the one minted by the recognized IdP" if bad or not ex else None


def a_business_got_grant_from_honest(run):
    posts = run["oracle"]["business"]
    grants = [p for p in posts if p["method"] == "POST" and p["path"] == "/token"]
    iss = [_grant_iss(p["form"].get("assertion", "")) for p in grants]
    if not grants or any(i != run["honest_issuer"] for i in iss):
        return "business received grants with iss %s (expected %s)" % (iss, run["honest_issuer"])
    return None


def a_outcome_chained(run):
    return None if run["outcome"] == "chained" else "outcome %s" % run["outcome"]


def a_outcome_fallback(run):
    return None if run["outcome"] == "fallback_direct_oauth" else "outcome %s, expected fallback_direct_oauth" % run["outcome"]


def a_no_exchange_anywhere(run):
    ex = _exchanges(run)
    idp_posts = [r for r in run["oracle"]["idp"] if r["method"] == "POST"]
    return "token exchange happened (%d in session, %d at the IdP)" % (len(ex), len(idp_posts)) if ex or idp_posts else None


def a_direct_oauth_started_on_business(run):
    ok = any(e["op"] == "discover_business_metadata" and _derived_from(e["url"], run["business_issuer"])
             for e in run["session"])
    return None if ok else "platform did not discover the business authorization server (direct OAuth fallback)"


CLAUSE_SELECT = ("For `oauth2` entries, platforms **MUST** select an entry only when its `auth_url` is "
                 "identical to a recognized issuer identifier, compared as character strings with no normalization")
CLAUSE_NEVER = ("Platforms **MUST NOT** run metadata discovery against, authenticate to, or present a "
                "`subject_token` to any endpoint derived from an `auth_url` that fails this check.")
CLAUSE_SUBJECT = "The `subject_token` presented in the [Flow](#flow) **MUST** have been issued by that IdP."
CLAUSE_FALLBACK = ("An `oauth2` entry that fails this check is not suitable; when no listed entry is suitable, "
                   "platforms fall back to direct OAuth on the business domain as required in "
                   "[Identity Providers](#identity-providers).")

CHECKS = [
    PCheck("pending.idl.never_presents_subject_token_to_unmatched_endpoint",
           ["IDL-P03", "IDL-P05"], "MUST NOT", "hostile_first", "select_by_key",
           [("hostile_log_empty", a_hostile_log_empty),
            ("no_session_request_to_hostile", a_no_session_request_to_hostile)], CLAUSE_NEVER),
    PCheck("pending.idl.selects_only_recognized_issuer",
           ["IDL-P01", "IDL-P02"], "MUST", "familiar_key_hostile_only", "select_by_key",
           [("exactly_one_exchange_at_honest", a_exactly_one_exchange_at_honest),
            ("subject_token_is_the_honest_one", a_subject_token_is_the_honest_one),
            ("business_got_grant_from_honest", a_business_got_grant_from_honest),
            ("outcome_chained", a_outcome_chained)], CLAUSE_SELECT + "; " + CLAUSE_SUBJECT),
    PCheck("pending.idl.exact_match_no_normalization",
           ["IDL-P01"], "MUST", "lookalike_slash", "normalize_issuer",
           [("outcome_fallback", a_outcome_fallback),
            ("no_exchange_anywhere", a_no_exchange_anywhere)], CLAUSE_SELECT),
    PCheck("pending.idl.falls_back_when_nothing_matches",
           ["IDL-P04", "IDL-067"], "MUST", "nothing_matches", "select_by_profile",
           [("outcome_fallback", a_outcome_fallback),
            ("direct_oauth_started_on_business", a_direct_oauth_started_on_business),
            ("hostile_log_empty", a_hostile_log_empty),
            ("no_exchange_anywhere", a_no_exchange_anywhere)], CLAUSE_FALLBACK),
]
