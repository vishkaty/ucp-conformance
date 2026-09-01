#!/usr/bin/env python3
"""
validate_spec_versions.py — kill-test for the version-map consolidation
(PLAN-0825 G0-b / A.4: "one shared version map instead of the ~5 duplicated
hardcoded lists found today").

Before this fix, five files each hand-maintained a copy of "what spec versions
exist": conformance/coverage/matrix.py, conformance/agent/agent_matrix.py (whose
copy had already silently drifted — it never gained 2026-08-25), conformance/
selfcheck/verify_register.py, conformance/selfcheck/verify_register_completeness.py,
and conformance/speclint/speclint.py (also missing 2026-08-25). A selftest that only
checks "today's five lists happen to agree" would not have caught that drift, and
would not stop a future one — it would just have shown five copies of the current
right answer. This proves something stronger: every consumer holds the SAME object
conformance/common/spec_versions.py owns (so there is no copy left to drift), a
version appended to that one source becomes visible everywhere immediately, an
unknown version is refused loudly rather than silently defaulted, and the concrete
regression that motivated this ticket (agent_matrix/speclint missing 2026-08-25)
stays fixed. It also kill-tests the adjacent stale-waiver fail-noisy fix (PLAN-0825
A.2) verify_register_completeness.py gained in the same landing.

Hermetic: no server, no network. Reads the vendored spec trees (already required by
verify_register_completeness.py itself) to prove the stale-waiver check against real
paths.

Exit 0 = every proof holds; 1 = a regression in the version-map wiring.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.dirname(HERE)

sys.path.insert(0, CONF)                              # common.spec_versions
sys.path.insert(0, os.path.join(CONF, "coverage"))    # matrix (+ its sibling evidence)
sys.path.insert(0, os.path.join(CONF, "agent"))       # agent_matrix
sys.path.insert(0, HERE)                              # verify_register(_completeness)
sys.path.insert(0, os.path.join(CONF, "speclint"))    # speclint (+ its sibling parsers/rules)

from common import spec_versions                             # noqa: E402
import matrix                                                 # noqa: E402
import agent_matrix                                            # noqa: E402
import agent_governance                                        # noqa: E402
import verify_register                                         # noqa: E402
import verify_register_completeness as vrc                     # noqa: E402
import speclint                                                 # noqa: E402


def _check_identity(fails):
    """Every consumer must hold the SAME object spec_versions.py owns, not an
    equal-by-value copy that could silently diverge the next time someone "inlines"
    one call site. `from module import NAME` binds a reference to the object, so
    this also fails the moment a future edit reintroduces a private literal
    (`VERSIONS = [...]`, etc.) in any of the five files instead of importing it."""
    identity_checks = [
        ("matrix.VERSIONS", matrix.VERSIONS, spec_versions.VERSIONS),
        ("matrix.REGISTER_ONLY_VERSIONS", matrix.REGISTER_ONLY_VERSIONS,
         spec_versions.REGISTER_ONLY_VERSIONS),
        ("agent_matrix.VERSIONS", agent_matrix.VERSIONS, spec_versions.VERSIONS),
        ("agent_matrix.AGENT_REGISTER_ONLY_VERSIONS",
         agent_matrix.AGENT_REGISTER_ONLY_VERSIONS,
         spec_versions.AGENT_REGISTER_ONLY_VERSIONS),
        ("verify_register.VERSION_TREE", verify_register.VERSION_TREE,
         spec_versions.VERSION_TREE),
        ("verify_register_completeness.VERSION_TREE", vrc.VERSION_TREE,
         spec_versions.VERSION_TREE),
        ("verify_register_completeness.REPORT_MODE_UNTIL", vrc.REPORT_MODE_UNTIL,
         spec_versions.REPORT_MODE_UNTIL),
        ("speclint.VERSION_TREE", speclint.VERSION_TREE, spec_versions.VERSION_TREE),
    ]
    for name, got, want in identity_checks:
        ok = got is want
        print(f"  {'OK ' if ok else 'XX '} {name} is spec_versions's own object: {ok}")
        if not ok:
            fails.append(f"IDENTITY: {name} is a SEPARATE object from spec_versions's "
                         f"— a private copy crept back in (or was never removed)")
    # CURRENT_SITE_VERSION is a plain str; rebinding a module attribute elsewhere
    # can't be caught by identity, so value-equality is the meaningful assertion
    # that no OTHER hand-typed literal exists.
    ok = matrix.CURRENT_SITE_VERSION == spec_versions.CURRENT_SITE_VERSION
    print(f"  {'OK ' if ok else 'XX '} matrix.CURRENT_SITE_VERSION == spec_versions's: {ok}")
    if not ok:
        fails.append("matrix.CURRENT_SITE_VERSION disagrees with "
                     "spec_versions.CURRENT_SITE_VERSION")


def _check_new_version_appears_everywhere(fails):
    """THE kill-test proper: mutate the ONE shared source and confirm every
    consumer sees it immediately, because they hold the same object — the
    mechanical proof that "add a version" is now a single edit, not five.
    Restores state in `finally` so no other gate in the same run sees the
    synthetic version."""
    syn_v, syn_tree = "2999-01-01", "ucp-2999-01-01"
    spec_versions.VERSIONS.append(syn_v)
    spec_versions.VERSION_TREE[syn_v] = syn_tree
    try:
        propagation_checks = [
            ("matrix.VERSIONS", syn_v in matrix.VERSIONS),
            ("agent_matrix.VERSIONS", syn_v in agent_matrix.VERSIONS),
            ("verify_register.VERSION_TREE",
             verify_register.VERSION_TREE.get(syn_v) == syn_tree),
            ("verify_register_completeness.VERSION_TREE",
             vrc.VERSION_TREE.get(syn_v) == syn_tree),
            ("speclint.VERSION_TREE", speclint.VERSION_TREE.get(syn_v) == syn_tree),
            ("speclint._vendor_dir(new version)",
             speclint._vendor_dir(syn_v) == speclint.ROOT / "conformance" / ".vendor" / syn_tree),
        ]
        for name, ok in propagation_checks:
            print(f"  {'OK ' if ok else 'XX '} synthetic version visible via {name}: {ok}")
            if not ok:
                fails.append(f"a version appended to spec_versions did NOT appear via "
                             f"{name} — that consumer holds a separate/stale copy")
    finally:
        spec_versions.VERSIONS.remove(syn_v)
        del spec_versions.VERSION_TREE[syn_v]


def _check_unknown_version_fails_loud(fails):
    """The flip side of the above: a version string NOT in the source must never
    silently resolve to a plausible-looking default — it must error, so a
    typo'd/retired version can't quietly grade against the wrong vendor tree."""
    bogus = "1970-01-01"
    thunks = [
        ("spec_versions.VERSION_TREE[bogus]", lambda: spec_versions.VERSION_TREE[bogus]),
        ("spec_versions.vendor_dir(bogus)", lambda: spec_versions.vendor_dir(bogus)),
        ("speclint._vendor_dir(bogus)", lambda: speclint._vendor_dir(bogus)),
    ]
    for name, thunk in thunks:
        try:
            thunk()
            print(f"  XX  {name} did not raise — a bogus version resolved silently")
            fails.append(f"{name} should raise KeyError on an unknown version but did not")
        except KeyError:
            print(f"  OK  {name} raises KeyError on an unknown version")


def _check_regression_fixed(fails):
    """The literal bug this ticket closes, pinned with the REAL newest version (not
    just the synthetic one above) as concrete, non-hypothetical proof: agent_matrix
    and speclint used to carry their own version lists that had silently drifted —
    agent_matrix never gained 2026-08-25 at all; speclint's VERSION_TREE was missing
    it too. Also proves the register-only wall (matrix.py's existing doctrine) was
    carried onto the agent axis rather than left as a real-but-unreviewed count."""
    newest = spec_versions.VERSIONS[-1]
    ok_agent = newest in agent_matrix.VERSIONS
    ok_speclint = newest in speclint.VERSION_TREE
    print(f"  {'OK ' if ok_agent else 'XX '} newest version {newest!r} visible in "
          f"agent_matrix.VERSIONS: {ok_agent}")
    print(f"  {'OK ' if ok_speclint else 'XX '} newest version {newest!r} visible in "
          f"speclint.VERSION_TREE: {ok_speclint}")
    if not ok_agent:
        fails.append(f"agent_matrix.VERSIONS is missing {newest!r} — the exact "
                     f"regression this ticket fixed")
    if not ok_speclint:
        fails.append(f"speclint.VERSION_TREE is missing {newest!r} — the exact "
                     f"regression this ticket fixed")

    if newest in spec_versions.AGENT_REGISTER_ONLY_VERSIONS:
        rows = agent_matrix.agent_rows(newest)
        ok_wall = rows == set()
        print(f"  {'OK ' if ok_wall else 'XX '} agent_matrix.agent_rows({newest!r}) is "
              f"empty (agent register-only wall honored): {ok_wall}")
        if not ok_wall:
            fails.append(f"agent_matrix.agent_rows({newest!r}) returned {len(rows)} "
                         f"row(s) for an AGENT_REGISTER_ONLY_VERSIONS version — the "
                         f"wall isn't wired on the agent axis")


def _check_agent_wall_independent_of_merchant_wall(fails):
    """The incident this split fixed, kill-tested directly (2026-08-31): the two
    walls (matrix.py's REGISTER_ONLY_VERSIONS, agent_matrix.py's
    AGENT_REGISTER_ONLY_VERSIONS) must be able to disagree — one lane graduating a
    version must never silently graduate the other. Proven both directions:

      1. The merchant wall is ALREADY empty (2026-08-25 graduated there) while the
         agent wall still holds 2026-08-25 — the two sets disagreeing right now,
         live, is the steady state this split exists to make possible.
      2. Simulate the agent lane's OWN graduation happening the wrong way — a
         version REMOVED from AGENT_REGISTER_ONLY_VERSIONS with no matching
         agent_denominator_lock.json update (i.e. exactly the un-reviewed removal
         that caused this incident) — and prove agent_governance.py's
         DENOMINATOR-DRIFT lock reddens for that version. A wall split that can't
         be shown to still be backstopped by the lock would just be moving the
         hazard, not closing it.

    Mutates spec_versions.AGENT_REGISTER_ONLY_VERSIONS in place (same object
    agent_matrix holds — see _check_identity) and restores it in `finally`, exactly
    like _check_new_version_appears_everywhere's synthetic-version pattern."""
    merchant_ver = spec_versions.VERSIONS[-1]     # 2026-08-25 today
    disagree = (merchant_ver not in spec_versions.REGISTER_ONLY_VERSIONS
                and merchant_ver in spec_versions.AGENT_REGISTER_ONLY_VERSIONS)
    print(f"  {'OK ' if disagree else 'XX '} the two walls currently DISAGREE on "
          f"{merchant_ver!r} (merchant graduated, agent has not): {disagree}")
    if not disagree:
        fails.append(f"the merchant and agent walls agree on {merchant_ver!r} right "
                     f"now — this proves nothing about independence; expected the "
                     f"merchant lane graduated ahead of the agent lane")

    if merchant_ver not in spec_versions.AGENT_REGISTER_ONLY_VERSIONS:
        print(f"  SKIP simulated-removal check: {merchant_ver!r} is not currently "
              f"agent-register-only, nothing to remove")
        return

    fails_before, _ = agent_governance.run()
    drift_before = [f for f in fails_before if f"DENOMINATOR-DRIFT {merchant_ver}" in f]
    ok_clean_before = not drift_before
    print(f"  {'OK ' if ok_clean_before else 'XX '} agent_governance is clean on "
          f"{merchant_ver!r} BEFORE the simulated removal: {ok_clean_before}")
    if not ok_clean_before:
        fails.append(f"agent_governance already shows DENOMINATOR-DRIFT for "
                     f"{merchant_ver!r} before any mutation — the committed lock "
                     f"and the wall already disagree")

    spec_versions.AGENT_REGISTER_ONLY_VERSIONS.discard(merchant_ver)
    try:
        fails_after, _ = agent_governance.run()
        drift_after = [f for f in fails_after if f"DENOMINATOR-DRIFT {merchant_ver}" in f]
        ok_catches = bool(drift_after)
        print(f"  {'OK ' if ok_catches else 'XX '} agent_governance reddens with "
              f"DENOMINATOR-DRIFT {merchant_ver!r} AFTER simulating an un-reviewed "
              f"wall removal (lock still bites): {ok_catches}")
        if not ok_catches:
            fails.append(f"removing {merchant_ver!r} from AGENT_REGISTER_ONLY_VERSIONS "
                         f"without a matching agent_denominator_lock.json regeneration "
                         f"did NOT redden agent_governance's DENOMINATOR-DRIFT check — "
                         f"the exact 2026-08-31 incident would recur silently")
    finally:
        spec_versions.AGENT_REGISTER_ONLY_VERSIONS.add(merchant_ver)


def _check_stale_waiver_fail_noisy(fails):
    """PLAN-0825 A.2's kill-test, verbatim: 'plant a waiver pointing at a ghost file
    -> red.' Proves verify_register_completeness.stale_file_errors() both catches a
    waiver/scope-exclusion whose target file does not exist in that version's
    vendor tree, AND does not false-positive on a real, existing one (a check that
    flags everything is as useless as one that flags nothing)."""
    ghost = "docs/specification/this-file-does-not-exist-anywhere.md"
    real_ver = spec_versions.VERSIONS[0]
    real_file = "docs/specification/overview.md"     # present in every vendored tree

    planted_waiver_idx = {(real_ver, ghost, 1): {"version": real_ver, "file": ghost, "line": 1}}
    planted_scope_idx = {(real_ver, ghost): {"file": ghost, "versions": [real_ver]}}
    planted_errs = vrc.stale_file_errors(planted_waiver_idx, planted_scope_idx)
    ok_catches = len(planted_errs) == 2       # one from the waiver, one from the scope exclusion
    print(f"  {'OK ' if ok_catches else 'XX '} a waiver + scope-exclusion at a ghost "
          f"path are both caught: {len(planted_errs)} error(s) (want 2)")
    if not ok_catches:
        fails.append(f"stale_file_errors did not catch a planted ghost-path waiver + "
                     f"scope exclusion (got {len(planted_errs)}, want 2) — the "
                     f"fail-noisy fix isn't wired")

    clean_waiver_idx = {(real_ver, real_file, 1): {"version": real_ver, "file": real_file, "line": 1}}
    clean_scope_idx = {(real_ver, real_file): {"file": real_file, "versions": [real_ver]}}
    clean_errs = vrc.stale_file_errors(clean_waiver_idx, clean_scope_idx)
    ok_clean = len(clean_errs) == 0
    print(f"  {'OK ' if ok_clean else 'XX '} a waiver + scope-exclusion at a REAL "
          f"path are NOT flagged: {len(clean_errs)} error(s) (want 0)")
    if not ok_clean:
        fails.append(f"stale_file_errors FALSE-POSITIVED on a real, existing file "
                     f"(got {len(clean_errs)} error(s), want 0) — would break every "
                     f"legitimate waiver/scope exclusion")


def main():
    fails = []
    print("spec-versions single-source identity:")
    _check_identity(fails)
    print("\na version added to the shared source appears in every consumer (mutate + observe):")
    _check_new_version_appears_everywhere(fails)
    print("\na version NOT in the shared source cannot be silently resolved:")
    _check_unknown_version_fails_loud(fails)
    print("\nthe concrete regression this ticket fixed stays fixed:")
    _check_regression_fixed(fails)
    print("\nthe agent wall is independent of the merchant wall, and the lock still "
          "bites an un-reviewed removal (2026-08-31 incident kill-test):")
    _check_agent_wall_independent_of_merchant_wall(fails)
    print("\nstale-waiver fail-noisy rule (PLAN-0825 A.2 kill-test):")
    _check_stale_waiver_fail_noisy(fails)

    print()
    for f in fails:
        print(f"  x {f}")
    print(f"spec-versions-map: {'PASS' if not fails else f'FAIL ({len(fails)} issue(s))'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
