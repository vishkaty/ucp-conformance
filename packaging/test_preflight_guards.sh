#!/usr/bin/env bash
# test_preflight_guards.sh — hermetic kill-tests for the RELEASE guards (D5-06 / D5-15):
# a guard that cannot fail proves nothing. Each case builds a SCRATCH copy of the
# packaging tree (the repo is never touched), plants one defect, and asserts the guard
# reds with a line naming it.
#
#   1. wheel bundle missing requirements/2026-08-25  -> release_guards.sh ✗ (versions derived
#      from conformance/common/spec_versions.VERSIONS, never a hard-coded set)
#   2. __version__ != pyproject                       -> release_guards.sh ✗
#   3. pypi report-only job expiry is MECHANICAL: pypi_job_mode.py says report-only below
#      0.4.0 (incl. 0.4.0rc1) and counted at >= 0.4.0
#   4. honest tree                                    -> release_guards.sh PASS
#   5-7. artifact freshness: staged-but-uncommitted / unstaged / honest (see the block below)
# Usage: bash packaging/test_preflight_guards.sh   (exit 0 pass · 1 fail)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0
ok(){  printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad(){ printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }

scratch() {   # a minimal copy: packaging/ (with the bundle) + conformance/common (VERSIONS)
  local d; d="$(mktemp -d)"
  mkdir -p "$d/conformance/common"
  cp -R "$ROOT/packaging" "$d/packaging"
  cp "$ROOT"/conformance/common/*.py "$d/conformance/common/"
  cp "$ROOT/conformance/SOURCES.lock.json" "$d/conformance/" 2>/dev/null || true
  rm -rf "$d/packaging/build" "$d/packaging"/*.egg-info
  echo "$d"
}
guards() { ( cd "$1" && bash packaging/release_guards.sh "${2:-}" 2>&1 ); }

echo "release-guards kill-tests"
# 1. wheel missing a spec version the engine ships
D="$(scratch)"; rm -rf "$D/packaging/spck_conformance/_bundle/conformance/requirements/2026-08-25"
OUT="$(guards "$D" v0.3.1)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "2026-08-25"; then ok "wheel missing requirements/2026-08-25 -> guard ✗ naming it"; else bad "wheel missing 2026-08-25 not caught (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# 2. __version__ drift
D="$(scratch)"; sed -i.bak 's/^__version__ = .*/__version__ = "0.0.0"/' "$D/packaging/spck_conformance/__init__.py"
OUT="$(guards "$D" v0.3.1)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "__version__"; then ok "__version__ 0.0.0 != pyproject -> guard ✗"; else bad "__version__ drift not caught (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# 3. mechanical expiry of the report-only pypi job
for pair in "0.3.1:report-only" "0.4.0rc1:report-only" "0.4.0:counted" "0.4.1:counted" "1.0.0:counted"; do
  v="${pair%%:*}"; want="${pair##*:}"
  got="$(python3 "$ROOT/packaging/pypi_job_mode.py" --version "$v" 2>/dev/null)"
  [ "$got" = "$want" ] && ok "pypi job at $v -> $want" || bad "pypi job at $v -> '$got' (want $want)"
done

# 4. honest tree passes (tag = the current pyproject version)
D="$(scratch)"; PKG="$(python3 -c "import tomllib;print(tomllib.load(open('$ROOT/packaging/pyproject.toml','rb'))['project']['version'])")"
OUT="$(guards "$D" "v$PKG")"; RC=$?
if [ $RC -eq 0 ]; then ok "honest tree at v$PKG -> guards PASS"; else bad "honest tree red (rc=$RC):"; echo "$OUT" | tail -6; fi
rm -rf "$D"

# ---------------------------------------------------------------------------
# artifact-freshness kill-tests (2026-10-12). The step is headed
# "committed == generated", so it must compare the worktree against HEAD and it
# must see additions. It compared against the INDEX, which made a regenerated
# artifact that conformance/ci/selftest.sh had already staged read as fresh: on
# the 2026-10-12 run it reported the pip bundle up to date while the committed
# bundle was missing three fixes. Cases 8-14 exist because an adversarial pass
# found six surviving mutants of the first version of this block.
# ---------------------------------------------------------------------------
freshrepo() {   # a scratch repo holding the three generated paths + stub generators
  local d; d="$(mktemp -d)"
  mkdir -p "$d/public" "$d/docs" "$d/conformance/coverage" \
           "$d/packaging/spck_conformance/_bundle/conformance/checks" \
           "$d/packaging/spck_conformance/_bundle/conformance/requirements"
  printf '{"v":1}\n'  > "$d/public/coverage.json"
  printf 'matrix v1\n' > "$d/docs/spec-coverage-matrix.md"
  printf 'old = 1\n'   > "$d/packaging/spck_conformance/_bundle/conformance/checks/m.py"
  printf 'ROW-1\n'     > "$d/packaging/spck_conformance/_bundle/conformance/requirements/r.txt"
  printf 'unrelated\n' > "$d/README.md"
  cp "$ROOT/packaging/artifact_freshness.sh" "$d/packaging/"
  # stub generators: --regenerate must call these two exact paths
  printf '#!/usr/bin/env bash\nexit 0\n' > "$d/packaging/sync_bundle.sh"
  printf 'import sys\nsys.exit(0)\n'     > "$d/conformance/coverage/matrix.py"
  ( cd "$d" && git init -q && git add -A \
      && git -c user.name=t -c user.email=t@t commit -q -m generated ) >/dev/null 2>&1
  echo "$d"
}
fresh()  { if [ -n "${2:-}" ]; then ( cd "$1" && bash packaging/artifact_freshness.sh "$2" 2>&1 );
           else ( cd "$1" && bash packaging/artifact_freshness.sh 2>&1 ); fi; }

echo "artifact-freshness kill-tests"

# 5. the defect that bit us: regenerated, STAGED by selftest.sh, not committed
D="$(freshrepo)"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
( cd "$D" && git add packaging/spck_conformance/_bundle ) >/dev/null 2>&1
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "m.py"; then ok "staged-but-uncommitted bundle -> ✗ naming it"; else bad "STAGED stale bundle read as FRESH (rc=$RC) — index compared, not HEAD:"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 6. unstaged drift still reds
D="$(freshrepo)"; printf 'matrix v2\n' > "$D/docs/spec-coverage-matrix.md"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "spec-coverage-matrix.md"; then ok "unstaged matrix drift -> ✗ naming it"; else bad "unstaged drift not caught (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 7. an honest tree passes, so the guard is not simply always-red
D="$(freshrepo)"; OUT="$(fresh "$D")"; RC=$?
if [ $RC -eq 0 ]; then ok "honest tree -> PASS"; else bad "honest tree red (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 8. ADDITIONS: git diff never reports an untracked file, and merchant_checks.py
# glob-discovers merchant_checks_*.py at runtime, so a new module that reaches the
# bundle uncommitted makes the shipped wheel run FEWER checks, silently.
D="$(freshrepo)"
printf 'new = 1\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/merchant_checks_99_zz.py"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "merchant_checks_99_zz.py"; then ok "untracked new bundle module -> ✗ naming it"; else bad "untracked ADDITION read as fresh (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 9. coverage.json must be in the watched set (kills: drop it from PATHS)
D="$(freshrepo)"; printf '{"v":2}\n' > "$D/public/coverage.json"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "coverage.json"; then ok "coverage.json drift -> ✗ naming it"; else bad "coverage.json unwatched (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 10. the WHOLE bundle is watched, not just checks/ (kills: narrow the pathspec)
D="$(freshrepo)"; printf 'ROW-2\n' > "$D/packaging/spck_conformance/_bundle/conformance/requirements/r.txt"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "r.txt"; then ok "bundle requirements/ drift -> ✗ naming it"; else bad "bundle subtree unwatched (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 11. it speaks ONLY about generated artifacts (kills: drop the pathspec entirely)
D="$(freshrepo)"; printf 'edited\n' > "$D/README.md"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -eq 0 ]; then ok "unrelated file drift -> PASS (scoped to generated paths)"; else bad "unrelated drift reds the gate (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 12. --regenerate actually regenerates (kills: delete or rename the block)
D="$(freshrepo)"
printf '#!/usr/bin/env bash\nprintf "old = 1\\n" > packaging/spck_conformance/_bundle/conformance/checks/m.py\nexit 0\n' > "$D/packaging/sync_bundle.sh"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
OUT="$(fresh "$D" --regenerate)"; RC=$?
if [ $RC -eq 0 ]; then ok "--regenerate restored the artifact -> PASS"; else bad "--regenerate did not run the generator (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 13. a FAILED generator must not read as fresh (kills: swallow it with || true)
D="$(freshrepo)"
printf '#!/usr/bin/env bash\nexit 3\n' > "$D/packaging/sync_bundle.sh"
OUT="$(fresh "$D" --regenerate)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "sync_bundle"; then ok "generator exit 3 -> ✗ naming it"; else bad "FAILED generator read as fresh (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 14. same verdict from a subdirectory (kills: relative paths with no cd to root)
D="$(freshrepo)"; printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
OUT="$( cd "$D/docs" && bash ../packaging/artifact_freshness.sh 2>&1 )"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "m.py"; then ok "run from a subdirectory -> ✗ naming it"; else bad "fails GREEN from a subdirectory (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 15. explicit paths replace the default set (what CI passes). Drift inside the
# named path reds; drift outside it does not, so the CI step keeps its own scope.
D="$(freshrepo)"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
printf '{"v":9}\n' > "$D/public/coverage.json"
OUT="$( cd "$D" && bash packaging/artifact_freshness.sh packaging/spck_conformance/_bundle 2>&1 )"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "m.py" && ! echo "$OUT" | grep -q "coverage.json"; then ok "explicit path scopes the gate -> ✗ bundle only"; else bad "explicit path ignored (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 16. an EMPTY path argument must not blank the watched set (it would make the
# gate pass by having nothing to compare). Found by case 5-11 going red when the
# explicit-paths mode of case 15 first treated "" as a selection.
D="$(freshrepo)"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
OUT="$( cd "$D" && bash packaging/artifact_freshness.sh "" 2>&1 )"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "m.py"; then ok "empty path argument -> ✗ default set kept"; else bad "empty path argument blanked the gate (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 17. a MISTYPED flag must not fall through as a pathspec matching nothing, which
# would exit 0 having compared no artifact (how `rename-regen` hid from case 12).
D="$(freshrepo)"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
OUT="$( cd "$D" && bash packaging/artifact_freshness.sh --regenerte 2>&1 )"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "unknown option"; then ok "mistyped flag -> ✗ rejected"; else bad "mistyped flag accepted as a pathspec (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

echo
if [ $FAIL -eq 0 ]; then echo "release guards kill-tests: PASS"; exit 0; else echo "release guards kill-tests: FAIL"; exit 1; fi
