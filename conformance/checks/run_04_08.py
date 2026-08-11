#!/usr/bin/env python3
"""
run_04_08.py — aggregate 2026-04-08 fixture-based check modules and produce the
honest, coverage-gated report. Core in v2026_04_08.py; areas auto-loaded from
area_04_08_*.py. These checks need no server (they validate synthetic fixtures via
the schema oracle); the schema oracle requires the built ucp-schema binary.

Exit codes (this IS the suite-04-08 gate; matrix.py attributes fixture_check ids
on the strength of it): 0 = every check clean-pass AND kill-safe; 1 = any check
unsound (deviating fixture or surviving mutant — our bug, must not ship);
2 = schema oracle unavailable (honest skip, mirrors the schema/fixture gates).
"""
import sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))
import v2026_04_08 as core            # noqa: E402
from engine import run_report         # noqa: E402
from verdict_gate import INCONCLUSIVE  # noqa: E402
from checkset_manifest import load_area_checks, load_manifest, AreaManifestError  # noqa: E402

MANIFEST = HERE / "area_manifest_04_08.json"

def collect():
    """Load core + every manifest-listed area_04_08_* module, STRICTLY. Raises
    AreaManifestError (main() turns that into a red gate) if any module is missing,
    unimportable, or count-drifted — a broken area module must never silently vanish
    from the run while the gate stays green (P0-3)."""
    return load_area_checks(HERE, "area_04_08_*.py", load_manifest(MANIFEST), core.CHECKS)

def main():
    try:
        checks = collect()
    except AreaManifestError as e:
        # A dropped/broken/drifted area module is an integrity failure, not a skip:
        # its checks would silently stop running while the matrix still claims coverage.
        print(f"AREA MANIFEST — gate RED: {e}")
        return 1
    rep, details = run_report(checks, "fixtures://", "2026-04-08",
                              core.SCOPE_STAMP, core.DISCLAIMER)
    print(f"fixture-based checks: {len(checks)}\n")
    for c, d in details:
        print(f"  {c.id:38} {str(d['clean']):11} {d['kills']:6} kill_safe={d['kill_safe']}"
              + (f"  survivors={d['survivors']}" if d.get("survivors") else ""))
    cc = rep.counts
    print(f"\n=== REPORT (2026-04-08 @ {core.SCOPE_STAMP['spec_commit'][:7]}, synthetic) ===")
    print(f"aggregate: {rep.aggregate.upper()}   "
          f"MUST coverage: {cc['musts_clean_pass']}/{cc['inscope_musts']} "
          f"({round(100*rep.coverage)}%)   deviations: {cc['deviations']}")
    unsafe = [c.id for c, d in details if not d["kill_safe"]]
    if unsafe:
        # every check INCONCLUSIVE = the oracle never answered -> skip, not red
        if all(d["clean"] == INCONCLUSIVE for _, d in details):
            print("schema oracle unavailable — skip")
            return 2
        print(f"UNSOUND — gate RED: {unsafe}")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
