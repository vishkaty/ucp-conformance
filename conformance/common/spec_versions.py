#!/usr/bin/env python3
"""
spec_versions.py — the single source of truth for "what spec versions exist, and what
do we call them" (PLAN-0825 G0-b / A.4: kill the version-map whack-a-mole seam).

Before this module, FIVE call sites each hand-maintained their own copy of some slice
of this data: conformance/coverage/matrix.py (VERSIONS + CURRENT_SITE_VERSION +
REGISTER_ONLY_VERSIONS), conformance/agent/agent_matrix.py (its own VERSIONS — which
had silently drifted, missing 2026-08-25 entirely), conformance/selfcheck/
verify_register.py (VERSION_TREE), conformance/selfcheck/verify_register_completeness.py
(VERSION_TREE + REPORT_MODE_UNTIL), and conformance/speclint/speclint.py (its own
VERSION_TREE, also missing 2026-08-25). Adding a version meant N hand-edits, and a
missed one (agent_matrix, speclint) was silent — the exact P-7 whack-a-mole this module
exists to kill. Every consumer now imports its slice of this file instead of
re-declaring it; there is exactly one place left to edit when a 5th version lands.

Import pattern (matches the existing conformance/common/{crypto,sdjwt}.py convention —
see conformance/ci/crypto_interop.py, conformance/agent/reference_agent.py):
    sys.path.insert(0, <path to conformance/>)
    from common.spec_versions import VERSIONS, VERSION_TREE, ...

What lives here, and why each field is DATA rather than a formula:

  VERSIONS
      The ordered list of spec versions that have a register tree under
      conformance/requirements/. Append-only, and a version joins only via the reasoned
      commit that lands its register (never inferred from SOURCES.lock.json, which may
      be pinned ahead of the register work — a version can be PINNED without being
      REGISTERED; matrix.py's own _pinned_spec_versions() reads that separate gap).

  VERSION_TREE
      {version: vendor-directory-name under conformance/.vendor}. This is genuinely
      irregular data, not a formula: the FIRST vendored spec tree kept the bare name
      "ucp" (there was no other version yet to disambiguate from), and every version
      vendored since follows the "ucp-YYYY-MM-DD" convention — so "2026-04-08" maps to
      "ucp" while every other version maps to its own "ucp-<version>" directory. Do not
      "simplify" this to f"ucp-{version}" for all versions or a hand-fix for one: the
      bare "ucp" alias is a historical/vendoring fact (conformance/ci/fetch_sources.sh
      owns the fetch side of it), not a rule that a future version will also follow.

  CURRENT_SITE_VERSION
      The version whose accounted-coverage figures the public site currently
      advertises. Deliberately a NAMED constant, not VERSIONS[-1]: a newly added spec
      version starts register-only (no site copy, no ratchet floor, no
      coverage-lock/review-signoff entries) and must not silently become "current" for
      site claims just by being appended to VERSIONS. Bump this only when the site is
      deliberately migrated to a new version's published figures.

  REGISTER_ONLY_VERSIONS
      Deliberately curated, and NEVER mechanically derived — this is the one field in
      this module that is data-driven "where possible" and genuinely is not: a version
      leaves this set only via an explicit per-id review pass (the "Check conversion
      phase" of PLAN-0825 §G), never by a formula noticing the register looks complete,
      never by aging out, and never by coverage_lock.json / review_signoffs.json
      happening to gain entries for it (that would make partial, un-reviewed progress
      look like graduation — precisely backwards). Every version listed here is forced
      to GAP/zero wherever coverage is accounted (matrix.py's attribution()/exempt_at(),
      agent_matrix.py's agent_rows()) regardless of what the register or exemptions.json
      would otherwise compute. Landing normalization (2026-08-30) added 2026-08-25 here
      after L2's new-surface rows proved the auto-attribution hazard concretely: 51
      checks (26 live-wire!) and 24 exemptions auto-attributed to it the moment its
      register existed, none reviewed.

      GRADUATED 2026-08-31 (the "Check conversion phase" flip, PLAN-0825 §G4): removed
      from this set once, and only once, EVERY currently-attributing id had actually
      been reviewed — not once the count merely looked plausible. The review found the
      51/26/24 auto-attribution above was a real leak with TWO independent root causes,
      both fixed at the source rather than papered over by leaving this wall up
      forever (a permanent wall is not a fix, it's a permanent GAP):
        (1) unreviewed checks with no per-check `versions=` (area_fulfillment.py,
            area_negotiation.py, area_payment.py, merchant_checks.py) were falling
            through to "every pinned version" the moment 2026-08-25's register
            existed — fixed with an explicit per-FILE `VERSIONS` marker naming the
            versions each file has actually been reviewed at (never a formula
            excluding "the new one" — that repeats this exact bug at the 5th
            version); ditto 25 unscoped conformance/coverage/exemptions.json entries,
            fixed with explicit `"versions"` lists.
        (2) the evidence-class reach report (coverage/reach_report.json) had NO
            spec-version axis in its keys, so a check's live-wire corroboration at
            one version silently applied to every OTHER version the same check
            object was attributed to — proven live: it would have handed 26 checks
            live-wire credit for 2026-08-25's zero-independent-implementation
            reality. Fixed generally (coverage/evidence.py's reach_key/classify_check
            now take the version being evaluated; gen_reach_report.py's keys carry
            it) — not special-cased to 08-25, so the fix also correctly demoted the
            SAME latent leak already affecting 2026-01-11/2026-01-23 (49/53 published
            live-wire, earned by neither — the committed reach report has only ever
            graded 2026-04-08). See ops/GAP-LEDGER-0825.md / the flip landing note for
            the full before/after.
      After both fixes, lifting this wall attributed EXACTLY the reviewed rows: the
      17 struct_check_08_25.py + 10 golden_check_08_25.py conversion-phase checks (27
      CHECK, 0 EXEMPT, 0 live-wire — correctly zero, since 2026-08-25 still has no
      independently-authored implementation). The MECHANISM stays (this set, not a
      one-time patch): the next id to gain a check goes through the same per-check
      `versions=` review before it can attribute, exactly like every version before
      it, and a 5th spec version starts life back in this set until ITS conversion
      phase runs.

  REPORT_MODE_UNTIL
      {version: ISO flip-by date}. A version in this dict is still having its
      register-completeness census BUILT (rows are landing, but the census-closing
      tooling has not), so verify_register_completeness.py PRINTS its unaccounted
      keyword count instead of gating the build on it — the honest middle between
      blocking every commit on ~500 unaccounted hits and inviting padding the register
      to force it green. This is itself a live claim ("actively closing this, not
      ignoring it forever") and must die loudly if unmet: past the flip-by date, an
      unclosed report-mode version turns the gate RED instead of silently staying quiet
      (fail-noisy self-expiry, the same mechanism this suite already uses for
      allowlists/waivers — see conformance/ci/differential_allowlist.json and friends).
      A version leaves this dict only by the census actually reaching GATE mode (at its
      landing per PLAN-0825 §G) or by the date passing, whichever is first — never by
      the date quietly passing unnoticed.

Versions that are legitimately SCOPED to a subset of VERSIONS (a check that only ever
applied at 2026-01-11, a waiver that only applies at 2026-04-08) keep their own explicit
scoping wherever they already declare it (chk.versions, an exemptions.json entry's
"versions" list, a waiver's "versions" field) — this module is the enumeration of what
versions EXIST, not a replacement for per-item scoping.

matrix.py separately reads SOURCES.lock.json for pinned commit SHAs / the
`unregistered`-state pinned-but-not-yet-registered set (_spec_pins/_pinned_spec_versions)
— that stays there rather than duplicated here, since it returns pin metadata (commit
SHAs), a different shape than the version ENUMERATION this module owns.
"""

VERSIONS = ["2026-01-11", "2026-01-23", "2026-04-08", "2026-08-25"]

VERSION_TREE = {
    "2026-04-08": "ucp",
    "2026-01-23": "ucp-2026-01-23",
    "2026-01-11": "ucp-2026-01-11",
    "2026-08-25": "ucp-2026-08-25",
}

CURRENT_SITE_VERSION = "2026-04-08"

# Empty as of 2026-08-31 — 2026-08-25 graduated the Check-conversion-phase review
# (see this module's docstring above for the two root causes found + fixed, not
# suppressed). The set/mechanism stays: a future 5th version starts life IN here.
REGISTER_ONLY_VERSIONS = set()

REPORT_MODE_UNTIL = {
    # 2026-08-25 graduated 2026-08-31: census closed at 0 unaccounted (922 kw =
    # 717 rows + 142 scope-excl + 63 waivers), 20 days ahead of the flip-by date.
    # The census now GATES for every pinned version.
}


def vendor_dir(version, default=None):
    """The conformance/.vendor/<dir> name for `version`. Fails loud (KeyError) on an
    unknown version when `default` is None — the same discipline speclint.py's original
    inline VERSION_TREE[version] lookup already had; pass `default` only where the
    original call site tolerated a fallback (verify_register.py's VERSION_TREE.get(ver,
    "ucp"))."""
    return VERSION_TREE[version] if default is None else VERSION_TREE.get(version, default)
