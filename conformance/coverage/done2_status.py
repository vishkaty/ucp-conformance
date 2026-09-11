#!/usr/bin/env python3
"""
done2_status.py — the DONE-2 definition (PLAN-v3-FINAL §1, items 1-12) as ONE machine
report (D2-16). A program-review artefact, not a run_suite gate: it never fails the
build, it says — per item — whether the mechanical condition holds today and what the
evidence is. `collect()` gathers inputs (exports, gate outputs, registers); `evaluate()`
is pure over those inputs so the selftest can feed scratch data.

  python3 conformance/coverage/done2_status.py            # twelve lines: item N: PASS/FAIL — evidence
  python3 conformance/coverage/done2_status.py --json     # {item: {pass, evidence}}
  python3 conformance/coverage/done2_status.py --selftest # hermetic: scratch inputs flip items 2 and 8

Items (each is decision 16's addition when it goes beyond PLAN-0825 §G):
   1 census (prose gate mode + schema --enforce + surface published)
   2 per role / per transport (--require all --role merchant|agent, per bound transport, agent axis)
   3 evidence classes published (seven classes)
   4 kill discipline (per-id kills, killset lock 0 drift, dormancy floor)
   5 independent corroboration (dual-oracle-0825 + pydantic referee)
   6 agent lane (08-25 run evidence from a 2026-08-25 sandbox)
   7 ratchet + lock + state (both lanes at 08-25; converting state machine)
   8 records (expiry clocks 0/0/0; known issues valid)
   9 SHOULD scope (census feeds surface.should; site sentence)
  10 review (every sample batch's human sample recorded)
  11 converting state (08-25 `converting` with the open testable-tier count)
  12 attribution (hook gate green; filing_lint --branches 0 hits)
"""
import json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONF = ROOT / "conformance"
sys.path.insert(0, str(CONF))
sys.path.insert(0, str(CONF / "selfcheck"))

V = "2026-08-25"
ITEMS = 12


def selftest():
    """Scratch inputs: an export whose merchant lane has 3 GAPs -> item 2 false; a
    register with an expired review_by -> item 8 false; the same inputs with those two
    fixed -> items 2 and 8 true (so the function can pass, not only fail)."""
    import datetime
    bad = 0

    def case(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  <-- {detail}"))
        return 0 if ok else 1

    def scratch(merchant_gap, expired):
        export = {"versions": {V: {
            "state": "converting", "musts": 10, "check": 7, "exempt": 0, "gap": 3,
            "roles": {"summary": {"merchant": 8, "agent": 4, "both": 2, "other": 0},
                      "merchant": {"musts": 8, "check": 8 - merchant_gap, "exempt": 0, "gap": merchant_gap},
                      "agent": {"musts": 4, "check": 4, "exempt": 0, "gap": 0}},
            "by_transport": {"rest": {"musts": 10, "check": 7, "exempt": 0, "gap": 3}},
            "surface": {"prose": {"missed": 0, "census_mode": "gate"}, "schema": {"enforce": True, "atoms_unaccounted": 0},
                        "should": {"hits": 274, "scope": "report-only"}},
            "evidence_classes": ["live-wire", "fixture-schema", "fixture-crypto", "self-referenced",
                                 "register-selfcheck", "reference-impl", "discovery-live"],
            "gap_by_testability": {"testable": 3},
        }}}
        today = datetime.date(2026, 9, 11)
        rb = "2026-09-01" if expired else "2026-12-01"
        return {
            "export": export, "today": today,
            "agent_axis": {V: {"agent_musts": 4, "check": 4, "exempt": 0, "gap": 0}},
            "req_kills_rc": 0, "killset_rc": 0, "dormancy_rc": 0,
            "dual_oracle_rc": 0, "pydantic_rc": 0,
            "agent_evidence": {"unrun_versions": {}, "evidence": {"agent.x": {V: {"date": "2026-09-11", "spec_pin": "cd78fb38"}}}},
            "agent_checks_at_v": ["agent.x"], "spec_pin": "cd78fb38",
            "ratchet": {V: 7}, "agent_ratchet": {V: {"accounted": 4}},
            "coverage_lock": {V: {"check": ["A-1"], "exempt": []}}, "agent_coverage_lock": {V: {"check": ["B-1"]}},
            "expiry_findings": [{"kind": "expired", "entry": "scratch.json x[0]", "detail": f"review_by {rb}"}] if expired else [],
            "known_issues_rc": 0,
            "should": {V: {"hits": 274}}, "rubric_sentence": True,
            "signoffs": [{"batch": "s1", "kind": "sample", "sample": {"human_review": {"status": "recorded"}}}],
            "attribution_rc": 0, "filing_lint_rc": 0,
        }

    try:
        bad_in = evaluate(scratch(merchant_gap=3, expired=True))
        good_in = evaluate(scratch(merchant_gap=0, expired=False))
    except NameError as e:
        print(f"  ✗ evaluate absent: {e}")
        print("\ndone2-status selftest: FAIL (1 case(s))")
        return 1
    bad += case("scratch export with roles.merchant.gap 3 -> item 2 FAIL",
                bad_in[2]["pass"] is False and "3" in bad_in[2]["evidence"], repr(bad_in[2]))
    bad += case("scratch register with an expired review_by -> item 8 FAIL",
                bad_in[8]["pass"] is False and "expired" in bad_in[8]["evidence"], repr(bad_in[8]))
    bad += case("same inputs fixed -> items 2 and 8 PASS",
                good_in[2]["pass"] is True and good_in[8]["pass"] is True, repr((good_in[2], good_in[8])))
    bad += case("twelve items reported", sorted(good_in) == list(range(1, ITEMS + 1)), repr(sorted(good_in)))
    print(f"\ndone2-status selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))
