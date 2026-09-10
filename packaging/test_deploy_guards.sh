#!/usr/bin/env bash
# test_deploy_guards.sh — hermetic kill-tests for packaging/deploy.sh (D5-08): the only
# sanctioned path to spck.dev must REFUSE every unsafe state and must deploy the preview
# branch BEFORE main. Builds a SYNTHETIC git repo (golden_boot_guards._mkroot idiom) with
# stub engine scripts at the exact paths deploy.sh calls, a stub `gh`, a stub `wrangler`
# that records its argv, and a stub smoke fetcher — the real repo, network and Cloudflare
# are never touched. `deploy.sh --selftest` delegates here.
#
#   refusals (exit 3): dirty tree · HEAD != origin/main · gh says the selftest check-run
#   failed · one-byte-stale coverage.json (step 2) · a red gate (step 3)
#   order: on a green run the stub wrangler log shows --branch=preview-<sha7> then --branch=main
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$HERE/deploy.sh"
FAIL=0; REFUSED=0
ok(){  printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad(){ printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }

mkroot() {   # $1 = coverage content the STUB matrix regenerates; $2 = committed coverage content
  local d; d="$(mktemp -d)"
  mkdir -p "$d/public" "$d/ops" "$d/packaging/spck_conformance/_bundle" "$d/conformance/coverage" \
           "$d/conformance/agent" "$d/conformance/web" "$d/conformance/ci" "$d/conformance/selfcheck" "$d/bin"
  cp "$DEPLOY" "$d/packaging/deploy.sh"
  printf 'exit 0\n' > "$d/packaging/sync_bundle.sh"
  : > "$d/packaging/spck_conformance/_bundle/marker"
  printf '%s' "$2" > "$d/public/coverage.json"
  printf '{"a":1}' > "$d/public/agent-coverage.json"
  printf '{"claims":[]}' > "$d/public/site_claims.json"
  # stub regenerators: matrix writes $1 to --json; agent_matrix writes the committed agent file
  cat > "$d/conformance/coverage/matrix.py" <<PY
import sys
a = sys.argv; out = a[a.index("--json")+1] if "--json" in a else None
open(out, "w").write("""$1""") if out else None
PY
  cat > "$d/conformance/agent/agent_matrix.py" <<'PY'
import sys
a = sys.argv; out = a[a.index("--json")+1] if "--json" in a else None
open(out, "w").write('{"a":1}') if out else None
PY
  for s in conformance/web/sync_site_claims.py conformance/agent/build_demo_data.py \
           conformance/coverage/coverage_gate.py conformance/selfcheck/validate_evidence_class.py \
           conformance/agent/agent_governance.py conformance/ci/validate_known_issues.py; do
    printf 'import os,sys\nsys.exit(1 if os.environ.get("GATE_STUB_FAIL") else 0)\n' > "$d/$s"
  done
  printf 'import os,sys\nsys.exit(1 if os.environ.get("GATE_STUB_FAIL") else 0)\n' > "$d/conformance/ci/site_gates.py"
  # stub tools
  cat > "$d/bin/gh" <<'SH'
#!/usr/bin/env bash
# stub gh: `api …/check-runs --jq …` -> the selftest conclusion
if [ "${GH_STUB_MODE:-success}" = "failure" ]; then echo "failure"; else echo "success"; fi
SH
  cat > "$d/bin/wrangler" <<'SH'
#!/usr/bin/env bash
echo "wrangler $*" >> "${WRANGLER_LOG:?}"
for a in "$@"; do case "$a" in --branch=*) b="${a#--branch=}";; esac; done
echo "✨ Deployment complete! Take a peek over at https://${b:-main}.ucp-conformance.pages.dev"
SH
  printf '#!/usr/bin/env bash\necho "{}"\n' > "$d/bin/curl"
  chmod +x "$d/bin/"*
  ( cd "$d" && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -q -m base \
      && git update-ref refs/remotes/origin/main HEAD )
  echo "$d"
}

run_deploy() {   # $1 root, rest = args; env: GH_STUB_MODE, GATE_STUB_FAIL
  ( cd "$1" && GH_BIN="$1/bin/gh" WRANGLER_BIN="$1/bin/wrangler" SMOKE_BIN="$1/bin/curl" \
      WRANGLER_LOG="$1/wrangler.log" DEPLOY_NO_FETCH=1 CLOUDFLARE_API_TOKEN=stub \
      bash packaging/deploy.sh "${@:2}" 2>&1 )
}

expect_refusal() {   # name, root, [env assignments…]
  local name="$1" root="$2"; shift 2
  local out rc
  out="$(env "$@" bash -c "$(declare -f run_deploy); run_deploy '$root'")"; rc=$?
  if [ $rc -eq 3 ]; then ok "$name -> exit 3"; REFUSED=$((REFUSED+1)); else bad "$name -> exit $rc (want 3)"; echo "$out" | tail -5 | sed 's/^/      /'; fi
  echo "$out"
}

echo "deploy.sh kill-tests"
[ -f "$DEPLOY" ] || { bad "packaging/deploy.sh does not exist"; echo; echo "deploy guards: FAIL"; exit 1; }

# 1. dirty tree
D="$(mkroot '{"v":1}' '{"v":1}')"; echo dirty > "$D/public/extra.html"
expect_refusal "dirty working tree" "$D" >/dev/null; rm -rf "$D"

# 2. HEAD != origin/main
D="$(mkroot '{"v":1}' '{"v":1}')"; ( cd "$D" && echo x > note.txt && git add note.txt && git -c user.name=t -c user.email=t@t commit -q -m ahead )
expect_refusal "HEAD is not origin/main" "$D" >/dev/null; rm -rf "$D"

# 3. gh reports the selftest check-run failed
D="$(mkroot '{"v":1}' '{"v":1}')"
expect_refusal "selftest check-run = failure (stub gh)" "$D" GH_STUB_MODE=failure >/dev/null; rm -rf "$D"

# 4. one-byte-stale coverage.json -> refused AT STEP 2
D="$(mkroot '{"v":1}' '{"v":2}')"
OUT="$(expect_refusal "one-byte-stale public/coverage.json" "$D")"
echo "$OUT" | grep -q "step 2" && ok "…refused at step 2 (site exports)" || bad "stale export not attributed to step 2"; rm -rf "$D"

# 5. a red gate -> refused at step 3
D="$(mkroot '{"v":1}' '{"v":1}')"
OUT="$(expect_refusal "red gate (stub coverage gate exits 1)" "$D" GATE_STUB_FAIL=1)"
echo "$OUT" | grep -q "step 3" && ok "…refused at step 3 (gates)" || bad "red gate not attributed to step 3"; rm -rf "$D"

# 6. green run: preview BEFORE main, log appended
D="$(mkroot '{"v":1}' '{"v":1}')"
OUT="$(run_deploy "$D")"; RC=$?
SHA7="$(cd "$D" && git rev-parse --short=7 HEAD)"
if [ $RC -eq 0 ]; then ok "green synthetic repo -> exit 0"; else bad "green run exit $RC"; echo "$OUT" | tail -8 | sed 's/^/      /'; fi
ORDER_OK=0
if [ -f "$D/wrangler.log" ]; then
  L1="$(grep -n "pages deploy" "$D/wrangler.log" | sed -n 1p)"; L2="$(grep -n "pages deploy" "$D/wrangler.log" | sed -n 2p)"
  if echo "$L1" | grep -q -- "--branch=preview-$SHA7" && echo "$L2" | grep -q -- "--branch=main"; then ORDER_OK=1; fi
fi
[ $ORDER_OK -eq 1 ] && ok "wrangler order: --branch=preview-$SHA7 then --branch=main" || { bad "preview→main order not observed:"; cat "$D/wrangler.log" 2>/dev/null | sed 's/^/      /'; }
grep -q "$SHA7" "$D/ops/DEPLOY_LOG.md" 2>/dev/null && ok "ops/DEPLOY_LOG.md records $SHA7" || bad "deploy log not written"
rm -rf "$D"

echo
if [ $FAIL -eq 0 ]; then
  echo "deploy guards: $REFUSED/5 refuse correctly · order preview→main asserted · PASS"; exit 0
else
  echo "deploy guards: $REFUSED/5 refuse correctly · FAIL"; exit 1
fi
