#!/usr/bin/env python3
"""
gen_coverage_lock.py — (re)generate coverage_lock.json, ADD-ONLY.

The lock is the set of accounted (check/exempt) requirement ids per pinned version.
This regenerates it from the current suite, but it will REFUSE to drop any id that
the existing lock holds unless that id is recorded in retirements.json. So the lock
grows as coverage grows and can never silently shrink — dropping an id is a
deliberate, spec-grounded, reviewed act (a retirement), not a regeneration side
effect.

  python3 conformance/coverage/gen_coverage_lock.py          # update (add-only)
  python3 conformance/coverage/gen_coverage_lock.py --check  # dry-run: would it drop anything?
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import matrix  # noqa: E402

LOCK = os.path.join(HERE, "coverage_lock.json")
RET = os.path.join(HERE, "retirements.json")


def current():
    cov = matrix.coverage_map()
    ex = matrix.load_exemptions()
    out = {}
    for v in matrix.VERSIONS:
        rows = {r["id"] for r in matrix.load_rows(v)}
        out[v] = {"check": sorted(cov[v].keys()),
                  "exempt": sorted(i for i in rows if matrix.exempt_at(ex, i, v))}
    return out


def retired_pairs():
    d = json.load(open(RET)) if os.path.exists(RET) else {"retirements": []}
    return {(e["id"], v) for e in d.get("retirements", []) for v in e.get("versions", [])}


def main():
    dry = "--check" in sys.argv
    cur = current()
    old = (json.load(open(LOCK))["versions"] if os.path.exists(LOCK) else
           {v: {"check": [], "exempt": []} for v in matrix.VERSIONS})
    retired = retired_pairs()
    dropped = []
    for v in matrix.VERSIONS:
        now_accounted = set(cur[v]["check"]) | set(cur[v]["exempt"])
        for i in set(old.get(v, {}).get("check", [])) | set(old.get(v, {}).get("exempt", [])):
            if i not in now_accounted and (i, v) not in retired:
                dropped.append(f"{v} {i}")
    if dropped:
        print("REFUSING to regenerate — these locked ids would be dropped without a "
              "retirement (add a spec-grounded entry to retirements.json first):")
        for d in dropped:
            print(f"  ✗ {d}")
        return 1
    if dry:
        print("dry-run: no locked id would be dropped — safe to regenerate.")
        return 0
    lock = {"_about": json.load(open(LOCK))["_about"] if os.path.exists(LOCK) else
            "Coverage lock — accounted (check/exempt) ids per pinned version; add-only "
            "(see verify_coverage_lock.py + retirements.json).",
            "versions": cur}
    json.dump(lock, open(LOCK, "w"), indent=1)
    tot = sum(len(cur[v]["check"]) + len(cur[v]["exempt"]) for v in matrix.VERSIONS)
    print(f"coverage_lock.json updated — {tot} accounted ids locked across {len(cur)} versions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
