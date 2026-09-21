#!/usr/bin/env python3
"""
killtest.py: proves the checks are non vacuous (golden_boot_guards.py pattern). For each
check, the kill leg (predicate == DEVIATION on the check's kill_mutation run) is re run
with the check's assertions excised: all of them (a vacuous predicate) MUST turn the kill
leg green, i.e. the reference gate would go red; and each one alone, reporting which
assertions are individually load bearing. Exit 0 when every check is proven, 1 otherwise.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checks import CHECKS, PCheck, CLEAN, DEVIATION   # noqa: E402
from harness import run_scenario                       # noqa: E402
from reference_platform import CONFORMANT              # noqa: E402


def main():
    fails = 0
    for c in CHECKS:
        clean_run = run_scenario(c.scenario, CONFORMANT)
        kill_run = run_scenario(c.scenario, c.kill_mutation)
        base = (c.predicate(clean_run)[0], c.predicate(kill_run)[0])
        if base != (CLEAN, DEVIATION):
            print("FAIL %s: reference gate itself not (CLEAN, DEVIATION) but %s" % (c.id, base)); fails += 1; continue
        vacuous = PCheck(c.id, c.req_ids, c.keyword, c.scenario, c.kill_mutation, [], c.clause)
        v = vacuous.predicate(kill_run)[0]
        if v != CLEAN:
            print("FAIL %s: excising every assertion did not turn the kill leg green (%s)" % (c.id, v)); fails += 1; continue
        bearing = []
        for name, _ in c.assertions:
            partial = PCheck(c.id, c.req_ids, c.keyword, c.scenario, c.kill_mutation,
                             [(n, f) for n, f in c.assertions if n != name], c.clause)
            if partial.predicate(kill_run)[0] == CLEAN:
                bearing.append(name)
        print("OK   %s: kill leg goes green with all %d assertions excised; solely load bearing: %s"
              % (c.id, len(c.assertions), bearing or "none (each defect is caught by more than one assertion)"))
    print("killtest: %d check(s), %d failure(s)" % (len(CHECKS), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
