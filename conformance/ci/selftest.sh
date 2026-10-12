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
# The pre-clean sweep is DERIVED from the ports registry (conformance/ci/ports.json,
# every row with sweep=true: golden, proxy, fixtures, sig-gate trio, static web, webhook
# harness pair, 01-11 golden, TLS harness, node reference, the golden-0825 family
# 8194-8199, and the selfcheck stub ports) — never a hand-maintained list here, which is
# how 8198/8199 went unswept. The ports-registry gate reds if this derivation is removed.
PORTS=("$PORT" $(python3 "$ROOT/conformance/ci/validate_ports_registry.py" --sweep))

free_ports() {
  for p in "${PORTS[@]}"; do
    pids="$(lsof -ti "tcp:$p" 2>/dev/null || true)"
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
  done
}

cleanup() {
  DB_DIR="$DB_DIR" bash "$ROOT/conformance/ci/stop_golden.sh" >/dev/null 2>&1 || true
  NODE_PORT=3000 bash "$ROOT/conformance/ci/stop_node_reference.sh" >/dev/null 2>&1 || true
  free_ports   # safety net: catches the proxy/fixture if run_suite was hard-killed
}
trap cleanup EXIT INT TERM

echo "selftest: pre-cleaning harness ports ${PORTS[*]} ..." >&2
free_ports
rm -rf "$DB_DIR"

# No bundle re-sync here, and no git command anywhere in this script (asserted by
# case 18 of packaging/test_preflight_guards.sh). This used to sync the bundle and
# `git add` it, so running the tests wrote to the index. Bundle staleness is caught
# by packaging/validate_bundle.py, which names an unsynced module and exits 1, and
# by preflight step 2, which regenerates and compares against HEAD. Run
# `bash packaging/sync_bundle.sh` deliberately when a register or exemption edit
# desyncs the copy.

bash "$ROOT/conformance/ci/serve_golden.sh"

# Best-effort SECOND differential target: the Node.js reference (samples/rest/nodejs).
# If it comes up, export UCP_NODEJS_URL so the differential gate exercises it too;
# if not, the gate simply omits it (reachability-filtered) — never a failure.
if NODE_PORT=3000 SIM_SECRET="$SIM_SECRET" bash "$ROOT/conformance/ci/serve_node_reference.sh"; then
  export UCP_NODEJS_URL="http://localhost:3000"
  echo "selftest: Node.js reference UP — differential gate will exercise it too" >&2
else
  echo "selftest: Node.js reference not up — differential runs the flower target only" >&2
fi

python3 "$ROOT/conformance/ci/run_suite.py" --server "http://localhost:$PORT" "$@"
# trap fires cleanup() on the way out with this exit code preserved
