#!/usr/bin/env bash
# release_guards.sh — the RELEASE guards, runnable on their own (preflight.sh step 4 calls
# this; release.yml runs it before publishing; test_preflight_guards.sh kill-tests it on
# scratch trees). Runs against the tree at $PWD's repo root (cd there first).
#
#   bash packaging/release_guards.sh            # __version__ + wheel bundle currency
#   bash packaging/release_guards.sh v0.4.0rc1  # + pyproject version == tag (PEP 440 pre-release ok)
#
# Guards (each ✗ fails the run):
#   1. pyproject version == tag (when a tag is given)
#   2. spck_conformance.__version__ == pyproject version (D5-06)
#   3. the built wheel bundles EVERY spec version the engine knows — derived from
#      conformance/common/spec_versions.VERSIONS, never a hard-coded set (D5-06) — and ≥15 check modules
#   4. packaging/CHANGELOG.md carries an entry for the version (D5-15; add-only, rc entries too)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TAG="${1:-}"
FAIL=0
ok(){   printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad(){  printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }

PKG=$(python3 -c "import tomllib;print(tomllib.load(open('packaging/pyproject.toml','rb'))['project']['version'])")
if [ -n "$TAG" ]; then
  # 1a. tag shape: vX.Y.Z or a PEP 440 pre-release vX.Y.ZrcN (D5-21)
  if [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$ ]]; then ok "tag $TAG is vX.Y.Z or vX.Y.ZrcN"; else bad "tag $TAG is not vX.Y.Z / vX.Y.ZrcN"; fi
  [ "${TAG#v}" = "$PKG" ] && ok "pyproject version $PKG matches tag $TAG" || bad "tag $TAG != pyproject version $PKG (bump packaging/pyproject.toml)"
  # 1b. rc BEFORE final (decision 17): a final tag needs a recorded green rc acceptance in the CHANGELOG
  if [[ ! "$TAG" =~ rc[0-9]+$ ]]; then
    BASE="${TAG#v}"
    if [ -f packaging/CHANGELOG.md ] && awk -v t="$BASE" '
        /^## / { insec = ($0 ~ ("^## +v?" t "rc[0-9]+")) }
        insec && /^acceptance: *green/ { found=1 }
        END { exit found ? 0 : 1 }' packaging/CHANGELOG.md; then
      ok "final tag $TAG: a green rc acceptance is recorded in packaging/CHANGELOG.md"
    else
      bad "final tag $TAG refused: no green rc acceptance recorded under a '## ${BASE}rcN' heading in packaging/CHANGELOG.md (rc first — decision 17)"
    fi
  fi
fi
# 1c. provenance (D5-15): the tagged commit is an ancestor of origin/main, and SOME completed-
#     success `selftest` check-run exists for it (W1 carry-over: never newest-only). The CI half is REQUIRED in release.yml (RELEASE_GUARDS_REQUIRE_CI=1)
#     and best-effort locally (gh may be absent/offline → warning, never a silent pass in CI).
if [ -n "$TAG" ] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  [ "${DEPLOY_NO_FETCH:-0}" = "1" ] || git fetch -q origin main >/dev/null 2>&1 || true
  SHA="$(git rev-parse HEAD)"
  if git rev-parse --verify -q refs/remotes/origin/main >/dev/null; then
    if git merge-base --is-ancestor "$SHA" refs/remotes/origin/main; then ok "HEAD ${SHA:0:7} is an ancestor of origin/main"
    else bad "HEAD ${SHA:0:7} is NOT an ancestor of origin/main — tags are cut from merged, CI-verified main only"; fi
  else
    bad "no refs/remotes/origin/main to check ancestry against (fetch first; release.yml checks out with fetch-depth 0)"
  fi
  GH="${GH_BIN:-gh}"
  # ANY completed-success `selftest` run for the SHA counts (packaging/check_run_verdict.py): the
  # tag push races the run it triggers; a newer in-progress run must never mask a green one.
  CONCL="$($GH api "repos/${RELEASE_REPO:-vishkaty/ucp-conformance}/commits/$SHA/check-runs?per_page=100" 2>/dev/null | python3 packaging/check_run_verdict.py selftest 2>/dev/null | tail -1)"
  if [ "$CONCL" = "success" ]; then ok "a completed-success selftest check-run exists for ${SHA:0:7}"
  elif [ "${RELEASE_GUARDS_REQUIRE_CI:-0}" = "1" ]; then bad "no completed-success selftest check-run for ${SHA:0:7} (newest: '${CONCL:-absent}') — no release on a red or unverified commit"
  else printf "  \033[33m!\033[0m selftest check-run for %s could not be confirmed locally ('%s') — release.yml enforces it\n" "${SHA:0:7}" "${CONCL:-absent}"; fi
fi
if [ "${RELEASE_GUARDS_TAG_ONLY:-0}" = "1" ]; then
  [ $FAIL -eq 0 ] && { echo "release tag guards: PASS"; exit 0; } || { echo "release tag guards: FAIL"; exit 1; }
fi

# 2. __version__ guard
INITV=$(python3 -c "import re;print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('packaging/spck_conformance/__init__.py').read()).group(1))")
[ "$INITV" = "$PKG" ] && ok "spck_conformance.__version__ $INITV == pyproject $PKG" || bad "spck_conformance.__version__ $INITV != pyproject version $PKG (packaging/spck_conformance/__init__.py)"

# 3. wheel bundle currency — versions from the single source
WANT=$(python3 -c "import sys;sys.path.insert(0,'conformance');from common.spec_versions import VERSIONS;print(' '.join(VERSIONS))")
python3 -m pip install --quiet build >/dev/null 2>&1 || true
DIST="$(mktemp -d)"
if python3 -m build --outdir "$DIST" packaging >"$DIST/build.log" 2>&1; then
  python3 - "$DIST" "$WANT" <<'PY'
import glob, sys, zipfile
dist, want = sys.argv[1], set(sys.argv[2].split())
w = sorted(glob.glob(dist + '/*.whl'))[-1]
z = zipfile.ZipFile(w)
vs = sorted(set(n.split('/requirements/')[1].split('/')[0] for n in z.namelist() if '/requirements/' in n and n.count('/') > 4))
mods = len([n for n in z.namelist() if 'merchant_checks' in n and n.endswith('.py')])
missing = sorted(want - set(vs))
if not missing and mods >= 15:
    print(f"  \033[32m✓\033[0m wheel {w.split('/')[-1]} bundles versions {vs}, {mods} check modules")
    sys.exit(0)
print(f"  \033[31m✗\033[0m wheel bundle STALE: versions {vs} (missing {missing}), {mods} modules (run packaging/sync_bundle.sh)")
sys.exit(1)
PY
  [ $? -ne 0 ] && FAIL=1
else
  bad "wheel build failed — see $DIST/build.log"
fi

# 4. CHANGELOG entry (D5-15): a bump without an entry is not releasable
if [ -f packaging/CHANGELOG.md ]; then
  grep -qE "^## +\[?v?${PKG//./\\.}\]?( |$)" packaging/CHANGELOG.md \
    && ok "packaging/CHANGELOG.md has an entry for $PKG" \
    || bad "packaging/CHANGELOG.md has no '## $PKG' entry — every release (rc included) needs one"
fi

[ $FAIL -eq 0 ] && { echo "release guards: PASS"; exit 0; } || { echo "release guards: FAIL"; exit 1; }
