#!/usr/bin/env bash
# serve_golden.sh — bring up the official Flower Shop reference server (the golden)
# with seeded data, wait for health, and print its PID. Used by CI and locally.
#
#   PORT=8182 SIM_SECRET=selfcheck-secret DB_DIR=/tmp/ucp_test \
#       conformance/ci/serve_golden.sh
#
# Writes the server PID to $DB_DIR/server.pid so a teardown step can kill it.
# Requires: uv, and the vendored samples + python-sdk under conformance/.vendor
# (run conformance/ci/fetch_sources.sh first if absent).
set -euo pipefail

PORT="${PORT:-8182}"
SIM_SECRET="${SIM_SECRET:-selfcheck-secret}"
DB_DIR="${DB_DIR:-/tmp/ucp_test}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER="$ROOT/conformance/.vendor/samples/rest/python/server"
DATA_DIR="${DATA_DIR:-$ROOT/conformance/.vendor/samples/rest/python/test_data/flower_shop}"

[ -d "$SERVER" ] || { echo "golden server not vendored at $SERVER — run fetch_sources.sh" >&2; exit 3; }
[ -f "$DATA_DIR/products.csv" ] || { echo "seed data not found at $DATA_DIR" >&2; exit 3; }
mkdir -p "$DB_DIR"

# Port-occupancy guard. The health check below only proves that SOMETHING answers
# on $PORT. If a previous golden is still bound (easy to do: `uv run` forks, so a
# teardown that kills the recorded wrapper PID can leave the real server alive),
# the new boot fails to bind, the old process answers, and this script reports
# "golden UP" — validating the suite against stale code while claiming to test the
# pinned SHA. Worse after a re-pin: the stale process re-reads data files that
# fetch_sources has already replaced, so it serves new payloads through old code.
# A verdict from that is worthless, so refuse to start rather than report a lie.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT is already in use — refusing to boot the golden." >&2
  echo "Something is already listening there, so a health check on $PORT would" >&2
  echo "pass without proving the pinned server is what answers. Stop it first:" >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  exit 3
fi

# SDK-pin guard. Older pinned samples took ucp-sdk as an editable path dep, so the
# SHA in SOURCES.lock (reference_sdk.commit) was the SDK under test. From samples
# 2e5b77c1 the server dropped [tool.uv.sources], so ucp-sdk comes from PyPI — an
# unpinned input that could change a verdict silently. When (and only when) the
# server resolves from PyPI, hold it to reference_sdk.pypi_pin and fail loudly on
# drift, so a PyPI release is re-pinned deliberately, exactly like a SHA.
sdk_pin_guard() {
  if grep -q "tool.uv.sources" "$SERVER/pyproject.toml" 2>/dev/null; then
    echo "golden takes ucp-sdk as a path dep; SHA pin governs, PyPI guard n/a" >&2
    return 0
  fi
  local want got
  want="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["reference_sdk"].get("pypi_pin",""))' "$LOCK" 2>/dev/null)"
  [ -n "$want" ] || { echo "no reference_sdk.pypi_pin in SOURCES.lock; cannot verify the golden's SDK" >&2; return 1; }
  got="$( cd "$SERVER" && uv pip show ucp-sdk 2>/dev/null | awk '/^Version:/{print $2}' )"
  [ -n "$got" ] || { echo "ucp-sdk not installed in the golden env after uv sync" >&2; return 1; }
  [ "$got" = "$want" ] || {
    echo "SDK PIN DRIFT: golden resolved ucp-sdk $got but SOURCES.lock pins $want." >&2
    echo "PyPI moved under the pinned server. Re-pin reference_sdk.pypi_pin deliberately" >&2
    echo "and revalidate, or install $want — do not let the verdict float." >&2
    return 1; }
  echo "✓ golden ucp-sdk $got matches SOURCES.lock reference_sdk.pypi_pin" >&2
}

LOCK="$ROOT/conformance/SOURCES.lock.json"

echo "seeding golden database in $DB_DIR (from $DATA_DIR) ..." >&2
( cd "$SERVER" && uv sync >/dev/null 2>&1 )
sdk_pin_guard || exit 3
( cd "$SERVER" && \
  uv run import_csv.py \
    --data_dir="$DATA_DIR" \
    --products_db_path="$DB_DIR/products.db" \
    --transactions_db_path="$DB_DIR/transactions.db" >/dev/null 2>&1 )

echo "starting golden on :$PORT ..." >&2
# Detach every inherited fd. `( cmd & echo $! )` leaves a bash subshell alive holding
# this script's stdout/stderr, so any caller that CAPTURES output (subprocess with
# pipes, `| tail`, CI log capture) blocks long after the script itself has exited —
# it waits on pipes the lingering subshell still owns. Backgrounding a subshell that
# `exec`s the server, with stdio bound to the log and stdin closed, leaves no such
# holder. $! is then the subshell-turned-server; the true listener is resolved below.
( cd "$SERVER" && exec uv run server.py \
    --products_db_path="$DB_DIR/products.db" \
    --transactions_db_path="$DB_DIR/transactions.db" \
    --port="$PORT" \
    --simulation_secret="$SIM_SECRET" ) >"$DB_DIR/server.log" 2>&1 </dev/null &
echo $! >"$DB_DIR/server.pid"

WRAPPER_PID="$(cat "$DB_DIR/server.pid")"
echo "$WRAPPER_PID" >"$DB_DIR/server.wrapper.pid"
for i in $(seq 1 40); do
  if curl -sf -m 2 "http://localhost:$PORT/.well-known/ucp" >/dev/null 2>&1; then
    # `uv run` forks the interpreter, so $! above is the wrapper, not the process
    # holding the port. Recording the wrapper is what let a "stopped" golden keep
    # listening. Record the actual listener so stop_golden.sh frees the port.
    LISTEN_PID="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
    echo "${LISTEN_PID:-$WRAPPER_PID}" >"$DB_DIR/server.pid"
    echo "golden UP on :$PORT (listener ${LISTEN_PID:-unknown}, wrapper $WRAPPER_PID)"; exit 0
  fi
  sleep 0.5
done
PID="$WRAPPER_PID"
echo "golden failed to become healthy on :$PORT; last log lines:" >&2
tail -20 "$DB_DIR/server.log" >&2 || true
kill "$PID" 2>/dev/null || true
exit 1
