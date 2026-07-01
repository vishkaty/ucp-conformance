#!/usr/bin/env python3
"""
run_04_08.py — aggregate 2026-04-08 fixture-based check modules and produce the
honest, coverage-gated report. Core in v2026_04_08.py; areas auto-loaded from
area_04_08_*.py. These checks need no server (they validate synthetic fixtures via
the schema oracle); the schema oracle requires the built ucp-schema binary.
"""
import sys, pathlib, importlib, glob
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import v2026_04_08 as core            # noqa: E402
from engine import run_report         # noqa: E402

def collect():
    checks = list(core.CHECKS)
    for f in sorted(glob.glob(str(HERE / "area_04_08_*.py"))):
        name = pathlib.Path(f).stem
        try:
            checks += list(getattr(importlib.import_module(name), "CHECKS", []))
        except Exception as e:
            print(f"(area {name} not loaded: {e})", file=sys.stderr)
    return checks

def main():
    checks = collect()
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
        print(f"UNSAFE (excluded): {unsafe}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
