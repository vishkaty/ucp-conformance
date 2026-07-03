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
FAIL=0
step(){ printf "\n\033[1m▶ %s\033[0m\n" "$1"; }
ok(){   printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad(){  printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }

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

# 3. Working tree clean (nothing uncommitted that a push would miss).
step "Working tree"
if [ -z "$(git status --porcelain 2>/dev/null)" ]; then ok "clean"; else bad "uncommitted changes:"; git --no-pager status --short | sed 's/^/    /'; fi

# 4. Release-only checks (when a tag is supplied).
if [ -n "$TAG" ]; then
  step "Release: version + wheel bundle currency for $TAG"
  PKG=$(python3 -c "import tomllib;print(tomllib.load(open('packaging/pyproject.toml','rb'))['project']['version'])")
  [ "${TAG#v}" = "$PKG" ] && ok "pyproject version $PKG matches tag $TAG" || bad "tag $TAG != pyproject version $PKG (bump packaging/pyproject.toml)"
  python3 -m pip install --quiet build >/dev/null 2>&1 || true
  rm -rf /tmp/preflight_dist
  if python3 -m build --outdir /tmp/preflight_dist packaging >/tmp/preflight_build.log 2>&1; then
    python3 - <<PY
import glob, zipfile, sys
w=sorted(glob.glob('/tmp/preflight_dist/*.whl'))[-1]
z=zipfile.ZipFile(w)
vs=sorted(set(n.split('/requirements/')[1].split('/')[0] for n in z.namelist() if '/requirements/' in n and n.count('/')>4))
mods=len([n for n in z.namelist() if 'merchant_checks' in n and n.endswith('.py')])
want={'2026-01-11','2026-01-23','2026-04-08'}
print(f"  \033[32m✓\033[0m wheel {w.split('/')[-1]} bundles versions {vs}, {mods} check modules" if set(vs)>=want and mods>=15
      else f"  \033[31m✗\033[0m wheel bundle STALE: versions {vs}, {mods} modules (run packaging/sync_bundle.sh)")
sys.exit(0 if set(vs)>=want and mods>=15 else 1)
PY
    [ $? -ne 0 ] && FAIL=1
  else
    bad "wheel build failed — see /tmp/preflight_build.log"
  fi
fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf "\033[1;32mPREFLIGHT PASS — shippable.\033[0m\n"
  if [ -n "$TAG" ]; then
    echo "Release: git tag $TAG && git push origin $TAG   (release.yml publishes via OIDC)"
  else
    echo "Deploy site: CLOUDFLARE_API_TOKEN=\$(…katyal-secret get cloudflare.api-token) \\"
    echo "  npx wrangler pages deploy public --project-name=ucp-conformance --branch=main"
  fi
  exit 0
else
  printf "\033[1;31mPREFLIGHT FAIL — fix the ✗ items above before shipping.\033[0m\n"; exit 1
fi
