#!/usr/bin/env python3
"""
keywords.py — the single source of the RFC 2119 keyword classes the suite accounts by
(PLAN-v3 §2.4 / A1, task D2-01).

Before this module, FIVE sites each hand-held their own slice of "which keywords are
mandatory": matrix.py (`account()` and `export_json()`), coverage_gate.py's exemption
denominator, agent_matrix.py's agent-subject rows, and the two census regexes in
verify_register_completeness.py / verify_schema_census.py. The accounting sites used the
pair ("MUST", "MUST NOT") while the census regexes already matched SHALL / SHALL NOT /
REQUIRED — so a register row filed under `keyword: REQUIRED` (SIG-039 "Extract the
`profile` key (REQUIRED)", signatures.md#L618 at 2026-08-25; SIG-039 + OVR-002 at
2026-04-08) was counted by the completeness census as a mandatory hit but silently
dropped from the coverage denominator. One tuple, imported everywhere, ends that seam.

Classes (RFC 2119 §1–§5; all-caps normative form only):
  MANDATORY     — MUST, MUST NOT, SHALL, SHALL NOT, REQUIRED   (the accounting denominator)
  SHOULD_CLASS  — SHOULD, SHOULD NOT, RECOMMENDED, NOT RECOMMENDED (census report-only, D2-10)
  MAY_CLASS     — MAY, OPTIONAL

KW_RE matches the MANDATORY class in prose, alternatives ordered LONGEST-FIRST so
"MUST NOT" wins over "MUST" and "SHALL NOT" over "SHALL". The order is derived from the
tuple, never hand-written, so the regex cannot drift from the tuple.

Import pattern (matches conformance/common/spec_versions.py):
    sys.path.insert(0, <path to conformance/>)
    from common.keywords import MANDATORY, KW_RE
"""
import re

MANDATORY = ("MUST", "MUST NOT", "SHALL", "SHALL NOT", "REQUIRED")
SHOULD_CLASS = ("SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED")
MAY_CLASS = ("MAY", "OPTIONAL")


def _longest_first(words):
    """Alternation body for a keyword class, longest alternative first so a
    two-word form is never shadowed by its one-word prefix."""
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


KW_RE = re.compile(r"\b(" + _longest_first(MANDATORY) + r")\b")
SHOULD_RE = re.compile(r"\b(" + _longest_first(SHOULD_CLASS) + r")\b")


def is_mandatory(keyword):
    """True when a register row's `keyword` field is in the MANDATORY class."""
    return keyword in MANDATORY
