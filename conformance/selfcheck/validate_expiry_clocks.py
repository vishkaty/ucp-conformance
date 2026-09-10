#!/usr/bin/env python3
"""
validate_expiry_clocks.py — the expiry-clock gate (P-2 / PLAN-v3 §2.4, task D2-04).

Every entry of every register listed in conformance/coverage/expiry_registers.json
(exemptions, agent exemptions, completeness waivers and scope exclusions, schema-census
and fenced-hit rulings, agent-lane overrides, site claims; retirements carry
`clock: none`) must carry TWO clock hands:
  review_by  — an ISO date; FAIL when it is in the past (the entry's truth was reviewed
               for a horizon and the horizon ended), WARN inside the last 14 days;
  spec_pin   — the 8-hex commit of the pinned spec the entry was reviewed against (a
               `{version: pin}` map when the entry spans versions with different pins);
               FAIL on drift from SOURCES.lock.json (a re-pin invalidates every review
               made against the old pin, mechanically — nobody has to remember).
Missing either hand is `missing-clock`. Findings name the entry as `<file> <path>`.

Usage:
  python3 conformance/selfcheck/validate_expiry_clocks.py            # the gate
  ... --today 2027-01-01                                            # clock override
  ... --lock PATH                                                   # alternate lock (drift proof)
  ... --selftest                                                    # hermetic kill-tests
"""
import argparse, json, os, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONF = os.path.join(ROOT, "conformance")
sys.path.insert(0, CONF)
sys.path.insert(0, os.path.join(CONF, "coverage"))
from common.spec_versions import VERSIONS  # noqa: E402

REGISTERS = os.path.join(CONF, "coverage", "expiry_registers.json")
LOCK = os.path.join(CONF, "SOURCES.lock.json")
WARN_DAYS = 14


def selftest():
    """Kill-tests (hermetic: synthetic registers/docs/lock, no repo I/O)."""
    today = date(2026, 9, 10)
    pins = {"2026-04-08": "a2d8bf0b", "2026-08-25": "cd78fb38"}
    regs = [{"name": "waivers", "file": "w.json", "entries": "$.waivers[*]",
             "scope": "version", "clock": "review_by"},
            {"name": "retirements", "file": "r.json", "entries": "$.retirements[*]",
             "scope": "versions", "clock": "none"}]
    docs = {"w.json": {"waivers": [
                {"version": "2026-08-25", "review_by": "2026-09-09", "spec_pin": "cd78fb38"},   # (a)
                {"version": "2026-04-08", "review_by": "2026-12-01", "spec_pin": "deadbeef"},   # (b)
                {"version": "2026-08-25"},                                                      # (c)
                {"version": "2026-08-25", "review_by": "2026-12-01", "spec_pin": "cd78fb38"}]}, # clean
            "r.json": {"retirements": [{"versions": ["2026-04-08"]}]}}                          # (d)
    bad = 0

    def case(label, ok, detail=""):
        nonlocal bad
        print(f"  {'✓' if ok else '✗'} {label}" + ("" if ok else f"  <-- {detail}"))
        bad += 0 if ok else 1

    f = evaluate(regs, docs.get, pins, today)
    by = {}
    for x in f:
        by.setdefault(x["entry"], []).append(x["kind"])
    case("(a) review_by yesterday -> expired", by.get("w.json waivers[0]") == ["expired"], by)
    case("(b) spec_pin deadbeef -> pin-drift", by.get("w.json waivers[1]") == ["pin-drift"], by)
    case("(c) no clock hands -> missing-clock", by.get("w.json waivers[2]") == ["missing-clock"], by)
    case("(d) clock: none register -> no finding", "r.json retirements[0]" not in by, by)
    case("clean entry -> no finding", "w.json waivers[3]" not in by, by)
    f2 = evaluate(regs, docs.get, pins, date(2026, 1, 1))
    case("(e) --today override: 2026-01-01 sees no expiry",
         not any(x["kind"] == "expired" for x in f2), [x["kind"] for x in f2])
    # (f) the seed batch is a sign-off WITH a recorded >=10% sample, or it is invalid
    from verify_review_signoffs import seed_batch_errors
    seed_ok = {"batch": "expiry-clock-seed-2026-09", "date": "2026-09-10", "reviewer": "x",
               "spec_reverified": True, "notes": "n" * 40,
               "sample": {"seed": 1, "of": 30, "size": 3, "ids": ["a", "b", "c"],
                          "human_review": {"status": "recorded", "by": "owner", "on": "2026-09-10"}}}
    seed_none = {k: v for k, v in seed_ok.items() if k != "sample"}
    seed_thin = {**seed_ok, "sample": {**seed_ok["sample"], "size": 2, "ids": ["a", "b"]}}
    case("(f) seed batch without a sample -> red", bool(seed_batch_errors(seed_none, today)))
    case("(f') seed batch sample < 10% -> red", bool(seed_batch_errors(seed_thin, today)))
    case("(f'') seed batch with a recorded >=10% sample -> clean",
         not seed_batch_errors(seed_ok, today), seed_batch_errors(seed_ok, today))

    print(f"\nexpiry-clocks selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main())
