#!/usr/bin/env bash
# deploy.sh — THE ONLY SANCTIONED PATH that changes spck.dev (PLAN-v3 §2.18, D5-08).
# Six ordered steps; any refusal exits 3 and nothing is deployed. Never run by CI; the
# Cloudflare token is read only at step 5 on the owner's machine.
#
#   bash packaging/deploy.sh             # full run: steps 1–6
#   bash packaging/deploy.sh --dry-run   # steps 1–4 for real, then prints what 5–6 would do
#   bash packaging/deploy.sh --selftest  # hermetic kill-tests (packaging/test_deploy_guards.sh)
#
#   1  tree        working tree clean; pip bundle in sync (sync_bundle.sh + git diff)
#   2  exports     site exports regenerate byte-identical: coverage.json (matrix), agent-coverage.json
#                  (agent_matrix), site_claims manifest/evidence (sync_site_claims --check),
#                  checks/ (site_gates checkdocs), agent-demo.json (build_demo_data --check),
#                  known-issues (gen_known_issues --check, once D5-12 lands); `git diff --exit-code public/`
#   3  gates       coverage, evidence-class, agent-governance, site_gates (every mode incl.
#                  docclaims + --orphans), known-issues, preview_parity (once D5-11 lands)
#   4  provenance  HEAD == origin/main AND the `selftest` check-run for this SHA is `success`
#                  (this is where "nothing deploys while W0-1 is red" is mechanical:
#                  probe-shape-0825 is a run_suite gate inside that check)
#   5  preview     wrangler pages deploy --branch=preview-<sha7>, then a smoke fetch of /coverage.json
#   6  main        wrangler pages deploy --branch=main; append ops/DEPLOY_LOG.md
#
# Env (tests/stubs): GH_BIN, WRANGLER_BIN, SMOKE_BIN, DEPLOY_NO_FETCH=1, DEPLOY_REPO, DEPLOY_PROJECT.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODE=run
case "${1:-}" in
  --dry-run) MODE=dry;;
  --selftest) exec bash "$ROOT/packaging/test_deploy_guards.sh";;
  "") ;;
  *) echo "usage: deploy.sh [--dry-run|--selftest]"; exit 2;;
esac
GH="${GH_BIN:-gh}"
WRANGLER="${WRANGLER_BIN:-npx wrangler@latest}"
SMOKE="${SMOKE_BIN:-curl}"
REPO="${DEPLOY_REPO:-vishkaty/ucp-conformance}"
PROJECT="${DEPLOY_PROJECT:-ucp-conformance}"
PY="${PYTHON:-python3}"
step(){ printf "\n\033[1m▶ step %s — %s\033[0m\n" "$1" "$2"; }
ok(){   printf "  \033[32m✓\033[0m %s\n" "$1"; }
refuse(){ printf "  \033[31m✗\033[0m %s\n\n\033[1;31mdeploy REFUSED at step %s — nothing deployed.\033[0m\n" "$2" "$1"; exit 3; }
run_q(){ "$@" >/tmp/deploy_step.log 2>&1; }

# ── 1 tree ─────────────────────────────────────────────────────────────────────
step 1 "working tree + bundle"
[ -z "$(git status --porcelain --untracked-files=normal -- . ':!ops' 2>/dev/null)" ] || refuse 1 "working tree is dirty — commit or stash first"
bash packaging/sync_bundle.sh >/dev/null 2>&1 || refuse 1 "sync_bundle.sh failed"
git diff --quiet -- packaging/spck_conformance/_bundle || refuse 1 "pip bundle drifted from source (commit the re-synced bundle)"
ok "clean tree; bundle in sync"

# ── 2 exports ──────────────────────────────────────────────────────────────────
step 2 "site exports regenerate byte-identical"
TMP="$(mktemp -d)"
run_q "$PY" conformance/coverage/matrix.py --json "$TMP/coverage.json" --md "$TMP/matrix.md" || refuse 2 "matrix.py failed: $(tail -1 /tmp/deploy_step.log)"
cmp -s "$TMP/coverage.json" public/coverage.json || refuse 2 "public/coverage.json is STALE vs a fresh matrix export (regenerate + commit)"
run_q "$PY" conformance/agent/agent_matrix.py --json "$TMP/agent-coverage.json" || refuse 2 "agent_matrix.py failed"
cmp -s "$TMP/agent-coverage.json" public/agent-coverage.json || refuse 2 "public/agent-coverage.json is STALE (regenerate + commit)"
run_q "$PY" conformance/web/sync_site_claims.py --check || refuse 2 "site_claims.json manifest/evidence out of sync: $(tail -1 /tmp/deploy_step.log)"
run_q "$PY" conformance/ci/site_gates.py checkdocs || refuse 2 "public/checks/ differs from regeneration"
run_q "$PY" conformance/agent/build_demo_data.py --check || refuse 2 "public/agent-demo.json out of sync"
if [ -f conformance/web/gen_known_issues.py ]; then
  run_q "$PY" conformance/web/gen_known_issues.py --check || refuse 2 "known-issues page out of sync"
fi
git diff --quiet --exit-code -- public/ || refuse 2 "public/ changed during export regeneration"
ok "coverage.json, agent-coverage.json, site_claims blocks, checks/, agent-demo.json byte-fresh"
rm -rf "$TMP"

# ── 3 gates ────────────────────────────────────────────────────────────────────
step 3 "public-claim gates"
GATES=(
  "conformance/coverage/coverage_gate.py"
  "conformance/selfcheck/validate_evidence_class.py"
  "conformance/agent/agent_governance.py"
  "conformance/ci/site_gates.py tdd" "conformance/ci/site_gates.py claims" "conformance/ci/site_gates.py claims --orphans"
  "conformance/ci/site_gates.py voice" "conformance/ci/site_gates.py security" "conformance/ci/site_gates.py redirects"
  "conformance/ci/site_gates.py consistency" "conformance/ci/site_gates.py freshness" "conformance/ci/site_gates.py docclaims"
)
for g in "${GATES[@]}"; do
  # shellcheck disable=SC2086
  run_q "$PY" $g || refuse 3 "gate red: $g — $(tail -1 /tmp/deploy_step.log)"
done
"$PY" conformance/ci/validate_known_issues.py >/tmp/deploy_step.log 2>&1; rc=$?
[ $rc -eq 0 ] || [ $rc -eq 2 ] || refuse 3 "gate red: validate_known_issues.py — $(tail -1 /tmp/deploy_step.log)"
if [ -f conformance/ci/preview_parity.py ]; then
  run_q "$PY" conformance/ci/preview_parity.py || refuse 3 "gate red: preview_parity.py"
fi
ok "${#GATES[@]}+ gates green"

# ── 4 provenance ───────────────────────────────────────────────────────────────
step 4 "provenance (HEAD == origin/main; selftest check-run success)"
[ "${DEPLOY_NO_FETCH:-0}" = "1" ] || git fetch -q origin main 2>/dev/null || refuse 4 "git fetch origin main failed"
SHA="$(git rev-parse HEAD)"; SHA7="$(git rev-parse --short=7 HEAD)"
MAIN="$(git rev-parse refs/remotes/origin/main 2>/dev/null || true)"
[ -n "$MAIN" ] && [ "$SHA" = "$MAIN" ] || refuse 4 "HEAD $SHA7 is not origin/main (${MAIN:0:7}) — deploy only what is merged and CI-verified"
CONCLUSION="$($GH api "repos/$REPO/commits/$SHA/check-runs" --jq '[.check_runs[]|select(.name=="selftest")]|.[0].conclusion' 2>/dev/null | tr -d '"' | tail -1)"
[ "$CONCLUSION" = "success" ] || refuse 4 "the \`selftest\` check-run for $SHA7 is '${CONCLUSION:-absent}', not success"
ok "HEAD $SHA7 == origin/main; selftest check-run: success"

# ── 5 preview ──────────────────────────────────────────────────────────────────
step 5 "preview deploy (--branch=preview-$SHA7) + smoke"
if [ "$MODE" = dry ]; then
  ok "(dry-run) would deploy public/ to --branch=preview-$SHA7 and smoke /coverage.json"
  step 6 "main deploy"
  ok "(dry-run) would deploy $SHA7 to --branch=main and append ops/DEPLOY_LOG.md"
  echo; echo "would deploy $SHA7"
  exit 0
fi
[ -n "${CLOUDFLARE_API_TOKEN:-}" ] || export CLOUDFLARE_API_TOKEN="$(katyal-secret get cloudflare.api-token 2>/dev/null || true)"
[ -n "${CLOUDFLARE_API_TOKEN:-}" ] || refuse 5 "no CLOUDFLARE_API_TOKEN (owner's machine only; the token is never in CI)"
# shellcheck disable=SC2086
PREVIEW_OUT="$($WRANGLER pages deploy public --project-name="$PROJECT" --branch="preview-$SHA7" 2>&1)" || refuse 5 "preview deploy failed: $(echo "$PREVIEW_OUT" | tail -2)"
PREVIEW_URL="$(echo "$PREVIEW_OUT" | grep -oE 'https://[a-zA-Z0-9.-]+\.pages\.dev' | tail -1)"
[ -n "$PREVIEW_URL" ] || refuse 5 "preview deploy printed no URL"
"$SMOKE" -fsS "$PREVIEW_URL/coverage.json" 2>/dev/null | "$PY" -c 'import json,sys; json.load(sys.stdin)' || refuse 5 "preview smoke failed: $PREVIEW_URL/coverage.json is not valid JSON"
ok "preview $PREVIEW_URL smoke OK"

# ── 6 main ─────────────────────────────────────────────────────────────────────
step 6 "main deploy"
# shellcheck disable=SC2086
MAIN_OUT="$($WRANGLER pages deploy public --project-name="$PROJECT" --branch=main 2>&1)" || refuse 6 "main deploy failed: $(echo "$MAIN_OUT" | tail -2)"
if [ -d ops ]; then
  printf '| %s | %s | %s | %s |\n' "$(date -u +%Y-%m-%dT%H:%MZ)" "$SHA7" "$PREVIEW_URL" "$(whoami)" >> ops/DEPLOY_LOG.md
fi
ok "deployed $SHA7 to main"
echo; echo "deployed $SHA7"
exit 0
