#!/usr/bin/env python3
"""
verify_review_signoffs.py — the COVERAGE-EXPANSION gate (review is not optional).

The coverage-lock gate stops a covered requirement from silently LOSING its test.
This gate is its mirror on the way IN: every CHECK id in coverage_lock.json must be
covered by an adversarial-review sign-off in review_signoffs.json — a recorded
statement that an INDEPENDENT reviewer re-read the pinned spec at that check's cited
clause and confirmed it binds the right subject, cites faithfully, and is not
over-strict. The strongest quality mechanism this project has (independent spec
re-read) is thereby made a build requirement, not a discretionary step: a future
session cannot grow the accounted coverage while skipping the review.

  For every (version, id) in coverage_lock.json's CHECK list:
    it must appear in a VALID sign-off for that version, OR be a sanctioned
    retirement (a retired check is no longer a live check, so it needs no sign-off).

A sign-off is valid only if it names a reviewer, a date, spec_reverified=true, and a
substantive notes field. Exit 0 = every locked check is reviewed; 1 = an unreviewed
check id is in the lock (the message names it).
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, "coverage_lock.json")
SIGN = os.path.join(HERE, "review_signoffs.json")
RET = os.path.join(HERE, "retirements.json")


def _retired_ids():
    if not os.path.exists(RET):
        return set()
    d = json.load(open(RET))
    return {(e.get("id"), v) for e in d.get("retirements", []) for v in (e.get("versions") or [])}


def _signed_ids():
    """{version: set(ids)} from VALID sign-offs; plus a list of validation errors."""
    out, errs = {}, []
    if not os.path.exists(SIGN):
        return out, ["review_signoffs.json missing"]
    for s in json.load(open(SIGN)).get("signoffs", []):
        batch = s.get("batch", "?")
        problems = []
        if not s.get("reviewer"):
            problems.append("no reviewer")
        if not s.get("date"):
            problems.append("no date")
        if s.get("spec_reverified") is not True:
            problems.append("spec_reverified must be true")
        if len((s.get("notes") or "").strip()) < 40:
            problems.append("notes too thin (say what was re-verified)")
        ids = s.get("ids") or {}
        if not any(ids.values()):
            problems.append("no ids")
        if problems:
            errs.append(f"sign-off '{batch}': " + "; ".join(problems))
            continue  # an invalid sign-off confers no coverage
        for v, idlist in ids.items():
            out.setdefault(v, set()).update(idlist)
    return out, errs


def run():
    if not os.path.exists(LOCK):
        return ["coverage_lock.json missing"]
    lock = json.load(open(LOCK))["versions"]
    signed, failures = _signed_ids()
    retired = _retired_ids()
    for v, locked in lock.items():
        sv = signed.get(v, set())
        for i in locked.get("check", []):
            if i in sv:
                continue
            if (i, v) in retired:
                continue
            failures.append(
                f"{v} {i}: a locked CHECK with no adversarial-review sign-off. Coverage "
                f"cannot grow without review — add {i} to a sign-off batch in "
                f"review_signoffs.json (reviewer re-read the pinned clause and confirmed "
                f"subject/citation/strictness).")
    return failures


def main():
    failures = run()
    if failures:
        print("review-signoff gate: FAIL — a covered check was not adversarially reviewed:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    lock = json.load(open(LOCK))["versions"]
    tot = sum(len(v["check"]) for v in lock.values())
    print(f"review-signoff gate: PASS — all {tot} locked CHECK ids across {len(lock)} "
          f"versions carry an adversarial-review sign-off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
