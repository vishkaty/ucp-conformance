#!/usr/bin/env python3
"""
run_01_23.py — aggregate all 2026-01-23 check modules and produce the honest,
coverage-gated report against a target server (default: live Flower Shop).

Core checks live in v2026_01_23.py; additional areas are auto-loaded from any
`area_*.py` module in this directory that exports a CHECKS list. Each check is
self-validated (kill-rate) by the engine before it can contribute to a verdict.
"""
import sys, pathlib, importlib, glob
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import v2026_01_23 as core           # noqa: E402
from engine import run_report        # noqa: E402

def collect():
    checks = list(core.CHECKS)
    for f in sorted(glob.glob(str(pathlib.Path(__file__).resolve().parent / "area_*.py"))):
        name = pathlib.Path(f).stem
        try:
            m = importlib.import_module(name)
            checks += list(getattr(m, "CHECKS", []))
        except Exception as e:
            print(f"(area {name} not loaded: {e})", file=sys.stderr)
    return checks

def main(base="http://localhost:8182"):
    checks = collect()
    rep, details = run_report(checks, base, "2026-01-23", core.SCOPE_STAMP, core.DISCLAIMER)
    print(f"target: {base}   checks: {len(checks)}\n")
    unsafe = [c.id for c, d in details if not d["kill_safe"]]
    for c, d in details:
        print(f"  {c.id:34} {str(d['clean']):11} {d['kills']:6} kill_safe={d['kill_safe']}"
              + (f"  survivors={d['survivors']}" if d.get("survivors") else ""))
    cc = rep.counts
    print(f"\n=== REPORT (2026-01-23 @ {core.SCOPE_STAMP['spec_commit'][:7]}) ===")
    print(f"aggregate: {rep.aggregate.upper()}   "
          f"MUST coverage: {cc['musts_clean_pass']}/{cc['inscope_musts']} "
          f"({round(100*rep.coverage)}%)   deviations: {cc['deviations']}")
    if unsafe:
        print(f"UNSAFE checks (excluded from green): {unsafe}")
    return 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
