#!/usr/bin/env python3
"""
verdict_gate.py — the report verdict/coverage gate (methodology section E).

Turns per-check results into an honest aggregate, enforcing the hard rule:
NO aggregate "pass" unless EVERY in-scope MUST is a clean-pass from a
mutation-validated (kill-safe) check, with the scope stamp + unofficial
disclaimer present. Anything short of that is "incomplete" (cannot claim
conformance) or "fail" (a MUST was violated) — never a green.

Per-check status vocabulary:
  clean-pass     requirement observed satisfied
  deviation      a MUST was violated            -> blocks green (aggregate fail)
  advisory       a SHOULD was violated          -> does NOT block green
  informational  a MAY note                     -> does NOT block green
  not-tested     requirement not exercised      -> blocks green (can't claim pass)
  inconclusive   transient/timeout/unsafe-check -> blocks green, never a pass/fail

Wiring to the mutation harness: a check that did NOT pass its kill-rate gate
(kill_safe=False) cannot contribute a clean-pass; its result is downgraded to
`inconclusive` here. So a no-op check can never produce a green.
"""
from dataclasses import dataclass, field

CLEAN, DEVIATION, ADVISORY, INFO, NOT_TESTED, INCONCLUSIVE = (
    "clean-pass", "deviation", "advisory", "informational", "not-tested", "inconclusive")

@dataclass
class CheckResult:
    req_id: str
    keyword: str            # MUST | MUST NOT | SHOULD | SHOULD NOT | MAY
    status: str             # one of the vocabulary above
    kill_safe: bool = True  # did the check pass its mutation kill-rate gate?

@dataclass
class Report:
    aggregate: str          # "pass" | "fail" | "incomplete" | "blocked"
    coverage: float
    counts: dict
    blocking: list = field(default_factory=list)
    advisories: list = field(default_factory=list)
    scope_stamp: dict = field(default_factory=dict)

def _effective(r: CheckResult) -> str:
    # An unsafe (non-mutation-validated) check cannot assert a pass.
    if r.status == CLEAN and not r.kill_safe:
        return INCONCLUSIVE
    return r.status

def aggregate(results, inscope_must_ids, scope_stamp, disclaimer):
    """results: list[CheckResult]; inscope_must_ids: set of MUST requirement ids that
    are in scope (REST, this version, testable). Returns a Report. A green ("pass")
    is only possible when every in-scope MUST is a kill-safe clean-pass AND the scope
    stamp + disclaimer are present."""
    musts_is = {k for k in ("MUST", "MUST NOT")}
    by_id = {}
    for r in results:
        by_id.setdefault(r.req_id, []).append(r)

    covered_musts, deviations, blocking = set(), [], []
    advisories = []
    for r in results:
        eff = _effective(r)
        if r.keyword in musts_is:
            if eff == CLEAN:
                covered_musts.add(r.req_id)
            elif eff == DEVIATION:
                deviations.append(r.req_id)
                blocking.append(f"deviation: MUST {r.req_id} violated")
            elif eff in (NOT_TESTED, INCONCLUSIVE):
                blocking.append(f"{eff}: MUST {r.req_id}")
        elif r.keyword in ("SHOULD", "SHOULD NOT") and eff in (DEVIATION, ADVISORY):
            advisories.append(f"advisory: SHOULD {r.req_id}")

    # in-scope MUSTs with no covering clean-pass and no explicit result => not-tested
    untested = sorted(set(inscope_must_ids) - covered_musts - set(deviations))
    for u in untested:
        if not any(x in (NOT_TESTED, INCONCLUSIVE, DEVIATION)
                   for x in (_effective(r) for r in by_id.get(u, []))):
            blocking.append(f"not-tested: MUST {u}")

    total = max(1, len(inscope_must_ids))
    coverage = len(covered_musts & set(inscope_must_ids)) / total

    has_stamp = bool(scope_stamp) and bool(disclaimer)
    if deviations:
        agg = "fail"
    elif not has_stamp:
        agg = "blocked"
        blocking.append("missing scope stamp / unofficial disclaimer")
    elif blocking:
        agg = "incomplete"
    else:
        agg = "pass"

    counts = {
        "inscope_musts": len(inscope_must_ids),
        "musts_clean_pass": len(covered_musts & set(inscope_must_ids)),
        "deviations": len(deviations),
        "blocking": len([b for b in blocking if not b.startswith("advisory")]),
        "advisories": len(advisories),
    }
    return Report(agg, round(coverage, 4), counts,
                  blocking=blocking, advisories=advisories,
                  scope_stamp=(scope_stamp if has_stamp else {}))


# ----------------------------- self-tests ----------------------------------
def _selftest():
    STAMP = {"version": "2026-01-23", "spec_commit": "dcf7eac7", "tool": "spck-dev",
             "methodology": "v1"}
    DISC = "Unofficial. Not affiliated with or endorsed by the UCP project."
    M = {"CHK-018", "CHK-021", "NEG-001"}

    def case(name, results, inscope=M, stamp=STAMP, disc=DISC):
        return name, aggregate(results, inscope, stamp, disc)

    cases = []
    # 1. all MUSTs kill-safe clean-pass -> pass
    cases.append(case("all-pass", [
        CheckResult("CHK-018", "MUST", CLEAN), CheckResult("CHK-021", "MUST", CLEAN),
        CheckResult("NEG-001", "MUST", CLEAN)]))
    # 2. one MUST not exercised -> incomplete (NOT pass)
    cases.append(case("one-untested", [
        CheckResult("CHK-018", "MUST", CLEAN), CheckResult("CHK-021", "MUST", CLEAN)]))
    # 3. one MUST deviation -> fail
    cases.append(case("one-deviation", [
        CheckResult("CHK-018", "MUST", CLEAN), CheckResult("CHK-021", "MUST", CLEAN),
        CheckResult("NEG-001", "MUST", DEVIATION)]))
    # 4. SHOULD failure present, all MUSTs pass -> still pass (advisory, non-blocking)
    cases.append(case("should-advisory", [
        CheckResult("CHK-018", "MUST", CLEAN), CheckResult("CHK-021", "MUST", CLEAN),
        CheckResult("NEG-001", "MUST", CLEAN), CheckResult("CHK-027", "SHOULD", DEVIATION)]))
    # 5. a MUST clean-pass but check NOT kill-safe -> downgraded -> incomplete (wires 0e->0f)
    cases.append(case("unsafe-check", [
        CheckResult("CHK-018", "MUST", CLEAN), CheckResult("CHK-021", "MUST", CLEAN),
        CheckResult("NEG-001", "MUST", CLEAN, kill_safe=False)]))
    # 6. inconclusive MUST -> incomplete
    cases.append(case("inconclusive", [
        CheckResult("CHK-018", "MUST", CLEAN), CheckResult("CHK-021", "MUST", CLEAN),
        CheckResult("NEG-001", "MUST", INCONCLUSIVE)]))
    # 7. all pass but NO scope stamp -> blocked (no green without stamp+disclaimer)
    cases.append(("no-stamp", aggregate(
        [CheckResult(i, "MUST", CLEAN) for i in M], M, {}, "")))

    expect = {"all-pass": "pass", "one-untested": "incomplete", "one-deviation": "fail",
              "should-advisory": "pass", "unsafe-check": "incomplete",
              "inconclusive": "incomplete", "no-stamp": "blocked"}
    ok = True
    for name, rep in cases:
        good = rep.aggregate == expect[name]
        ok &= good
        print(f"  {'OK ' if good else 'XX '} {name:16} -> {rep.aggregate:11} "
              f"(expected {expect[name]}; coverage {rep.coverage})")
    print(f"\nverdict-gate self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
