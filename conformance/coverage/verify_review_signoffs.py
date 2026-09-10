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


SEED_PREFIX = "expiry-clock-seed"
SEED_SAMPLE_FRACTION = 0.10


def is_seed_batch(signoff):
    return str(signoff.get("batch", "")).startswith(SEED_PREFIX)


def seed_batch_errors(signoff, today=None):
    """The A10 sample contract for an expiry-clock seed batch (D2-04, RV2 V3): a seed
    batch is valid only with a RECORDED sample — `sample.seed` (the RNG seed the
    selection is reproducible from), `sample.of` (entries stamped), `sample.size` ==
    len(sample.ids) >= 10% of `of`, and `sample.human_review` {status, by, on, due}.
    `status: recorded` = a human re-read the sampled entries; `status: pending` is
    tolerated only until `due` (the owner's action window) — past it, the batch is as
    invalid as having no sample. Returns a list of problems (empty = valid)."""
    import math
    from datetime import date
    today = today or date.today()
    errs = []
    smp = signoff.get("sample")
    if not isinstance(smp, dict):
        return ["seed batch has no recorded sample (>=10% human sample is the contract)"]
    of, size, ids = smp.get("of"), smp.get("size"), smp.get("ids") or []
    if not isinstance(of, int) or of <= 0:
        errs.append("sample.of missing")
    if smp.get("seed") is None:
        errs.append("sample.seed missing (selection must be reproducible)")
    if not isinstance(size, int) or size != len(ids):
        errs.append(f"sample.size {size!r} != len(sample.ids) {len(ids)}")
    elif isinstance(of, int) and of > 0 and size < math.ceil(SEED_SAMPLE_FRACTION * of):
        errs.append(f"sample.size {size} < 10% of {of} (need >= {math.ceil(SEED_SAMPLE_FRACTION * of)})")
    hr = smp.get("human_review") or {}
    st = hr.get("status")
    if st == "recorded":
        if not hr.get("by") or not hr.get("on"):
            errs.append("human_review recorded without by/on")
    elif st == "pending":
        due = hr.get("due", "")
        try:
            past = date.fromisoformat(due) < today
        except Exception:                               # noqa: BLE001 — a bad date is a problem
            past = True
        if past:
            errs.append(f"human_review still pending past due {due!r}")
    else:
        errs.append(f"human_review.status {st!r} must be recorded|pending")
    return errs


def seed_batch_status(signoff):
    """One line for the PASS output: 'expiry-clock-seed-2026-09: sampled 12% · human recorded'."""
    smp = signoff.get("sample") or {}
    of, size = smp.get("of") or 0, smp.get("size") or 0
    pct = round(100 * size / of) if of else 0
    hr = (smp.get("human_review") or {})
    human = "human recorded" if hr.get("status") == "recorded" else f"human PENDING (due {hr.get('due')})"
    return f"{signoff.get('batch')}: sampled {pct}% ({size}/{of}) · {human}"


def _signed_ids():
    """{version: set(ids)} from VALID sign-offs; plus a list of validation errors."""
    out, errs = {}, []
    if not os.path.exists(SIGN):
        return out, ["review_signoffs.json missing"]
    for s in json.load(open(SIGN)).get("signoffs", []):
        batch = s.get("batch", "?")
        problems = []
        if is_seed_batch(s):
            # an expiry-clock seed batch confers no coverage; its contract is the sample
            problems += seed_batch_errors(s)
            if not s.get("reviewer") or not s.get("date"):
                problems.append("no reviewer/date")
            if problems:
                errs.append(f"sign-off '{batch}': " + "; ".join(problems))
            continue
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
    for s in json.load(open(SIGN)).get("signoffs", []):
        if is_seed_batch(s):
            print(f"  {seed_batch_status(s)}")
    print(f"review-signoff gate: PASS — all {tot} locked CHECK ids across {len(lock)} "
          f"versions carry an adversarial-review sign-off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
