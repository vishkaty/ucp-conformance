#!/usr/bin/env bash
# artifact_freshness.sh — is every GENERATED artifact identical to what is COMMITTED?
# Extracted from preflight.sh step 2 so the comparison can be kill-tested; a gate
# that cannot be shown to fail proves nothing.
# Usage: bash packaging/artifact_freshness.sh [--regenerate]
#   exit 0 = every artifact matches · 1 = at least one differs (diffstat on stdout)
set -uo pipefail
PATHS=(public/coverage.json docs/spec-coverage-matrix.md packaging/spck_conformance/_bundle)
if [ "${1:-}" = "--regenerate" ]; then
  python3 conformance/coverage/matrix.py --json public/coverage.json \
          --md docs/spec-coverage-matrix.md >/dev/null 2>&1 || true
  bash packaging/sync_bundle.sh >/dev/null 2>&1 || true
fi
# HEAD, not the index. `git diff` with no commit compares the worktree against the
# INDEX, so a regenerated artifact that has been staged but not committed reports as
# fresh while it is still absent from the commit a push would publish. That is how a
# bundle missing three reporting fixes passed this step on 2026-10-12.
if git diff --quiet HEAD -- "${PATHS[@]}" 2>/dev/null; then exit 0; fi
git --no-pager diff --stat HEAD -- "${PATHS[@]}" 2>/dev/null
exit 1
