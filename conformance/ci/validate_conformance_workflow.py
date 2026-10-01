#!/usr/bin/env python3
"""
validate_conformance_workflow.py — the conformance.yml contract (F8), hermetic.

Branch protection on main requires the check `selftest` in strict mode, so ANYTHING that
stops that job from running and reporting blocks every pull request for ever.

Measured on PR #9. It was opened 2026-09-24 on one commit, b1546b09, touching only
WORKSTREAMS.md. That matched the `pull_request` paths filter nowhere, so NO pull_request
run ever started, the required `selftest` context never appeared, and the pull request
sat unmergeable for seven days with no failing check to act on. Forcing the suite by hand
with a workflow_dispatch (run #259) was the only way to make a `selftest` check exist at
all, and that run reported 139 passed and 1 failed, the one failure being
`oracle-verdict-diff`. Two further commits then went on the branch together, 84d7ec4f
re-recording the oracle verdict diff and b9642a87 re-running the battery; BOTH touch
conformance/, so either would have satisfied the filter, and the single pull_request run
they produced, #260, went green and the pull request merged.
Nothing about the first head was ever wrong; it was unreviewable by accident of its paths.

This gate pins the shape that fixes that, so a later edit cannot quietly restore it:

  trigger      `pull_request` carries NO `paths:` or `paths-ignore:` filter, and the
               `selftest` job carries no job-level `if:`. A filtered trigger and a
               skipped job both report no check at all, which is the deadlock.
  scope        the first step of `selftest` is the ungated scope decision (id: scope) and
               EVERY later step is conditional on its verdict, so an irrelevant change
               costs seconds and still reports a green `selftest`.
  fail-safe    that script decides RELEVANT for any non-pull_request event and for an
               unreadable changed-file list, and only ONE branch may short-circuit.
  merge base   it reads the pull request's files from the pulls/<n>/files endpoint, which
               diffs from the merge base; the tip commit alone misses earlier files.
  push paths   `on.push.paths` equals `env.WATCHED_PATHS` translated (a trailing "/"
               becomes "/**") plus exactly `action.yml`, and WATCHED_PATHS itself must
               NOT list action.yml. The two lists answer different questions; the
               workflow comments carry the reasoning.
  action job   `action-selftest` has no scope step and references no scope output. It is
               the only job that executes action.yml, and a reference to a step that does
               not exist is always empty, which would skip every step silently.

Exit 0 PASS · 1 finding.  --selftest: kill-tests on mutated copies (a paths filter back
on pull_request -> red, a job-level if -> red, an ungated step -> red, the scope step
gated -> red, a dropped or stray push path -> red, action.yml in WATCHED_PATHS -> red, a
fail-safe branch flipped to short-circuit -> red, a scope reference in action-selftest
-> red).
"""
import argparse
import copy
import glob
import importlib
import pathlib
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "conformance.yml"

# The required context. Losing its report is the failure this whole gate exists to stop.
REQUIRED_JOB = "selftest"
# Exercises action.yml, is NOT a required context, and is deliberately never scope-gated.
ACTION_JOB = "action-selftest"
# Every step after the scope decision must carry this in its `if:`.
GUARD = "steps.scope.outputs.relevant == 'true'"
# In `on.push.paths` but deliberately NOT in env.WATCHED_PATHS: it starts the workflow so
# ACTION_JOB runs, while the self-test suite itself never reads it.
PUSH_ONLY = ("action.yml",)
# Sentinels the scope script must keep: each is a branch that must fail OPEN.
FAILSAFE = (
    ("is not a pull_request", "the non-pull_request fail-open branch"),
    ("failing SAFE to RELEVANT", "the unreadable-changed-file-list fail-safe branch"),
)
# Registered gates that READ files OUTSIDE conformance/ and packaging/. Their own
# module-level definitions are the source of truth, imported rather than copied, so a gate
# that gains an input reds this workflow until env.WATCHED_PATHS covers it. Short-circuiting
# a file one of these reads lets a change that reds a gate merge green and break main.
# The three shapes are all real, which is why this is not a single list of strings:
#   "paths" a tuple of repo-relative paths · "path" one pathlib.Path, absolute under ROOT
#   · "globs" a tuple of glob patterns, expanded against ROOT so the check is on real files.
GATE_INPUT_SOURCES = (
    ("validate_steward_docs", "DOCS", "paths"),
    ("site_gates", "DOC_FILES", "paths"),
    ("validate_nightly_workflow", "NIGHTLY", "path"),
    ("validate_ports_registry", "SCAN_GLOBS", "globs"),
)


def _resolve(kind, value):
    """One gate's declared inputs -> repo-relative paths this workflow must watch."""
    if kind == "paths":
        return tuple(str(v) for v in value)
    if kind == "path":
        return (str(pathlib.Path(value).resolve().relative_to(ROOT)),)
    if kind == "globs":
        out = []
        for pat in value:
            out.extend(
                str(pathlib.Path(p).resolve().relative_to(ROOT))
                for p in glob.glob(str(ROOT / pat), recursive=True)
                if pathlib.Path(p).is_file()
            )
        return tuple(sorted(set(out)))
    raise ValueError(f"unknown gate-input kind {kind!r}")


def gated_inputs():
    """(label, files) from the other gates' own definitions, plus errors.

    An import failure, an unreadable attribute or an EMPTY resolution is returned as a
    finding, never swallowed. A silently empty list would make the coverage clause below
    pass vacuously, which is the one failure mode a gate must not have.
    """
    pairs, errs = [], []
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    for mod, attr, kind in GATE_INPUT_SOURCES:
        label = f"{mod}.{attr}"
        try:
            files = _resolve(kind, getattr(importlib.import_module(mod), attr))
        except Exception as exc:  # noqa: BLE001 - any failure here must be loud
            errs.append(
                f"cannot read {label} ({type(exc).__name__}: {exc}): the watched-set "
                f"coverage check cannot run, so it would pass vacuously"
            )
            continue
        if not files:
            errs.append(
                f"{label} resolved to NO files: the watched-set coverage check would pass "
                f"vacuously for that gate, so treat it as a failure rather than a clean run"
            )
            continue
        pairs.append((label, files))
    return pairs, errs


def covered_by(rel, watched):
    """Does any env.WATCHED_PATHS entry cover this repo-relative path?"""
    for w in watched:
        if w.endswith("/"):
            if rel.startswith(w):
                return True
        elif rel == w:
            return True
    return False


def load(path=WORKFLOW):
    return yaml.safe_load(pathlib.Path(path).read_text())


def triggers(doc):
    # PyYAML resolves a bare `on:` key to the boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def watched_paths(doc):
    raw = (doc.get("env") or {}).get("WATCHED_PATHS") or ""
    return [ln.strip() for ln in raw.split("\n") if ln.strip()]


def as_push_glob(entry):
    """WATCHED_PATHS spelling -> on.push.paths spelling."""
    return entry[:-1] + "/**" if entry.endswith("/") else entry


def steps_of(job):
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def step_label(step, i):
    return f"[{i}] {(step.get('name') or step.get('uses') or '(run)')!r}"


def check(doc):
    f = []
    on = triggers(doc)
    jobs = doc.get("jobs") or {}

    # ── the trigger must not filter pull requests ───────────────────────────────────
    if "pull_request" not in on:
        f.append(
            "no pull_request trigger: the required `selftest` context would never report "
            "on a pull request, so every pull request is blocked"
        )
    else:
        pr = on["pull_request"] or {}
        if isinstance(pr, dict):
            for key in ("paths", "paths-ignore"):
                if pr.get(key):
                    f.append(
                        f"on.pull_request.{key} is set: a filtered trigger starts no run, so "
                        f"the required `selftest` check never reports and a pull request it "
                        f"does not match is blocked for ever (PR #9, head b1546b09)"
                    )

    # ── the watched set, and the one deliberate difference ──────────────────────────
    watched = watched_paths(doc)
    if not watched:
        f.append("env.WATCHED_PATHS is missing or empty: the scope step would match nothing")
    for extra in PUSH_ONLY:
        if extra in watched or extra in {as_push_glob(w) for w in watched}:
            f.append(
                f"env.WATCHED_PATHS lists {extra!r}: the self-test suite does not read it, so "
                f"listing it there buys a full irrelevant suite run; it belongs in "
                f"on.push.paths only"
            )
    # Anything another gate reads must be inside the watched set, or a pull request
    # touching only that file short-circuits the required check to green while the gate
    # that covers it never runs, and the breakage surfaces on main instead.
    pairs, errs = gated_inputs()
    f.extend(errs)
    for label, files in pairs:
        for rel in sorted(files):
            if not covered_by(rel, watched):
                f.append(
                    f"{rel} is read by {label} but no env.WATCHED_PATHS entry covers it: a "
                    f"pull request touching only that file would short-circuit `selftest` to "
                    f"green, and the gate that reads it would not run until the change was "
                    f"already on main"
                )

    want = {as_push_glob(w) for w in watched} | set(PUSH_ONLY)
    got = set((on.get("push") or {}).get("paths") or [])
    for miss in sorted(want - got):
        f.append(
            f"on.push.paths is missing {miss!r}: a push touching only that path starts no "
            f"run at all, so nothing tests it"
        )
    for stray in sorted(got - want):
        f.append(
            f"on.push.paths carries {stray!r}, which is neither an env.WATCHED_PATHS entry "
            f"nor one of {list(PUSH_ONLY)}: the two lists have drifted"
        )

    # ── the required job must always run, and decide scope inside itself ────────────
    job = jobs.get(REQUIRED_JOB)
    if job is None:
        f.append(f"job {REQUIRED_JOB!r} is missing: that is the required status context")
        return f
    if job.get("if"):
        f.append(
            f"job {REQUIRED_JOB!r} carries a job-level if: a skipped job reports NO check, "
            f"which blocks the pull request exactly as a filtered trigger does"
        )
    steps = steps_of(job)
    if not steps:
        f.append(f"job {REQUIRED_JOB!r} has no steps")
        return f

    scope = steps[0]
    if scope.get("id") != "scope":
        f.append(
            f"the first step of {REQUIRED_JOB!r} is not the scope decision (id: scope); it is "
            f"{step_label(scope, 0)}"
        )
    if scope.get("if"):
        f.append(
            "the scope step is itself gated: it must run unconditionally, or every later step "
            "reads an empty verdict and skips, and no gate is ever executed"
        )
    script = scope.get("run") or ""
    for marker, what in FAILSAFE:
        if marker not in script:
            f.append(f"the scope step has lost {what} (no {marker!r}): it must fail OPEN")
    if script.count("decide false") != 1:
        f.append(
            f"the scope step has {script.count('decide false')} short-circuit branches; "
            f"exactly one is allowed, the genuinely-irrelevant case. Every other branch, "
            f"including each error path, must decide RELEVANT"
        )
    if "/pulls/" not in script or "/files" not in script:
        f.append(
            "the scope step does not read the pull request's files from the pulls/<n>/files "
            "endpoint: that endpoint diffs from the MERGE BASE, and judging the tip commit "
            "alone misses files the branch changed earlier"
        )
    # The jq fragment itself, not the bare word: the word also appears in the comment above
    # it, and a clause that a comment can satisfy is a clause that cannot fail.
    if "(.previous_filename // empty)" not in script:
        f.append(
            "the scope step does not emit `(.previous_filename // empty)` from its jq: for a "
            "renamed file the endpoint reports only the NEW path in `filename`, so a move OUT "
            "of a watched path scores NOT RELEVANT and the gate that reads the old path reds "
            "main instead"
        )

    for i, step in enumerate(steps[1:], start=1):
        if GUARD not in str(step.get("if") or ""):
            f.append(
                f"step {step_label(step, i)} of {REQUIRED_JOB!r} is not gated on the scope "
                f"verdict: it would run on every irrelevant pull request, and a failure in it "
                f"would turn the required check red for a change that cannot affect the suite"
            )

    # ── the job that exercises action.yml is never scope-gated ─────────────────────
    action = jobs.get(ACTION_JOB)
    if action is None:
        f.append(f"job {ACTION_JOB!r} is missing: nothing would execute action.yml")
    else:
        if action.get("if"):
            f.append(f"job {ACTION_JOB!r} carries a job-level if: action.yml would go untested")
        if any(s.get("id") == "scope" for s in steps_of(action)):
            f.append(
                f"job {ACTION_JOB!r} has a scope step again: it is not a required context so it "
                f"cannot deadlock anything, and gating it means an action.yml-only change can "
                f"merge with the composite Action never once executed"
            )
        if "steps.scope" in yaml.safe_dump(action):
            f.append(
                f"job {ACTION_JOB!r} references steps.scope but has no scope step: the reference "
                f"is always empty, so every step skips silently and action.yml is never executed"
            )
    return f


def selftest():
    ok = True

    def case(tag, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'OK  ' if cond else 'FAIL'} {tag}" + (f" — {detail}" if detail else ""))

    doc = load()
    base = check(doc)
    case("real conformance.yml: 0 findings", not base, "; ".join(base)[:400])

    m = copy.deepcopy(doc)
    triggers(m)["pull_request"] = {"paths": ["conformance/**"]}
    case("mutant: a paths filter back on pull_request -> red",
         any("on.pull_request.paths is set" in x for x in check(m)))

    m = copy.deepcopy(doc)
    triggers(m)["pull_request"] = {"paths-ignore": ["docs/**"]}
    case("mutant: a paths-ignore filter on pull_request -> red",
         any("on.pull_request.paths-ignore is set" in x for x in check(m)))

    m = copy.deepcopy(doc)
    m["jobs"][REQUIRED_JOB]["if"] = "github.event_name != 'pull_request'"
    case("mutant: a job-level if on the required job -> red",
         any("job-level if" in x for x in check(m)))

    m = copy.deepcopy(doc)
    del m["jobs"][REQUIRED_JOB]["steps"][-1]["if"]
    case("mutant: one ungated step in the required job -> red",
         any("is not gated on the scope verdict" in x for x in check(m)))

    m = copy.deepcopy(doc)
    m["jobs"][REQUIRED_JOB]["steps"][0]["if"] = GUARD
    case("mutant: the scope step itself gated -> red",
         any("scope step is itself gated" in x for x in check(m)))

    m = copy.deepcopy(doc)
    triggers(m)["push"]["paths"] = [p for p in triggers(m)["push"]["paths"] if p != "public/**"]
    case("mutant: a watched path dropped from on.push.paths -> red",
         any("is missing 'public/**'" in x for x in check(m)))

    m = copy.deepcopy(doc)
    stray = "scripts/**"
    assert stray not in triggers(m)["push"]["paths"], "pick a value that is genuinely absent"
    triggers(m)["push"]["paths"] = list(triggers(m)["push"]["paths"]) + [stray]
    case("mutant: a stray entry in on.push.paths -> red",
         any("the two lists have drifted" in x for x in check(m)))

    m = copy.deepcopy(doc)
    triggers(m)["push"]["paths"] = [p for p in triggers(m)["push"]["paths"] if p not in PUSH_ONLY]
    case("mutant: action.yml dropped from on.push.paths -> red",
         any("is missing 'action.yml'" in x for x in check(m)))

    m = copy.deepcopy(doc)
    m["env"]["WATCHED_PATHS"] = m["env"]["WATCHED_PATHS"] + "action.yml\n"
    case("mutant: action.yml added to env.WATCHED_PATHS -> red",
         any("env.WATCHED_PATHS lists 'action.yml'" in x for x in check(m)))

    # the coverage clause: a root document a gate reads, dropped from the watched set
    m = copy.deepcopy(doc)
    m["env"]["WATCHED_PATHS"] = "\n".join(
        ln for ln in m["env"]["WATCHED_PATHS"].split("\n") if ln.strip() != "WORKSTREAMS.md")
    case("mutant: WORKSTREAMS.md dropped from the watched set -> red",
         any("WORKSTREAMS.md is read by validate_steward_docs.DOCS" in x for x in check(m)))

    m = copy.deepcopy(doc)
    m["env"]["WATCHED_PATHS"] = "\n".join(
        ln for ln in m["env"]["WATCHED_PATHS"].split("\n") if ln.strip() != "README.md")
    case("mutant: README.md dropped from the watched set -> red",
         any("README.md is read by site_gates.DOC_FILES" in x for x in check(m)))

    m = copy.deepcopy(doc)
    m["env"]["WATCHED_PATHS"] = "\n".join(
        ln for ln in m["env"]["WATCHED_PATHS"].split("\n") if ln.strip() != "docs/")
    case("mutant: docs/ dropped from the watched set -> red",
         any("docs/archive/ROADMAP.md is read by" in x for x in check(m)))

    # the two gates the FIRST version of this file could not see: a Path constant and a glob
    m = copy.deepcopy(doc)
    m["env"]["WATCHED_PATHS"] = "\n".join(
        ln for ln in m["env"]["WATCHED_PATHS"].split("\n") if ln.strip() != ".github/")
    red = check(m)
    case("mutant: .github/ dropped -> red naming nightly.yml (validate_nightly_workflow.NIGHTLY)",
         any(".github/workflows/nightly.yml is read by validate_nightly_workflow.NIGHTLY" in x
             for x in red))
    case("mutant: .github/ dropped -> red naming release.yml (validate_ports_registry.SCAN_GLOBS)",
         any(".github/workflows/release.yml is read by validate_ports_registry.SCAN_GLOBS" in x
             for x in red))

    # narrowing .github/ to only this file is the exact pre-2026-10-01 hole
    m = copy.deepcopy(doc)
    m["env"]["WATCHED_PATHS"] = m["env"]["WATCHED_PATHS"].replace(
        ".github/\n", ".github/workflows/conformance.yml\n")
    case("mutant: .github/ narrowed back to just conformance.yml -> red",
         any("nightly.yml is read by" in x for x in check(m)))

    saved_src = GATE_INPUT_SOURCES
    try:
        globals()["GATE_INPUT_SOURCES"] = (("validate_ports_registry", "SCAN_GLOBS", "globs"),)
        import validate_ports_registry as _vpr  # noqa: PLC0415 - mutation needs the module
        saved_globs = _vpr.SCAN_GLOBS
        try:
            _vpr.SCAN_GLOBS = ("no/such/dir/*.py",)
            case("mutant: a gate input list that resolves to nothing -> red (never vacuous)",
                 any("resolved to NO files" in x for x in check(copy.deepcopy(doc))))
        finally:
            _vpr.SCAN_GLOBS = saved_globs
    finally:
        globals()["GATE_INPUT_SOURCES"] = saved_src

    m = copy.deepcopy(doc)
    s = m["jobs"][REQUIRED_JOB]["steps"][0]
    s["run"] = s["run"].replace(".filename, (.previous_filename // empty)", ".filename")
    case("mutant: scope reads only .filename, so a rename out of a watched path hides -> red",
         any("previous_filename" in x for x in check(m)))

    saved = GATE_INPUT_SOURCES
    try:
        globals()["GATE_INPUT_SOURCES"] = (("no_such_gate_module", "DOCS", "paths"),)
        case("mutant: a gate input list that cannot be imported -> red (never vacuous)",
             any("would pass vacuously" in x for x in check(copy.deepcopy(doc))))
    finally:
        globals()["GATE_INPUT_SOURCES"] = saved

    m = copy.deepcopy(doc)
    s = m["jobs"][REQUIRED_JOB]["steps"][0]
    s["run"] = s["run"].replace(
        'say "SCOPE: changed-file list unavailable -> failing SAFE to RELEVANT: the full self-test suite RUNS."',
        'say "SCOPE: changed-file list unavailable -> short-circuiting."',
    ).replace("            decide true\n            say \"SCOPE: changed-file list unavailable",
              "            decide false\n            say \"SCOPE: changed-file list unavailable")
    case("mutant: the unreadable-file-list branch flipped to short-circuit -> red",
         any("fail OPEN" in x or "short-circuit branches" in x for x in check(m)))

    m = copy.deepcopy(doc)
    s = m["jobs"][REQUIRED_JOB]["steps"][0]
    s["run"] = s["run"].replace('"repos/${{ github.repository }}/pulls/${{ github.event.pull_request.number }}/files"',
                                '"repos/${{ github.repository }}/commits/${{ github.sha }}"')
    case("mutant: scope judged on the tip commit instead of the merge base -> red",
         any("MERGE BASE" in x for x in check(m)))

    m = copy.deepcopy(doc)
    m["jobs"][ACTION_JOB]["steps"][-1]["if"] = f"always() && {GUARD}"
    case("mutant: a scope reference back in the action job -> red",
         any("references steps.scope" in x for x in check(m)))

    m = copy.deepcopy(doc)
    m["jobs"][REQUIRED_JOB]["steps"][0]["id"] = "scopecheck"
    case("mutant: the scope step loses its id -> red",
         any("is not the scope decision" in x for x in check(m)))

    print("conformance-workflow selftest: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="run the kill-tests on mutated copies")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    f = check(load())
    for x in f:
        print(f"  x {x}")
    doc = load()
    n = len(steps_of(doc["jobs"].get(REQUIRED_JOB, {})))
    print(f"conformance-workflow: {'PASS' if not f else 'FAIL'} "
          f"({REQUIRED_JOB} {n} steps, {len(watched_paths(doc))} watched paths)")
    return 0 if not f else 1


if __name__ == "__main__":
    sys.exit(main())
