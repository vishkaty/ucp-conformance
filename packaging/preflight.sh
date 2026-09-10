#!/usr/bin/env bash
# preflight.sh — one command that proves a change is release-ready end to end.
#
# Run this before `git push` on anything substantive, and ALWAYS before cutting a
# PyPI release. It mechanically covers every surface that has to stay in sync — the
# things a human forgets: gates, coverage freshness, doc/site copy, the pip bundle,
# and (for a release) the wheel's bundle currency + version/tag match.
#
#   bash packaging/preflight.sh            # validate the working tree is shippable
#   bash packaging/preflight.sh v0.2.1     # + assert pyproject version == this tag
#
# Exit 0 = ready. Non-zero = a specific surface is stale/broken (message says which).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TAG="${1:-}"
GUARDS_ONLY=0
if [ "$TAG" = "--guards-only" ]; then GUARDS_ONLY=1; TAG="${2:-}"; fi   # release guards alone (fast; tests/CI)
FAIL=0
step(){ printf "\n\033[1m▶ %s\033[0m\n" "$1"; }
ok(){   printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad(){  printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }
# Surfaces something worth acting on without blocking a release. Used where the
# finding is real but not a property of the artifact being shipped.
warn(){ printf "  \033[33m!\033[0m %s\n" "$1"; }

if [ $GUARDS_ONLY -eq 0 ]; then
# 1. The full self-test: all gates (conformance + coverage/copy-freshness + responsive
#    web + citation soundness + bundle drift). This is the single source of truth.
step "Full self-test (all gates)"
if bash conformance/ci/selftest.sh >/tmp/preflight_selftest.log 2>&1; then
  ok "selftest GREEN — $(grep -oE '[0-9]+ passed' /tmp/preflight_selftest.log | tail -1)"
else
  bad "selftest RED — see /tmp/preflight_selftest.log (tail below)"; tail -6 /tmp/preflight_selftest.log
fi

# 2. Coverage artifacts + pip bundle must be committed-fresh (regenerate → no diff).
step "Coverage + bundle freshness (committed == generated)"
python3 conformance/coverage/matrix.py --json public/coverage.json --md docs/spec-coverage-matrix.md >/dev/null 2>&1 || true
bash packaging/sync_bundle.sh >/dev/null 2>&1 || true
if git diff --quiet -- public/coverage.json docs/spec-coverage-matrix.md packaging/spck_conformance/_bundle 2>/dev/null; then
  ok "coverage.json, spec-coverage-matrix.md, and the pip bundle are up to date"
else
  bad "regenerated artifacts differ from committed — commit these:"; git --no-pager diff --stat -- public/coverage.json docs/spec-coverage-matrix.md packaging/spck_conformance/_bundle 2>/dev/null | sed 's/^/    /'
fi
# the bundle must also be COMPLETE, not just current (D1-05 <- D5-06 pending item): every module
# merchant.py transitively imports + every data file it reads ships, and it imports in isolation
if python3 packaging/validate_bundle.py >/tmp/preflight_bundle.log 2>&1; then
  ok "$(tail -1 /tmp/preflight_bundle.log)"
else
  bad "pip bundle INCOMPLETE — see /tmp/preflight_bundle.log"; tail -4 /tmp/preflight_bundle.log | sed 's/^/    /'
fi
# the claims register's generated blocks (manifest + evidence split) are byte-synced with the engine (D5-05)
if python3 conformance/web/sync_site_claims.py --check >/tmp/preflight_claims_sync.log 2>&1; then
  ok "site_claims.json manifest + evidence.per_version in sync with the engine"
else
  bad "site_claims.json generated blocks drifted — run: python3 conformance/web/sync_site_claims.py --write"; tail -5 /tmp/preflight_claims_sync.log | sed 's/^/    /'
fi

# 3. Working tree clean (nothing uncommitted that a push would miss).
step "Working tree"
if [ -z "$(git status --porcelain 2>/dev/null)" ]; then ok "clean"; else bad "uncommitted changes:"; git --no-pager status --short | sed 's/^/    /'; fi

# 3a. PRIVATE baseline currency. ops/ is excluded from this public repo on purpose
# (unfiled issue drafts, outreach records, strategy) and lives in the private
# vishkaty/spck-ops repo instead. Excluded from here plus uncommitted there means
# backed up nowhere, which is how 4.7 MB of baseline came to sit on one disk. This
# surfaces drift; it never fails the build, because the private repo is a companion
# to a release rather than part of one.
step "Private ops baseline (vishkaty/spck-ops)"
if [ -d ops/.git ]; then
  ops_dirty="$(git -C ops status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  ops_ahead="$(git -C ops rev-list --count @{u}..HEAD 2>/dev/null || echo 0)"
  if [ "$ops_dirty" = "0" ] && [ "$ops_ahead" = "0" ]; then
    ok "ops/ committed and pushed"
  else
    warn "ops/ has $ops_dirty uncommitted file(s) and $ops_ahead unpushed commit(s) — run ops/sync.sh"
  fi
else
  warn "ops/ is not a git repo — the private baseline is unversioned"
fi

# 3b. Drift tripwire (D2-03): a pinned spec TAG whose live identity (tag object +
#     dereferenced commit) differs from the lock FAILS preflight (rc 1) unless the move
#     is acknowledged and unexpired in conformance/ci/known_tag_moves.json (D2-17);
#     gh missing / offline FAILS too (rc 2) — never a silent skip. Branch staleness and
#     release-branch drift stay informational lines inside the same output.
step "Source pin identity + freshness"
if python3 conformance/ci/sources_age.py --check 2>/dev/null | sed 's/^/  /'; [ "${PIPESTATUS[0]}" -eq 0 ]; then
  ok "pinned spec tags carry their locked identity (moves acknowledged or none)"
else
  bad "sources_age --check failed (tag moved unacknowledged, or gh/offline) — see above"
fi
fi   # GUARDS_ONLY

# 4. Release guards (packaging/release_guards.sh — also run by release.yml and kill-tested
#    by packaging/test_preflight_guards.sh): __version__ == pyproject, the wheel bundles every
#    spec_versions.VERSIONS entry, CHANGELOG entry; plus version == tag when one is given.
step "Release guards${TAG:+ for $TAG}"
bash packaging/release_guards.sh "$TAG" || FAIL=1

# 4b. The guards must be ABLE to fail: hermetic kill-tests (scratch trees, repo untouched).
step "Release guard kill-tests"
bash packaging/test_preflight_guards.sh >/tmp/preflight_guard_tests.log 2>&1 && ok "release guards kill-tests PASS" || { bad "release guards kill-tests RED — see /tmp/preflight_guard_tests.log"; tail -4 /tmp/preflight_guard_tests.log; }
bash packaging/test_release_guards.sh >/tmp/preflight_tagguard_tests.log 2>&1 && ok "release tag-guard kill-tests PASS" || { bad "release tag-guard kill-tests RED — see /tmp/preflight_tagguard_tests.log"; tail -4 /tmp/preflight_tagguard_tests.log; }

echo
if [ "$FAIL" -eq 0 ]; then
  printf "\033[1;32mPREFLIGHT PASS — shippable.\033[0m\n"
  if [ -n "$TAG" ]; then
    echo "Release: git tag $TAG && git push origin $TAG   (release.yml publishes via OIDC)"
  else
    echo "Deploy site: bash packaging/deploy.sh   (the ONLY sanctioned path — six guarded steps, preview before main; --dry-run first)"
  fi
  exit 0
else
  printf "\033[1;31mPREFLIGHT FAIL — fix the ✗ items above before shipping.\033[0m\n"; exit 1
fi
