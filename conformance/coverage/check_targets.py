#!/usr/bin/env python3
"""
check_targets.py — the wave target gate (PLAN-v3 §2.3, D1-14).

A wave commits to a LIST of register ids it will convert to CHECK at a spec version;
the list is data (coverage/wave<N>_targets_<ver>.json), clocked and pinned:

  {wave, version, spec_pin, review_by, clock_tier: pin-only, ids: [...],
   blocked: {id: "<task-id>: reason"}, owner_tasks: [...]}

Gate: every id in `ids` is CHECK at `version` in the export (a fresh matrix export by
default, `--export FILE` for a committed/scratch one) — an id that is not CHECK is a `gap`
finding UNLESS `blocked` names it with a task id (D<lane>-<n>[a-z]) and `--allow-blocked`
is given (mid-wave: run_suite passes it; at wave close the sheet runs WITHOUT it, so
`blocked` must be empty). A `blocked` value that is not "<task-id>: reason", a blocked id
that is not in `ids`, an expired review_by or a spec_pin that drifted from
SOURCES.lock.json is red too. Wave files are registered in expiry_registers.json.

    python3 conformance/coverage/check_targets.py conformance/coverage/wave1_targets_0825.json
    python3 conformance/coverage/check_targets.py --allow-blocked conformance/coverage/wave*_targets_*.json
    python3 conformance/coverage/check_targets.py --selftest
Exit 0 = every listed id CHECK (blocked empty, or allowed); 1 = a finding (named); 2 = usage.
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.dirname(HERE)
ROOT = os.path.dirname(CONF)
sys.path.insert(0, HERE)
LOCK = os.path.join(CONF, "SOURCES.lock.json")
TASK_ID = re.compile(r"^D\d-\d+[a-z]?: .+")


def selftest():
    bad = 0

    def case(label, ok, detail=""):
        nonlocal bad
        print(f"  {'✓' if ok else '✗'} {label}" + ("" if ok else f"  <-- {detail}"))
        bad += 0 if ok else 1

    export = {"versions": {"2026-08-25": {"rows": [
        {"id": "AAA-001", "status": "check"}, {"id": "AAA-002", "status": "check"},
        {"id": "AAA-003", "status": "gap"}, {"id": "AAA-004", "status": "exempt"}]}}}
    pins = {"2026-08-25": "cd78fb38"}
    today = date(2026, 9, 11)
    base = {"wave": 1, "version": "2026-08-25", "spec_pin": "cd78fb38", "review_by": "2026-12-01",
            "clock_tier": "pin-only", "ids": ["AAA-001", "AAA-002"], "blocked": {}, "owner_tasks": ["D1-15"]}
    try:
        ev = evaluate
    except NameError as e:
        for label in ("(a) all listed ids CHECK -> rc 0", "(b) one listed id gap -> rc 1 naming it",
                      "(c) blocked non-empty without --allow-blocked -> rc 1",
                      "(c') blocked + --allow-blocked -> rc 0, excused", "(d) blocked value not a task id -> rc 1",
                      "(e) review_by past -> rc 1", "(f) spec_pin drift -> rc 1", "(g) blocked id not in ids -> rc 1"):
            case(label, False, f"no script ({e})")
        ev = None
    if ev is not None:
        rc, lines, st = ev(base, export, pins, today, allow_blocked=False, name="w.json")
        case("(a) all listed ids CHECK -> rc 0", rc == 0 and st["check"] == 2 and st["blocked"] == 0, (rc, lines, st))
        rc, lines, st = ev({**base, "ids": ["AAA-001", "AAA-003"]}, export, pins, today, allow_blocked=False, name="w.json")
        case("(b) one listed id gap -> rc 1 naming it", rc == 1 and any("AAA-003" in l and "gap" in l for l in lines), (rc, lines))
        bl = {**base, "ids": ["AAA-001", "AAA-003"], "blocked": {"AAA-003": "D3-28: golden lacks the seam"}}
        rc, lines, st = ev(bl, export, pins, today, allow_blocked=False, name="w.json")
        case("(c) blocked non-empty without --allow-blocked -> rc 1", rc == 1 and any("blocked" in l for l in lines), (rc, lines))
        rc, lines, st = ev(bl, export, pins, today, allow_blocked=True, name="w.json")
        case("(c') blocked + --allow-blocked -> rc 0, excused", rc == 0 and st["blocked"] == 1 and st["check"] == 1, (rc, lines, st))
        rc, lines, st = ev({**bl, "blocked": {"AAA-003": "someone will fix it"}}, export, pins, today, allow_blocked=True, name="w.json")
        case("(d) blocked value not a task id -> rc 1", rc == 1 and any("task id" in l for l in lines), (rc, lines))
        rc, lines, st = ev({**base, "review_by": "2026-09-01"}, export, pins, today, allow_blocked=False, name="w.json")
        case("(e) review_by past -> rc 1", rc == 1 and any("expired" in l for l in lines), (rc, lines))
        rc, lines, st = ev({**base, "spec_pin": "deadbeef"}, export, pins, today, allow_blocked=False, name="w.json")
        case("(f) spec_pin drift -> rc 1", rc == 1 and any("spec_pin" in l for l in lines), (rc, lines))
        rc, lines, st = ev({**base, "blocked": {"AAA-009": "D1-15: not listed"}}, export, pins, today, allow_blocked=True, name="w.json")
        case("(g) blocked id not in ids -> rc 1", rc == 1 and any("AAA-009" in l for l in lines), (rc, lines))
        # (h) an exempt id is not CHECK: the wave promised a check, not an exemption
        rc, lines, st = ev({**base, "ids": ["AAA-004"]}, export, pins, today, allow_blocked=False, name="w.json")
        case("(h) exempt listed id -> rc 1 (exempt is not CHECK)", rc == 1, (rc, lines))
    # (i) every committed wave file is a clocked register entry
    reg = json.load(open(os.path.join(HERE, "expiry_registers.json")))
    registered = {r["file"] for r in reg["registers"]}
    files = sorted(glob.glob(os.path.join(HERE, "wave*_targets_*.json")))
    rels = [os.path.relpath(f, ROOT) for f in files]
    missing = [r for r in rels if r not in registered]
    case("(i) every coverage/wave*_targets_*.json registered in expiry_registers.json",
         bool(files) and not missing, f"files={rels} missing={missing}")
    print(f"\nwave-targets selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="wave target gate (D1-14)")
    ap.add_argument("files", nargs="*", help="wave target files")
    ap.add_argument("--export", help="coverage export JSON (default: a fresh matrix export)")
    ap.add_argument("--allow-blocked", action="store_true", help="mid-wave: excuse ids named in `blocked`")
    ap.add_argument("--today", help="ISO date override")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.files:
        ap.print_usage(); return 2
    try:
        return run(a.files, a.export, a.allow_blocked, a.today)
    except NameError as e:
        print(f"wave-targets: FAIL — no gate yet ({e})"); return 1


if __name__ == "__main__":
    sys.exit(main())
