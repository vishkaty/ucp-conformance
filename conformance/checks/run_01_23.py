#!/usr/bin/env python3
"""
run_01_23.py — aggregate the 2026-01-23 check modules and produce the honest,
coverage-gated report against a target server (default: live Flower Shop, which the
01-era engine checks were authored against — hardcoded flower catalog ids).

Core checks live in v2026_01_23.py; the 01-era area modules are loaded STRICTLY against
`area_manifest_01_23.json` (checkset_manifest.load_area_checks) — a missing / unimportable
/ count-drifted module reds the gate rather than silently vanishing (P0-3 class). The
04-08 fixture modules (`area_04_08_*`) are excluded — they belong to suite-04-08.

This file IS the suite-01-23 gate: main() returns verdict_exit(...) — non-zero on any
unsound check, MUST deviation, rogue (non-allowlisted) version-skip, or a vacuous run —
so a red result is actually reachable (before P0-2 it always returned 0). Each check is
self-validated (kill-rate) by the engine before it can contribute to a verdict.
"""
import sys, json, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v2026_01_23 as core           # noqa: E402
from engine import run_report        # noqa: E402
from checkset_manifest import load_area_checks, load_manifest, AreaManifestError  # noqa: E402
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))
from verdict_gate import INCONCLUSIVE  # noqa: E402

MANIFEST = HERE / "area_manifest_01_23.json"
SKIP_ALLOWLIST = HERE / "expected_version_skips_01_23.json"

def collect():
    """Load core + every manifest-listed 01-era area module, STRICTLY. Raises
    AreaManifestError (main() turns that into a red gate) if any module is missing,
    unimportable, or count-drifted — a broken area module must never silently vanish
    from the run while the gate stays green (P0-3 class).

    The 04-08 fixture modules (area_04_08_*) are EXCLUDED: they belong to suite-04-08,
    need the schema oracle, and would both pollute this report and red it from the leak
    when the oracle is unavailable (they'd be INCONCLUSIVE -> not kill-safe)."""
    return load_area_checks(HERE, "area_*.py", load_manifest(MANIFEST), core.CHECKS,
                            exclude_prefixes=("area_04_08_",))

def _allowed_skips():
    return set(json.load(open(SKIP_ALLOWLIST))["allowed_version_skips"])

def verdict_exit(details, rep, allowed_skips=frozenset()):
    """The suite-01-23 exit code (run_suite.py maps it to PASS/FAIL). Mirrors
    run_04_08.py's semantics — this IS the gate, so it must be able to go red:

      1 = UNSOUND/RED. Any executed check that is not kill-safe (kill_safe is False):
          a real MUST deviation (clean=DEVIATION), a surviving mutant (a check that
          can false-PASS), or a transient error — none may ship as green. `rep`'s
          own aggregate=="fail" is honoured as a belt-and-suspenders on deviations.
          ALSO red on vacuity: an EMPTY executed set (every check skipped -> nothing
          actually ran -> fail closed), or a version-skip OUTSIDE the reviewed
          allowlist (skip-inflation must not silently hollow the gate).
      2 = honest SKIP. The target was unreachable, so EVERY executed check is
          inconclusive (the run said nothing about the server) — mirrors run_04_08's
          oracle-unavailable skip; the runner gates this on golden availability too.
      0 = GREEN. Every executed check is a kill-safe clean-pass, at least one check
          ran, and every skip is an allowlisted, expected one.

    Version-skipped checks (kill_safe is None: citations scoped to a version the
    target does not speak) are out of scope for kill-safety — never counted toward red
    OR toward the executed set. `is False` (not `not kill_safe`) is load-bearing: it
    excludes None. But an UNEXPECTED skip (id not in `allowed_skips`) is itself red.
    """
    executed = [d for _, d in details if not d.get("version_skip")]
    unsound = [c.id for c, d in details if d["kill_safe"] is False]
    rogue_skips = [c.id for c, d in details
                   if d.get("version_skip") and c.id not in allowed_skips]

    # honest skip: nothing reachable (every executed check inconclusive). Checked
    # before the empty-executed fail-closed so a down target skips, not falsely reds.
    if executed and all(d.get("clean") == INCONCLUSIVE for d in executed):
        return 2
    if not executed:
        return 1                           # vacuous: no check ran -> fail closed
    if unsound or rogue_skips or rep.aggregate == "fail":
        return 1
    return 0


def main(base="http://localhost:8182"):
    try:
        checks = collect()
    except AreaManifestError as e:
        # A dropped/broken/drifted 01-era area module is an integrity failure, not a
        # skip: its checks would silently stop running while coverage still claims them.
        print(f"AREA MANIFEST — gate RED: {e}")
        return 1
    rep, details = run_report(checks, base, "2026-01-23", core.SCOPE_STAMP, core.DISCLAIMER)
    print(f"target: {base}   checks: {len(checks)}\n")
    # kill_safe is None for version-scoped checks the served-version gate skipped:
    # they did not run, so they are out of scope on this server — not unsound.
    unsafe = [c.id for c, d in details if d["kill_safe"] is False]
    vskipped = [(c, d) for c, d in details if d.get("version_skip")]
    for c, d in details:
        print(f"  {c.id:34} {str(d['clean']):11} {d['kills']:6} kill_safe={d['kill_safe']}"
              + (f"  survivors={d['survivors']}" if d.get("survivors") else ""))
    cc = rep.counts
    print(f"\n=== REPORT (2026-01-23 @ {core.SCOPE_STAMP['spec_commit'][:7]}) ===")
    print(f"aggregate: {rep.aggregate.upper()}   "
          f"MUST coverage: {cc['musts_clean_pass']}/{cc['inscope_musts']} "
          f"({round(100*rep.coverage)}%)   deviations: {cc['deviations']}")
    allowed = _allowed_skips()
    if vskipped:
        rogue = [c.id for c, _ in vskipped if c.id not in allowed]
        print(f"version-scoped (not run against this server): "
              f"{[c.id for c, _ in vskipped]}")
        for c, d in vskipped:
            flag = "" if c.id in allowed else "  <-- NOT in the version-skip allowlist"
            print(f"    {c.id:34} {d.get('reason')}{flag}")
        if rogue:
            print(f"ROGUE version-skips (not allowlisted -> RED): {rogue}")
    if unsafe:
        print(f"UNSAFE checks (excluded from green): {unsafe}")
    rc = verdict_exit(details, rep, allowed)
    if rc == 1:
        print("\nSUITE-01-23: RED — unsound check(s), MUST deviation, rogue version-skip, "
              "or vacuous run (nothing executed). See details above.")
    elif rc == 2:
        print("\nSUITE-01-23: SKIP — target unreachable (every check inconclusive).")
    return rc

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
