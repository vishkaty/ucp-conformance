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

echo "seeding golden database in $DB_DIR (from $DATA_DIR) ..." >&2
( cd "$SERVER" && uv sync >/dev/null 2>&1 && \
  uv run import_csv.py \
    --data_dir="$DATA_DIR" \
    --products_db_path="$DB_DIR/products.db" \
    --transactions_db_path="$DB_DIR/transactions.db" >/dev/null 2>&1 )

echo "starting golden on :$PORT ..." >&2
( cd "$SERVER" && uv run server.py \
    --products_db_path="$DB_DIR/products.db" \
    --transactions_db_path="$DB_DIR/transactions.db" \
    --port="$PORT" \
    --simulation_secret="$SIM_SECRET" >"$DB_DIR/server.log" 2>&1 & echo $! >"$DB_DIR/server.pid" )

PID="$(cat "$DB_DIR/server.pid")"
for i in $(seq 1 40); do
  if curl -sf -m 2 "http://localhost:$PORT/.well-known/ucp" >/dev/null 2>&1; then
    echo "golden UP on :$PORT (pid $PID)"; exit 0
  fi
  sleep 0.5
done
echo "golden failed to become healthy on :$PORT; last log lines:" >&2
tail -20 "$DB_DIR/server.log" >&2 || true
kill "$PID" 2>/dev/null || true
exit 1
