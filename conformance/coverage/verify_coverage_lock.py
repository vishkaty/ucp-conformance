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


def run():
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
    print(f"coverage-lock gate: PASS — all {tot} locked (check+exempt) ids across "
          f"{len(lock)} versions are still accounted (or spec-groundedly retired).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
