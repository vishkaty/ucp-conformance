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
substantive notes field. A batch that records a `sample` (the >=10% human re-read that is a
`kind: sample` batch's whole contract) is valid only while every sampled entry still
RESOLVES against its register, or carries a documented `sample.substitutions` record — a
frozen list of ids that a retirement has hollowed out is not a sample. Exit 0 = every locked check is reviewed; 1 = an unreviewed
check id is in the lock (the message names it).
"""
import json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LOCK = os.path.join(HERE, "coverage_lock.json")
SIGN = os.path.join(HERE, "review_signoffs.json")
RET = os.path.join(HERE, "retirements.json")
EXPIRY_REGISTERS = os.path.join(HERE, "expiry_registers.json")
REQ_DIR = os.path.join(ROOT, "conformance", "requirements")


def _retired_ids():
    if not os.path.exists(RET):
        return set()
    d = json.load(open(RET))
    return {(e.get("id"), v) for e in d.get("retirements", []) for v in (e.get("versions") or [])}


SEED_PREFIX = "expiry-clock-seed"
SEED_SAMPLE_FRACTION = 0.10


def is_seed_batch(signoff):
    """A SAMPLE batch: confers no coverage; its contract is the recorded >=10% human
    sample (decision 13). Either the expiry-clock seed (batch name prefix, D2-04) or any
    batch declaring `kind: sample` (D2-08 role adjudications; D2-11a normative-basis
    adjudications; later A10 exemption batches)."""
    return str(signoff.get("batch", "")).startswith(SEED_PREFIX) or signoff.get("kind") == "sample"


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



# ─── F5 G5: the sampled entries must still EXIST ────────────────────────────────────
# seed_batch_errors above polices `size == len(sample.ids)` and `size >= 10% of of`, but
# `sample.ids` is frozen text: nothing resolved the entries it names. D2-08 retired
# agent_lane_overrides.json (two registers, 42 clocked entries) and took two SAMPLED
# entries with it, so the RE-READABLE sample fell from 34/336 (10.1%) to 32/336 (9.52%) —
# under its own floor — while this gate stayed green, because both frozen arithmetic checks
# still held against entries that no longer exist. A sample nobody can re-read is not a
# sample; the >=10% human re-read is the whole contract of a `kind: sample` batch
# (decision 13), so it has to be a contract about live entries.
#
# `sample.substitutions` is the documented, auditable way for a sampled entry to go away.
# Each record names:
#   retired            the exact sample.ids entry that no longer resolves (it must be in
#                      sample.ids, and it must really not resolve — a substitution cannot be
#                      pre-armed against a live entry)
#   replaced_by        a register entry that DOES resolve and was re-read in its place, or
#                      null when the entry simply left the population
#   population_retired (only with replaced_by: null) how many stamped entries left the
#                      sample's population together with it, so the retirement shrinks the
#                      DENOMINATOR as well as the numerator instead of silently eating the
#                      floor
#   reason             >= 40 chars naming what removed it (the task / decision / commit)
#   on                 ISO date of the substitution
SUBSTITUTION_MIN_REASON = 40
VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROW_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def _expiry_register_entry_paths():
    """{register file basename: {entry path, ...}} over every register listed in
    expiry_registers.json — the resolution target for a sampled clocked entry, written in
    the `<file basename> <entry path>` form the seed batch records (e.g.
    "exemptions.json CART-024", "register_completeness_waivers.json waivers[100]").
    A retired register is simply absent from the registry, so its entries stop resolving —
    which is exactly the signal this gate was missing."""
    sys.path.insert(0, os.path.join(ROOT, "conformance", "selfcheck"))
    try:
        from validate_expiry_clocks import iter_entries            # the gate's own walker
    except Exception:                                              # noqa: BLE001
        return {}
    try:
        regs = json.load(open(EXPIRY_REGISTERS)).get("registers", [])
    except Exception:                                              # noqa: BLE001
        return {}
    out = {}
    for reg in regs:
        fp = os.path.join(ROOT, reg.get("file", ""))
        if not os.path.exists(fp):
            continue
        try:
            doc = json.load(open(fp))
            paths = {path for path, _e in iter_entries(reg, doc)}
        except Exception:                                          # noqa: BLE001
            continue
        out.setdefault(os.path.basename(reg["file"]), set()).update(paths)
    return out


def _register_row_ids():
    """{version: {row ids present at that version}} — the resolution target for a sampled
    register row, written either as "<version> <ROW-ID>" (the role / normative-basis
    adjudication samples) or as a bare "<ROW-ID>" (the coverage-lock samples, whose version
    comes from the batch's own `ids` keys)."""
    out = {}
    if not os.path.isdir(REQ_DIR):
        return out
    for ver in sorted(os.listdir(REQ_DIR)):
        vdir = os.path.join(REQ_DIR, ver)
        if not os.path.isdir(vdir) or ver[:2] != "20":
            continue
        ids = out.setdefault(ver, set())
        for af in sorted(f for f in os.listdir(vdir) if f.endswith(".json")):
            try:
                rows = json.load(open(os.path.join(vdir, af))).get("rows", [])
            except Exception:                                      # noqa: BLE001
                continue
            for r in rows:
                if ver in (r.get("versions") or [ver]):
                    ids.add(r.get("id"))
    return out


def default_sample_resolver(signoff=None):
    """A `resolver(entry) -> bool` over the committed registers. `signoff` supplies the
    versions a bare row id may resolve at (the batch's own `ids` keys)."""
    reg_paths = _expiry_register_entry_paths()
    row_ids = _register_row_ids()
    batch_versions = list((signoff or {}).get("ids") or {}) or list(row_ids)

    def resolve(entry):
        toks = str(entry).split(None, 1)
        if not toks:
            return False
        head = toks[0]
        if head.endswith(".json"):
            return len(toks) == 2 and toks[1] in reg_paths.get(head, set())
        if VERSION_RE.match(head):
            return len(toks) == 2 and toks[1] in row_ids.get(head, set())
        if ROW_ID_RE.match(head) and len(toks) == 1:
            return any(head in row_ids.get(v, set()) for v in batch_versions)
        return False
    return resolve


def sample_resolution_errors(signoff, resolver=None, today=None):
    """Every `sample.ids` entry of this batch must still RESOLVE against its register, or
    be covered by a documented `sample.substitutions` record; and the RE-READABLE sample
    (entries that resolve, plus substitutions whose replacement resolves) must still clear
    the 10% floor against the population the retirements left behind. `resolver` is a
    callable(entry)->bool; None builds one over the committed registers. Returns a list of
    problems (empty = the sample is live)."""
    from datetime import date
    smp = signoff.get("sample")
    if not isinstance(smp, dict):
        return []                       # seed_batch_errors already reds a missing sample
    if resolver is None:
        resolver = default_sample_resolver(signoff)
    errs = []
    ids = smp.get("ids") or []
    subs = smp.get("substitutions") or []
    if not isinstance(subs, list):
        return ["sample.substitutions must be a list"]

    retired_ok, population_retired = set(), 0
    for i, sub in enumerate(subs):
        label = f"sample.substitutions[{i}]"
        if not isinstance(sub, dict):
            errs.append(f"{label} must be an object")
            continue
        ret = sub.get("retired")
        if not ret:
            errs.append(f"{label}: no `retired` — name the sample.ids entry that went away")
            continue
        if ret not in ids:
            errs.append(f"{label}: `retired` {ret!r} is not in sample.ids — a substitution "
                        f"documents a change to THIS sample, it cannot introduce an entry")
            continue
        if resolver(ret):
            errs.append(f"{label}: `retired` {ret!r} still RESOLVES — a substitution may not "
                        f"be pre-armed against a live entry; drop it and re-read the entry")
            continue
        reason = (sub.get("reason") or "").strip()
        if len(reason) < SUBSTITUTION_MIN_REASON:
            errs.append(f"{label} ({ret}): reason too thin (<{SUBSTITUTION_MIN_REASON} chars) "
                        f"— name the task/decision/commit that removed the entry")
        on = sub.get("on")
        try:
            date.fromisoformat(str(on))
        except Exception:                                          # noqa: BLE001
            errs.append(f"{label} ({ret}): `on` {on!r} is not an ISO date")
        rep, pop = sub.get("replaced_by"), sub.get("population_retired")
        if rep is not None:
            if not resolver(rep):
                errs.append(f"{label} ({ret}): `replaced_by` {rep!r} does not resolve either "
                            f"— a replacement must be an entry that exists and was re-read")
            elif pop is not None:
                errs.append(f"{label} ({ret}): `population_retired` is only for an entry that "
                            f"LEFT the population (`replaced_by: null`); this one was replaced, "
                            f"so the population did not shrink")
            else:
                retired_ok.add(ret)
        else:
            if pop is not None:
                if not isinstance(pop, int) or isinstance(pop, bool) or pop < 0:
                    errs.append(f"{label} ({ret}): `population_retired` {pop!r} must be a "
                                f"non-negative integer")
                else:
                    population_retired += pop
            retired_ok.add(ret)

    substituted = {s.get("retired") for s in subs if isinstance(s, dict)}
    readable = 0
    for entry in ids:
        if resolver(entry):
            readable += 1
        elif entry in retired_ok:
            # a replaced entry contributes its replacement; a withdrawn one contributes
            # nothing to the numerator (its population share comes off the denominator)
            if any(isinstance(s, dict) and s.get("retired") == entry
                   and s.get("replaced_by") is not None for s in subs):
                readable += 1
        elif entry not in substituted:
            errs.append(f"sample.ids entry {entry!r} no longer resolves against its register "
                        f"— the retirement that removed it hollowed out the recorded sample; "
                        f"record a `sample.substitutions` entry for it (replacement re-read, "
                        f"or the population it left with) or re-draw the sample")

    of = smp.get("of")
    if isinstance(of, int) and of > 0:
        eff_of = max(of - population_retired, 0)
        floor = math.ceil(SEED_SAMPLE_FRACTION * eff_of)
        if eff_of and readable < floor:
            errs.append(f"re-readable sample {readable}/{eff_of} "
                        f"({100 * readable / eff_of:.2f}%) is under the "
                        f"{int(SEED_SAMPLE_FRACTION * 100)}% floor (need >= {floor}) — "
                        f"{len(ids) - readable} of {len(ids)} sampled entries can no longer "
                        f"be re-read; a sample nobody can re-read is not a sample")
    return errs

def seed_batch_status(signoff):
    """One line for the PASS output: 'expiry-clock-seed-2026-09: sampled 12% · human recorded'.

    When the batch carries `sample.substitutions` the line reports the RE-READABLE sample
    against the population the retirements left behind, not the frozen `size`/`of`: the
    frozen pair is what read 10% while only 9.52% of the sample could still be re-read
    (F5 G5), so the published figure has to be the live one."""
    smp = signoff.get("sample") or {}
    of, size = smp.get("of") or 0, smp.get("size") or 0
    subs = [s for s in (smp.get("substitutions") or []) if isinstance(s, dict)]
    note = ""
    if subs:
        withdrawn = sum(1 for s in subs if s.get("replaced_by") is None)
        of = max(of - sum(s.get("population_retired") or 0 for s in subs
                          if s.get("replaced_by") is None), 0)
        size = max(size - withdrawn, 0)
        note = f" · {len(subs)} substitution(s)"
    pct = round(100 * size / of) if of else 0
    hr = (smp.get("human_review") or {})
    human = "human recorded" if hr.get("status") == "recorded" else f"human PENDING (due {hr.get('due')})"
    return f"{signoff.get('batch')}: sampled {pct}% ({size}/{of}) · {human}{note}"


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
            problems += sample_resolution_errors(s)
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
        # decision 13: a coverage batch that declares a `sample` (>=10% human re-read of a
        # machine-assisted review) is valid only while that sample contract holds — a
        # pending sample past its due date invalidates the batch, and with it the
        # coverage it confers (fail-noisy, never a decorative field).
        if isinstance(s.get("sample"), dict):
            problems += seed_batch_errors(s)
            problems += sample_resolution_errors(s)
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
        if is_seed_batch(s) or isinstance(s.get("sample"), dict):
            print(f"  {seed_batch_status(s)}")
    print(f"review-signoff gate: PASS — all {tot} locked CHECK ids across {len(lock)} "
          f"versions carry an adversarial-review sign-off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
