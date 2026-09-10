#!/usr/bin/env python3
"""
validate_dormancy.py — every merchant check must run somewhere, or be named (PLAN-v3
§2.2, D1-07).

A check that no golden ever exercises is kill-tested nowhere: its predicate could be
inverted and every gate would stay green (L5 F8: M8/R5 survived because DSC-020/022 are
"skipped on reference"). D1 N6 measured the union of the four merchant gates (flower
:8182, controlled 04-08 :8184, controlled 01-23 :8185, controlled 01-11 :8193): 13 check
ids are DORMANT — never run on any golden. This gate pins that number and its
explanation:

  dormant  = all_checks() ids  −  ∪ ran(record)   over the four run records that
             validate_merchant_checks.py --record writes
  each dormant id is either exempt-by-gate (dormancy_exemptions.json names the run_suite
  gate that kill-tests it by id, with a review_by) or listed under never_kill_tested
  (an honest, visible debt until a gate names it) — anything else is UNEXPLAINED (red)
  floor   = the exact expected count; above it red (a new dormant check), below it red
            too (lower the floor deliberately — the number is reviewed, never drifting)
  partial union: a missing record (a SKIPPED merchant gate) cannot shrink the union —
            fewer than the expected records is red, never a smaller dormant set (RV2 T14)
  under --require-server a missing/partial record set FAILs (rc 1), never SKIPs (RV2 V6)

    python3 conformance/selfcheck/validate_dormancy.py --records DIR [--require-server]
    python3 conformance/selfcheck/validate_dormancy.py --selftest
Exit 0 = dormant == floor, all explained; 1 = red (named); 2 = records absent and
--require-server not given (honest skip: no goldens were up).
"""
import argparse
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "checks"))

EXEMPTIONS = HERE / "dormancy_exemptions.json"
EXPECTED_RECORDS = ("flower", "controlled-04-08", "controlled-01-23", "controlled-01-11")


# ---------------------------------------------------------------------------
# --selftest: canned records + scratch registry; each rule carries its mutant
# ---------------------------------------------------------------------------
def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    fn = globals().get("compute")
    if fn is None:
        print("  ✗ compute() — no validator exists yet")
        print("dormancy selftest: FAIL (no script)")
        return 1

    ids = ["a.one", "a.two", "b.three", "c.four", "d.five"]
    records = {
        "flower": {"golden": "flower", "served_version": "2026-04-08", "ran": ["a.one"], "skipped": {}},
        "controlled-04-08": {"golden": "controlled", "served_version": "2026-04-08", "ran": ["a.two"], "skipped": {}},
        "controlled-01-23": {"golden": "controlled", "served_version": "2026-01-23", "ran": ["b.three"], "skipped": {}},
        "controlled-01-11": {"golden": "controlled", "served_version": "2026-01-11", "ran": [], "skipped": {}},
    }
    exemptions = {"floor": 2,
                  "exemptions": {"c.four": {"gate": "tls-check", "review_by": "2099-01-01"}},
                  "never_kill_tested": ["d.five"]}
    gates = {"tls-check", "disc014-check", "verdict"}
    today = "2026-09-10"

    rc, lines = fn(ids, records, exemptions, gates, today, expected=EXPECTED_RECORDS)
    check("expected dormant set -> green", rc == 0, "\n".join(lines))
    check("summary line", lines[-1] == "dormant: 2 (floor 2) · exempt-by-gate: 1 · never-kill-tested: 1 listed", lines[-1])

    # planted cfg_needs=("__never__",) check: one more id nobody runs -> floor+1 -> red
    rc, lines = fn(ids + ["planted.never"], records, exemptions, gates, today, expected=EXPECTED_RECORDS)
    check("planted never-run check -> red (above floor, unexplained)",
          rc == 1 and any("planted.never" in l for l in lines) and any("floor" in l for l in lines), "\n".join(lines))

    # exemption naming a gate that is not in run_suite's table -> red
    bad = json.loads(json.dumps(exemptions)); bad["exemptions"]["c.four"]["gate"] = "no-such-gate"
    rc, lines = fn(ids, records, bad, gates, today, expected=EXPECTED_RECORDS)
    check("exemption naming an unknown gate -> red", rc == 1 and any("no-such-gate" in l for l in lines), "\n".join(lines))

    # backdated review_by -> red
    bad = json.loads(json.dumps(exemptions)); bad["exemptions"]["c.four"]["review_by"] = "2026-01-01"
    rc, lines = fn(ids, records, bad, gates, today, expected=EXPECTED_RECORDS)
    check("backdated review_by -> red", rc == 1 and any("expired" in l for l in lines), "\n".join(lines))

    # one of four records missing -> red "partial union" (RV2 T14) — even though the
    # dormant set computed from three records would look LARGER, never smaller
    three = {k: v for k, v in records.items() if k != "controlled-01-11"}
    rc, lines = fn(ids, three, exemptions, gates, today, expected=EXPECTED_RECORDS)
    check("one of four records missing -> red 'partial union'",
          rc == 1 and any("partial union" in l and "controlled-01-11" in l for l in lines), "\n".join(lines))

    # a never_kill_tested id that actually ran -> red (stale entry)
    ran = json.loads(json.dumps(records)); ran["flower"]["ran"].append("d.five")
    rc, lines = fn(ids, ran, exemptions, gates, today, expected=EXPECTED_RECORDS)
    check("never_kill_tested id that ran -> red (stale; below floor)", rc == 1, "\n".join(lines))

    # dormant below floor -> red: lower the floor deliberately
    rc, lines = fn(ids, ran, {**exemptions, "never_kill_tested": []}, gates, today, expected=EXPECTED_RECORDS)
    check("dormant below floor -> red 'lower the floor deliberately'",
          rc == 1 and any("below" in l and "floor" in l for l in lines), "\n".join(lines))

    # a record that names an id absent from the registry -> red (record from another tree)
    alien = json.loads(json.dumps(records)); alien["flower"]["ran"].append("ghost.id")
    rc, lines = fn(ids, alien, exemptions, gates, today, expected=EXPECTED_RECORDS)
    check("record naming an unknown id -> red", rc == 1 and any("ghost.id" in l for l in lines), "\n".join(lines))

    # the on-disk path: load_records from a dir with three files -> partial; --require-server semantics
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        for k, v in three.items():
            (d / f"{k}.json").write_text(json.dumps(v))
        loaded = globals()["load_records"](d)
        check("load_records reads <name>.json files", set(loaded) == set(three), f"{sorted(loaded)}")
        rc, lines = fn(ids, loaded, exemptions, gates, today, expected=EXPECTED_RECORDS)
        check("partial dir -> red", rc == 1)
        check("empty dir + no --require-server -> rc 2 (skip)",
              globals()["run"](pathlib.Path(tmp) / "none", require_server=False, ids=ids,
                               exemptions=exemptions, gates=gates, today=today) == 2)
        check("empty dir + --require-server -> rc 1 (FAIL, never SKIP)",
              globals()["run"](pathlib.Path(tmp) / "none", require_server=True, ids=ids,
                               exemptions=exemptions, gates=gates, today=today) == 1)

    print(f"dormancy selftest: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description="Dormancy gate: every merchant check runs somewhere or is named.")
    ap.add_argument("--records", help="directory of <name>.json run records from validate_merchant_checks --record")
    ap.add_argument("--require-server", action="store_true",
                    help="a missing/partial record set FAILs (rc 1) instead of skipping (rc 2)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.records:
        ap.error("--records DIR is required (or --selftest)")
    fn = globals().get("run")
    if fn is None:
        print("dormancy: FAIL — no validator implemented"); return 1
    return fn(pathlib.Path(args.records), require_server=args.require_server)


if __name__ == "__main__":
    sys.exit(main())
