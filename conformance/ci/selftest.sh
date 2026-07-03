#!/usr/bin/env bash
# selftest.sh — run the full conformance self-test locally with GUARANTEED cleanup.
#
# One command. It:
#   1. pre-cleans any stragglers on the harness ports (self-healing after a hard kill),
#   2. brings up the golden reference server,
#   3. runs every self-validation gate (run_suite.py, which also spawns + tears down the
#      controlled fixture and mutation proxy),
#   4. ALWAYS tears everything down on exit — success, failure, or Ctrl-C — via a trap,
#      so no server is ever left orphaned on ports 8182/8183/8184.
#
# Usage:
#   conformance/ci/selftest.sh [--verbose] [any extra run_suite.py args]
#
# This is the recommended way to run the suite locally. Never leaves processes behind.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PORT="${PORT:-8182}"
export SIM_SECRET="${SIM_SECRET:-selfcheck-secret}"
export DB_DIR="${DB_DIR:-/tmp/ucp_test}"
PORTS=("$PORT" 8183 8184 8185 8186 8187 8188 8189 8190 8191 8193 8443 8444 8445)   # golden, proxy, fixtures, sig-gate trio, static web, webhook harness pair, 01-11 golden, TLS harness

free_ports() {
  for p in "${PORTS[@]}"; do
    pids="$(lsof -ti "tcp:$p" 2>/dev/null || true)"
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
  done
}

cleanup() {
  DB_DIR="$DB_DIR" bash "$ROOT/conformance/ci/stop_golden.sh" >/dev/null 2>&1 || true
  free_ports   # safety net: catches the proxy/fixture if run_suite was hard-killed
}
trap cleanup EXIT INT TERM

echo "selftest: pre-cleaning harness ports ${PORTS[*]} ..." >&2
free_ports
rm -rf "$DB_DIR"

# pip-bundle freshness (CI hard-fails on a stale bundle; locally we AUTO-SYNC so
# the pending commit always carries a fresh bundle — an em-dash/ensure_ascii JSON
# re-encode in a register/exemption edit can desync the copy, which run_suite's
# gates never see because they read source, not the bundle). Non-fatal locally:
# it corrects + stages, so "forgot to sync" can't reach a commit.
if bash "$ROOT/packaging/sync_bundle.sh" >/dev/null 2>&1; then
  if ! git -C "$ROOT" diff --quiet packaging/spck_conformance/_bundle 2>/dev/null; then
    echo "selftest: pip bundle was stale vs source — re-synced + staged for commit" >&2
    git -C "$ROOT" add packaging/spck_conformance/_bundle 2>/dev/null || true
  fi
fi

bash "$ROOT/conformance/ci/serve_golden.sh"
python3 "$ROOT/conformance/ci/run_suite.py" --server "http://localhost:$PORT" "$@"
# trap fires cleanup() on the way out with this exit code preserved
