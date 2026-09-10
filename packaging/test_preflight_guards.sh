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

echo
if [ $FAIL -eq 0 ]; then echo "release guards kill-tests: PASS"; exit 0; else echo "release guards kill-tests: FAIL"; exit 1; fi
