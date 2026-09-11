#!/usr/bin/env python3
"""
check_run_verdict.py — ONE reading of a commit's `selftest` check-run history, shared by
packaging/deploy.sh (step 4) and packaging/release_guards.sh (guard 1c).

    gh api "repos/<owner>/<repo>/commits/<sha>/check-runs?per_page=100" | python3 packaging/check_run_verdict.py [JOB]

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
    job = argv[1] if len(argv) > 1 else "selftest"
    try:
        doc = json.load(sys.stdin)
    except (ValueError, OSError):
        doc = None
    print(verdict(doc, job))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
