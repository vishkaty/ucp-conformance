#!/usr/bin/env python3
"""
validate_known_issues.py — the single KNOWN ISSUES file (conformance/ci/known_issues.json,
schema conformance/ci/known_issues.schema.json — D4's field set, PLAN-v3 §2.13, D5-07)
can never publish a refuted, stale, unevidenced or unanchored row:

  · `refuted` is a const false — a refuted finding (S8d) never renders anywhere
  · `re_verified` ≤ 30 days old, `review_by` ≤ 90 days ahead and unexpired
  · every `status_by_cut` entry with status `fixed` names its evidence (SHA/PR)
  · `repro.{command,expected,observed}` non-empty; symptom ≤ 300 chars; ids KI-###, unique
  · `ledger_row` exists in ops/GAP-LEDGER-0825.md when ops/ is mounted — else SKIP rc 2
    (never silently green)

  validate_known_issues.py            # real file → `known-issues: N rows · 0 stale · 0 refuted · ledger cross-ref OK`
  validate_known_issues.py --selftest # hermetic kill-tests (each planted defect must red)
Exit 0 pass · 1 fail · 2 honest skip (ops/ not mounted). Stdlib only; jsonschema used when importable.
"""
import copy, datetime, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
KI = ROOT / "conformance" / "ci" / "known_issues.json"
SCHEMA = ROOT / "conformance" / "ci" / "known_issues.schema.json"
LEDGER = ROOT / "ops" / "GAP-LEDGER-0825.md"
TODAY = datetime.date.today()


def selftest():
    bad = 0
    today = datetime.date(2026, 9, 10)
    seed = {"generated": "2026-09-10", "spec_pin": "cd78fb38e819de77d9b527d110476eccb876f1bd",
            "issues": [{
                "id": "KI-001", "artifact": "python-sdk", "component": "generated models",
                "symptom": "constraint families dropped", "repro": {"command": "python3 x.py", "expected": "enforced", "observed": "not enforced"},
                "status_by_cut": [{"cut": "PyPI 0.5.0", "status": "open"},
                                  {"cut": "main 836b0228", "status": "fixed", "evidence": "python-sdk#89 merged 836b0228"}],
                "origin": "theirs", "filed": ["https://github.com/Universal-Commerce-Protocol/python-sdk/pull/89"],
                "re_verified": "2026-09-09", "confidence": "high", "ledger_row": "G1",
                "review_by": "2026-12-08", "refuted": False}]}
    ledger_ids = {"G1", "R20"}

    def case(name, mutate, want_red, ledger=ledger_ids, must_name=None):
        nonlocal bad
        doc = copy.deepcopy(seed)
        mutate(doc)
        fails = validate_doc(doc, today, ledger)
        got_red = bool(fails)
        ok = got_red == want_red and (not must_name or any(must_name in f for f in fails))
        print(f"  {'✓' if ok else '✗'} {name}: {'RED' if got_red else 'GREEN'}"
              + ("" if ok else f"  <-- expected {'RED' if want_red else 'GREEN'}"
                               + (f" naming {must_name!r}" if must_name else ""))
              + (("\n      " + "\n      ".join(fails)) if fails and not ok else ""))
        bad += 0 if ok else 1

    case("honest seed row", lambda d: None, want_red=False)
    case("re_verified 40 days old (stale)", lambda d: d["issues"][0].__setitem__("re_verified", "2026-08-01"), True, must_name="stale")
    case("refuted: true (S8d must never render)", lambda d: d["issues"][0].__setitem__("refuted", True), True, must_name="refuted")
    case("fixed status without evidence", lambda d: d["issues"][0]["status_by_cut"][1].pop("evidence"), True, must_name="evidence")
    case("unknown ledger_row (ops mounted)", lambda d: d["issues"][0].__setitem__("ledger_row", "G99"), True, must_name="G99")
    case("empty repro command", lambda d: d["issues"][0]["repro"].__setitem__("command", ""), True, must_name="repro")
    case("review_by expired", lambda d: d["issues"][0].__setitem__("review_by", "2026-09-01"), True, must_name="review_by")
    case("review_by beyond 90 days", lambda d: d["issues"][0].__setitem__("review_by", "2027-06-01"), True, must_name="review_by")
    case("symptom over 300 chars", lambda d: d["issues"][0].__setitem__("symptom", "x" * 301), True, must_name="symptom")
    case("duplicate id", lambda d: d["issues"].append(copy.deepcopy(d["issues"][0])), True, must_name="duplicate")
    case("wrong spec_pin const", lambda d: d.__setitem__("spec_pin", "deadbeef"), True, must_name="spec_pin")
    case("ledger not mounted → cross-ref skipped, otherwise green", lambda d: None, want_red=False, ledger=None)

    print(f"\nknown-issues selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main())
