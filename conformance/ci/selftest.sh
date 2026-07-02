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
PORTS=("$PORT" 8183 8184)   # golden, mutation proxy, controlled fixture

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

bash "$ROOT/conformance/ci/serve_golden.sh"
python3 "$ROOT/conformance/ci/run_suite.py" --server "http://localhost:$PORT" "$@"
# trap fires cleanup() on the way out with this exit code preserved
