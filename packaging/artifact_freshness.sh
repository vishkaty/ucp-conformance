#!/usr/bin/env bash
# artifact_freshness.sh — is every GENERATED artifact identical to what is COMMITTED?
# Extracted from preflight.sh step 2 so the comparison can be kill-tested; a gate
# that cannot be shown to fail proves nothing (cases 5-14 of
# packaging/test_preflight_guards.sh).
# Usage: bash packaging/artifact_freshness.sh [--regenerate] [path ...]
#   exit 0 = every artifact matches HEAD · 1 = unproven or stale (reason on stdout)
# Explicit paths replace the default set, so CI can reuse this comparison for the
# bundle alone (regenerating the coverage matrix there needs the vendored spec
# sources, which a workflow checkout does not carry).
set -uo pipefail
# resolve the repo root rather than trusting the caller's cwd: every path below is
# repo-relative, so from a subdirectory the pathspecs match nothing and the gate
# would exit 0 on a stale tree. preflight.sh happens to cd first; nothing enforces it.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
PATHS=(public/coverage.json docs/spec-coverage-matrix.md packaging/spck_conformance/_bundle)
rc=0
regen=0
if [ "${1:-}" = "--regenerate" ]; then regen=1; shift; fi
# drop empty arguments: a caller passing "" must not narrow the watched set to
# nothing, which would make the gate pass by having no artifact to compare.
sel=()
for a in "$@"; do
  # an unrecognised flag must not fall through and be used as a pathspec: it would
  # match nothing and the gate would exit 0 having compared no artifact at all.
  case "$a" in -*) echo "artifact_freshness: unknown option $a" >&2; exit 2 ;; esac
  [ -n "$a" ] && sel+=("$a")
done
if [ "${#sel[@]}" -gt 0 ]; then PATHS=("${sel[@]}"); fi

if [ "$regen" = "1" ]; then
  # a generator that CRASHED must not be indistinguishable from one that produced
  # identical output: the earlier `|| true` with output discarded made a failed
  # matrix.py read as "up to date".
  if ! python3 conformance/coverage/matrix.py --json public/coverage.json \
                --md docs/spec-coverage-matrix.md >/dev/null 2>&1; then
    echo "coverage/matrix.py FAILED to regenerate — freshness unproven"; rc=1
  fi
  if ! bash packaging/sync_bundle.sh >/dev/null 2>&1; then
    echo "packaging/sync_bundle.sh FAILED — freshness unproven"; rc=1
  fi
fi

# 1. content drift of tracked artifacts, against HEAD rather than the index.
# HEAD, not the index. conformance/ci/selftest.sh USED to re-sync the bundle and
# `git add` it (removed 2026-10-12, asserted by case 18), and preflight runs that as
# step 1, so the drift was already staged by the time this gate ran. `git diff` with
# no commit compares against the index, so it called the artifact fresh although it
# was absent from the commit a push would publish. HEAD stays the reference whatever
# else might write to the index.
if ! git diff --quiet HEAD -- "${PATHS[@]}" 2>/dev/null; then
  git --no-pager diff --stat HEAD -- "${PATHS[@]}" 2>/dev/null
  rc=1
fi

# 2. ADDITIONS. git diff never reports an untracked file, and merchant_checks.py
# glob-discovers merchant_checks_*.py at import time, so a new module that reached
# the bundle without being committed leaves the published wheel running fewer
# checks with nothing red anywhere.
untracked="$(git ls-files --others --exclude-standard -- "${PATHS[@]}" 2>/dev/null)"
if [ -n "$untracked" ]; then
  echo "untracked generated file(s) — present in the tree, absent from HEAD:"
  echo "$untracked" | sed 's/^/ /'
  rc=1
fi

exit $rc
