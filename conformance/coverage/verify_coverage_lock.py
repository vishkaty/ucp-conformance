#!/usr/bin/env python3
"""
verify_coverage_lock.py — the TEST-INTEGRITY gate.

A pinned spec version is immutable (sources pinned by SHA), so a requirement that
was once covered by a kill-rate-validated CHECK — or accounted by a documented
EXEMPT — must stay accounted for the life of that version. This gate makes that a
build invariant, not a promise:

  For every (version, id) in coverage_lock.json:
    - a locked CHECK id must STILL be CHECK           (never silently deleted/weakened
      to a mere exemption or a gap)
    - a locked EXEMPT id must STILL be CHECK or EXEMPT (upgrading exempt->check is fine)
    - the ONLY way out is an entry in retirements.json with a spec-grounded reason.

So you cannot remove a test, downgrade a check to an exemption, or let a covered id
fall back to GAP without recording WHY in retirements.json — and the retirement
class must be a real one (unsound-check / superseded / spec-defect), never "flaky"
or "failing and unclear why". This is what stops us from covering a shortcoming by
deleting the test that exposes it.

Exit 0 = the lock holds; 1 = a covered requirement lost coverage without a sanctioned,
spec-grounded retirement (the message names the id + what's missing).
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import matrix  # noqa: E402

LOCK = os.path.join(HERE, "coverage_lock.json")
RET = os.path.join(HERE, "retirements.json")
VALID_CLASSES = {"unsound-check", "superseded", "spec-defect"}


def _retired():
    """{(id, version): entry} for every sanctioned retirement (validated)."""
    out, errs = {}, []
    d = json.load(open(RET)) if os.path.exists(RET) else {"retirements": []}
    for e in d.get("retirements", []):
        i, vers = e.get("id"), e.get("versions") or []
        cls, reason = e.get("class"), (e.get("reason") or "").strip()
        if cls not in VALID_CLASSES:
            errs.append(f"retirement {i}: class '{cls}' not in {sorted(VALID_CLASSES)}")
        if len(reason) < 40:
            errs.append(f"retirement {i}: reason too thin to be spec-grounded")
        if not (e.get("spec_source") or cls == "superseded"):
            errs.append(f"retirement {i}: no spec_source cited")
        for v in vers:
            out[(i, v)] = e
    return out, errs


def completeness_failures(lock, published):
    """B4 (W1 review V2) — the COMPLETENESS direction of the lock.

    coverage_lock.json is not only the floor the suite may not fall below; it is also the
    POPULATION verify_review_signoffs.py walks. So a published CHECK id that is OUTSIDE the
    lock is a coverage claim no adversarial review ever had to see: at 4a51834 the 08-25 lock
    held 77 CHECK ids while the export published 106, and the review gate still printed PASS.

    Red whenever a version's published CHECK set is a STRICT SUPERSET of its locked CHECK set.
    The other direction (locked ⊋ published) is the lock-holds rule above plus retirements.json
    and is deliberately not this rule's business.

    `lock` is {version: {check: [...], exempt: [...]}}; `published` is {version: set(ids)}.
    """
    out = []
    for v in sorted(published):
        extra = sorted(set(published[v]) - set((lock.get(v) or {}).get("check", [])))
        if not extra:
            continue
        shown = ", ".join(extra[:10]) + (f", … (+{len(extra) - 10} more)" if len(extra) > 10 else "")
        out.append(
            f"{v}: {len(extra)} published CHECK id(s) are not in coverage_lock.json ({shown}) — "
            f"the lock is the adversarial-review population, so these are coverage claims no "
            f"sign-off covers. Run gen_coverage_lock.py and add a review_signoffs.json batch "
            f"for them.")
    return out


def run(lock=None):
    """`lock` overrides coverage_lock.json's `versions` block (the selftest feeds a scratch
    lock through the SAME path the gate runs, so unwiring a rule from here cannot stay green)."""
    if lock is None:
        if not os.path.exists(LOCK):
            return ["coverage_lock.json missing — generate it with gen_coverage_lock.py"]
        lock = json.load(open(LOCK))["versions"]
    cov = matrix.coverage_map()
    ex = matrix.load_exemptions()
    retired, failures = _retired()

    def status(i, v):
        if i in cov[v]:
            return "check"
        if matrix.exempt_at(ex, i, v):
            return "exempt"
        return "gap"

    for v, locked in lock.items():
        for i in locked.get("check", []):
            s = status(i, v)
            if s == "check":
                continue
            if (i, v) in retired:
                continue
            failures.append(
                f"{v} {i}: was a CHECK in the lock, is now '{s}' — a covered requirement "
                f"lost its test. Restore the check, or add a spec-grounded entry to "
                f"retirements.json (class {sorted(VALID_CLASSES)}).")
        for i in locked.get("exempt", []):
            s = status(i, v)
            if s in ("check", "exempt"):
                continue
            if (i, v) in retired:
                continue
            failures.append(
                f"{v} {i}: was EXEMPT in the lock, is now GAP — accounting regressed. "
                f"Re-exempt/cover it, or record a retirement.")
    failures += completeness_failures(lock, {v: set(cov[v]) for v in cov})
    return failures


def main():
    failures = run()
    if failures:
        print("coverage-lock gate: FAIL — the test-integrity invariant was violated:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    lock = json.load(open(LOCK))["versions"]
    tot = sum(len(v["check"]) + len(v["exempt"]) for v in lock.values())
    npub = sum(len(matrix.coverage_map()[v]) for v in matrix.VERSIONS)
    print(f"coverage-lock gate: PASS — all {tot} locked (check+exempt) ids across "
          f"{len(lock)} versions are still accounted (or spec-groundedly retired), and all "
          f"{npub} published CHECK ids are inside the lock (the review population).")
    return 0


def selftest():
    """B4 (W1 review V2): the completeness direction — published CHECK ⊋ locked CHECK — is
    pure over (lock, published) so it can be driven on scratch data."""
    bad = 0

    def case(name, ok, detail=""):
        nonlocal bad
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  <-- {detail}"))
        bad += 0 if ok else 1

    V = "2026-08-25"
    try:
        strict = completeness_failures({V: {"check": ["A-1"], "exempt": []}}, {V: {"A-1", "B-2"}})
        equal = completeness_failures({V: {"check": ["A-1", "B-2"], "exempt": []}}, {V: {"A-1", "B-2"}})
        subset = completeness_failures({V: {"check": ["A-1", "B-2"], "exempt": []}}, {V: {"A-1"}})
        missing_v = completeness_failures({}, {V: {"A-1"}})
    except NameError as e:
        print(f"  ✗ completeness_failures absent: {e}")
        print("\ncoverage-lock selftest: FAIL (1 case(s))")
        return 1
    case("published CHECK ⊋ locked CHECK reds and names the unlocked id",
         len(strict) == 1 and "B-2" in strict[0] and V in strict[0], repr(strict))
    case("published CHECK == locked CHECK is clean", equal == [], repr(equal))
    case("published CHECK ⊊ locked CHECK is not this rule's business (the lock-holds "
         "direction + retirements own it)", subset == [], repr(subset))
    case("a version with no lock block at all reds", len(missing_v) == 1 and "A-1" in missing_v[0],
         repr(missing_v))
    # the rule must be WIRED INTO run(), not merely defined: feed run() a scratch lock that drops
    # one published CHECK id and require the real gate path to red naming it.
    real = json.load(open(LOCK))["versions"]
    drop = sorted(matrix.coverage_map()[V])[0]
    stale = {v: {"check": [i for i in b.get("check", []) if not (v == V and i == drop)],
                 "exempt": list(b.get("exempt", []))} for v, b in real.items()}
    wired = run(lock=stale)
    case(f"run() applies the completeness rule (scratch lock missing {V} {drop} -> red)",
         any("not in coverage_lock.json" in f and drop in f for f in wired), repr(wired[-3:]))
    case("run() on the committed lock is clean", run() == [], repr(run()[:3]))
    print(f"\ncoverage-lock selftest: {'PASS' if not bad else f'FAIL ({bad} case(s))'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    sys.exit(main())
