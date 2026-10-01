#!/usr/bin/env python3
"""
check_run_verdict.py — TWO readings of a commit's CI history, shared by packaging/deploy.sh
(step 4) and packaging/release_guards.sh (guard 1c). Both are pure functions of their stdin.

    gh api "repos/<owner>/<repo>/commits/<sha>/check-runs?per_page=100" | python3 packaging/check_run_verdict.py [JOB]
    gh api "repos/<owner>/<repo>/actions/runs?head_sha=<sha>"           | python3 packaging/check_run_verdict.py --suite-ran

The first asks DID THE REQUIRED CHECK PASS. The second asks DID THE SUITE ACTUALLY RUN, which
stopped being the same question on 2026-10-01 when the `selftest` job started short-circuiting
irrelevant pull requests. Callers must require both.

Reads the GitHub check-runs JSON on stdin and prints ONE word:
  · `success`  — ANY check-run named JOB (default `selftest`) is `completed` with conclusion
                 `success`. A push that races a deploy or a tag triggers a NEW run for the same
                 SHA; that newer in-progress run must never mask a green one (W0-integration
                 §11: refused twice on timing with the old newest-only reading).
  · otherwise  — the NEWEST JOB run's state (its conclusion when completed, else its status,
                 e.g. `in_progress`/`queued`/`failure`), or `absent` when no such run exists
                 or the input is not check-runs JSON. A history with no completed-success run
                 is therefore still refused, whatever else it contains.
Never exits non-zero on bad input — the callers compare the word against `success`.
Kill-tested by packaging/test_deploy_guards.sh and packaging/test_release_guards.sh.
"""
import json
import sys


def suite_ran(doc, job_unused=None):
    """Did the SUITE actually EXECUTE for this SHA, or only report?

    Reads `actions/runs?head_sha=<sha>` JSON (NOT check-runs) and prints `ran` or the reason
    it cannot say so. Needed since 2026-10-01: the `selftest` job now decides scope inside
    itself and SHORT-CIRCUITS a pull request whose changed files cannot affect the suite,
    reporting a green required check having executed no gate. `verdict()` above cannot tell
    those apart, because a short-circuited run is `completed`/`success` like any other.

    Non-pull_request events are never short-circuited (the scope step decides RELEVANT for
    push, schedule and workflow_dispatch unconditionally), so a successful run for this SHA
    whose event is not `pull_request` is positive proof the suite ran. A UI merge mints a new
    SHA and gets exactly such a push run; a command-line fast-forward of main to a branch head
    does not, which is the case this closes.
    """
    runs = doc.get("workflow_runs") if isinstance(doc, dict) else None
    if not isinstance(runs, list):
        return "absent"
    mine = [r for r in runs if isinstance(r, dict)]
    if not mine:
        return "absent"
    if any(r.get("status") == "completed" and r.get("conclusion") == "success"
           and r.get("event") != "pull_request" for r in mine):
        return "ran"
    if any(r.get("status") == "completed" and r.get("conclusion") == "success" for r in mine):
        return "pull-request-only"
    return "no-successful-run"


def verdict(doc, job="selftest"):
    runs = doc.get("check_runs") if isinstance(doc, dict) else None
    if not isinstance(runs, list):
        return "absent"
    mine = [r for r in runs if isinstance(r, dict) and r.get("name") == job]
    if not mine:
        return "absent"
    if any(r.get("status") == "completed" and r.get("conclusion") == "success" for r in mine):
        return "success"
    newest = mine[0]                     # GitHub lists check-runs newest first
    return str(newest.get("conclusion") or newest.get("status") or "absent")


def main(argv):
    args = argv[1:]
    mode = suite_ran if "--suite-ran" in args else verdict
    rest = [a for a in args if not a.startswith("--")]
    job = rest[0] if rest else "selftest"
    try:
        doc = json.load(sys.stdin)
    except (ValueError, OSError):
        doc = None
    print(mode(doc, job))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
