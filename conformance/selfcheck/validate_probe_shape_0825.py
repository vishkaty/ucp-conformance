#!/usr/bin/env python3
"""
validate_probe_shape_0825.py — gate `probe-shape-0825`: the merchant CLI, pointed at
golden-0825 with REF_CONFIG, must report ZERO deviations in BOTH probe modes, and must
actually have run checks (PLAN-v3 D1-04).

Why: before wire_shapes.py (D1-01) the CLI sent a 2026-04-08 `destinations[]` to the
2026-08-25 golden and reported 8 false deviations about a conformant server. This gate
is the kill-proof for that delta: revert it and the default mode goes red. The second
mode, `--omit-destination-type`, sends destinations WITHOUT `type` — the platform is
allowed to (fulfillment_destination.json `ucp_request: optional`) and the golden must
default it per method (C3b, D3-04; mutant `destination_type_required_on_request`).
Both modes must show 0 deviations, an aggregate that is not `fail`, and >= MIN_RUN
checks run (a run that exercised nothing proves nothing — vacuity is red).

    python3 conformance/selfcheck/validate_probe_shape_0825.py --server http://localhost:8197
    python3 conformance/selfcheck/validate_probe_shape_0825.py --selftest
Exit 0 = both modes clean; 1 = deviations / vacuous / aggregate fail; 2 = golden down.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "checks"))
MIN_RUN = 29


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    fn = globals().get("assess")
    if fn is None:
        print("  ✗ assess() — script absent")
        print("probe-shape-0825 selftest: FAIL (script absent)")
        return 1

    def doc(deviations, run, aggregate=None):
        agg = aggregate or ("fail" if deviations else "incomplete")
        checks = [{"id": f"c{i}", "status": "deviation" if i < deviations else "clean-pass"} for i in range(run)]
        return {"verdict": {"aggregate": agg, "deviations": deviations}, "checks": checks,
                "checks_summary": {"run": run, "deviation": deviations, "clean": run - deviations}}

    rc, msg = fn(doc(7, 29), doc(0, 29))
    check("deviations 7 (default mode) -> rc 1", rc == 1 and "7 deviations" in msg, msg)
    rc, msg = fn(doc(0, 29), doc(0, 29))
    check("deviations 0, run 29 (both modes) -> rc 0", rc == 0, msg)
    check("summary names both modes",
          "0 deviations" in msg and "29 checks run" in msg and "omit-type 0 deviations" in msg, msg)
    rc, msg = fn(doc(0, 0), doc(0, 29))
    check("run 0 -> rc 1 (vacuity)", rc == 1 and "vacu" in msg.lower(), msg)
    rc, msg = fn(doc(0, 29), doc(3, 29))
    check("omit-mode deviations 3 -> rc 1", rc == 1 and "omit-type 3 deviations" in msg, msg)
    rc, msg = fn(doc(0, 29, aggregate="fail"), doc(0, 29))
    check("aggregate fail with 0 deviations -> rc 1", rc == 1, msg)
    rc, msg = fn(doc(0, 28), doc(0, 29))
    check("run below MIN_RUN -> rc 1", rc == 1, msg)
    rc, msg = fn(None, doc(0, 29))
    check("unparseable CLI output -> rc 1", rc == 1, msg)

    print(f"probe-shape-0825 selftest: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description="probe-shape-0825 gate.")
    ap.add_argument("--server", default="http://localhost:8197")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    fn = globals().get("run")
    if fn is None:
        print("probe-shape-0825: FAIL — script absent"); return 1
    return fn(args.server)


if __name__ == "__main__":
    sys.exit(main())
