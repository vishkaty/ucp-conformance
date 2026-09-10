#!/usr/bin/env python3
"""
validate_battery_freshness.py — the golden-0825 mutant battery (R11) must have run,
recently, and passed — as a COUNTED gate, not a report-only line (PLAN-v3 D1-09).

Two sources, in order of preference:
  1. in-run  — the battery run of THIS run_suite invocation (`run_suite.py --battery`
               copies the battery's report into the run-scoped record dir); CI always
               uses this, so an idle fortnight can never red an unrelated push (RV2 F9)
  2. tracked — conformance/testbed/golden-0825/battery/LAST_RUN.json, committed by the
               owner on the release path (decision 24: artifacts + in-run freshness, no
               bot commits); the 14-day rule applies here

Red when: the chosen report is missing, `ok` is false, or `ran_at` is older than
STALE_DAYS; green prints `N/N killed · A acknowledged · ran YYYY-MM-DD`.

    python3 conformance/selfcheck/validate_battery_freshness.py [--in-run FILE] [--file FILE]
    python3 conformance/selfcheck/validate_battery_freshness.py --selftest
"""
import argparse
import json
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TRACKED = ROOT / "conformance" / "testbed" / "golden-0825" / "battery" / "LAST_RUN.json"
STALE_DAYS = 14


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    fn = globals().get("assess")
    if fn is None:
        print("  ✗ assess() — no gate exists yet")
        print("battery-freshness selftest: FAIL (no gate)")
        return 1

    now = 1_800_000_000.0
    fresh = {"ran_at": now - 3600, "ok": True, "killed": 38, "total": 38, "acknowledged_open": 0}
    stale = {**fresh, "ran_at": now - (STALE_DAYS + 1) * 86400}
    failed = {**fresh, "ok": False, "killed": 37, "survivors": ["x"]}

    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        def w(name, doc):
            p = d / name
            if doc is not None:
                p.write_text(json.dumps(doc))
            return p
        rc, msg = fn(in_run=None, tracked=w("fresh.json", fresh), now=now)
        check("fresh tracked report -> green", rc == 0 and "38/38 killed" in msg and "ran 2027-01-15" in msg, msg)
        rc, msg = fn(in_run=None, tracked=w("stale.json", stale), now=now)
        check("backdated ran_at -> red", rc == 1 and "stale" in msg.lower(), msg)
        rc, msg = fn(in_run=None, tracked=w("failed.json", failed), now=now)
        check("ok:false -> red", rc == 1 and "ok" in msg.lower(), msg)
        rc, msg = fn(in_run=None, tracked=d / "missing.json", now=now)
        check("missing -> red", rc == 1 and "missing" in msg.lower(), msg)
        rc, msg = fn(in_run=w("inrun_fresh.json", fresh), tracked=w("t_stale.json", stale), now=now)
        check("in-run fresh beats a stale tracked file -> green", rc == 0 and "in-run" in msg, msg)
        rc, msg = fn(in_run=w("inrun_stale.json", stale), tracked=w("t_fresh.json", fresh), now=now)
        check("in-run stale is NOT rescued by a fresh tracked file -> red", rc == 1, msg)
        rc, msg = fn(in_run=d / "no_inrun.json", tracked=w("t_fresh2.json", fresh), now=now)
        check("absent in-run file falls back to tracked", rc == 0 and "tracked" in msg, msg)
        rc, msg = fn(in_run=None, tracked=w("garbage.json", None), now=now)
        (d / "garbage.json").write_text("{not json")
        rc, msg = fn(in_run=None, tracked=d / "garbage.json", now=now)
        check("unreadable -> red", rc == 1, msg)

    print(f"battery-freshness selftest: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description="R11 battery freshness gate.")
    ap.add_argument("--in-run", help="battery report produced in THIS run (preferred when present)")
    ap.add_argument("--file", default=str(TRACKED), help="tracked battery report (fallback)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    fn = globals().get("assess")
    if fn is None:
        print("battery-freshness: FAIL — no gate implemented"); return 1
    rc, msg = fn(in_run=pathlib.Path(args.in_run) if args.in_run else None,
                 tracked=pathlib.Path(args.file), now=time.time())
    print(f"battery-freshness: {'PASS' if rc == 0 else 'FAIL'} — {msg}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
