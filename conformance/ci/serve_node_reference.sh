#!/usr/bin/env bash
# serve_node_reference.sh — bring up the official Node.js reference server (the
# SECOND independent differential target: samples/rest/nodejs, a Hono + Zod +
# @ucp-js/sdk app) with the same flower catalog the differential config expects,
# wait for health, and print its PID.
#
#   NODE_PORT=3000 SIM_SECRET=selfcheck-secret \
#       conformance/ci/serve_node_reference.sh
#
# Writes the server PID to $NODE_DB_DIR/node_server.pid so a teardown step can kill
# it. Requires: node + npm, and the vendored samples under conformance/.vendor
# (run conformance/ci/fetch_sources.sh first if absent).
#
# WHY tsx AND NOT `npm run build && npm start`: the sample's production build is
# broken at the pinned SHA — tsc (moduleResolution:bundler) emits extensionless ESM
# imports that Node's loader rejects with ERR_MODULE_NOT_FOUND, and `npm start`
# points at dist/index.js while tsc writes dist/src/index.js. That is upstream
# samples#58 (closed but still reproducing). The `dev` path (tsx) resolves
# extensionless imports itself and runs the exact same source, so we boot with tsx.
set -euo pipefail

NODE_PORT="${NODE_PORT:-3000}"   # the sample hardcodes serve({ port: 3000 }); no override knob
SIM_SECRET="${SIM_SECRET:-selfcheck-secret}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NODE_DIR="$ROOT/conformance/.vendor/samples/rest/nodejs"
PY_DATA="$ROOT/conformance/.vendor/samples/rest/python/test_data/flower_shop"
NODE_DB_DIR="${NODE_DB_DIR:-$NODE_DIR/databases}"

[ -d "$NODE_DIR/src" ] || { echo "Node reference not vendored at $NODE_DIR — run fetch_sources.sh" >&2; exit 3; }
[ -f "$PY_DATA/products.csv" ] || { echo "flower catalog not found at $PY_DATA — run fetch_sources.sh" >&2; exit 3; }

# Port-occupancy guard (same rationale as serve_golden.sh): a stale listener would
# let the health check pass without proving THIS pinned server is what answers.
if lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $NODE_PORT is already in use — refusing to boot the Node reference." >&2
  lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN >&2
  exit 3
fi

echo "installing Node reference deps ..." >&2
( cd "$NODE_DIR" && npm install --no-audit --no-fund >/dev/null 2>&1 )

echo "seeding Node reference SQLite from the flower catalog ($PY_DATA) ..." >&2
mkdir -p "$NODE_DB_DIR"
# Seed the SAME flower catalog the Python golden uses, so the shared differential
# scenario (bouquet_roses, out-of-stock gardenias, etc.) resolves identically. The
# schema mirrors src/data/db.ts (products / inventory tables).
python3 - "$PY_DATA" "$NODE_DB_DIR" <<'PY'
import csv, os, sqlite3, sys
data, dbdir = sys.argv[1], sys.argv[2]
p = sqlite3.connect(os.path.join(dbdir, "products.db"))
p.execute("CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, title TEXT, price INTEGER, image_url TEXT)")
p.execute("DELETE FROM products")
with open(os.path.join(data, "products.csv")) as f:
    for row in csv.DictReader(f):
        p.execute("INSERT OR REPLACE INTO products VALUES (?,?,?,?)",
                  (row["id"], row["title"], int(row["price"]), row.get("image_url", "")))
p.commit()
t = sqlite3.connect(os.path.join(dbdir, "transactions.db"))
t.execute("CREATE TABLE IF NOT EXISTS inventory (product_id TEXT PRIMARY KEY, quantity INTEGER DEFAULT 0)")
t.execute("DELETE FROM inventory")
with open(os.path.join(data, "inventory.csv")) as f:
    for row in csv.DictReader(f):
        t.execute("INSERT OR REPLACE INTO inventory VALUES (?,?)",
                  (row["product_id"], int(row["quantity"])))
t.commit()
print(f"seeded {p.execute('SELECT count(*) FROM products').fetchone()[0]} products, "
      f"{t.execute('SELECT count(*) FROM inventory').fetchone()[0]} inventory rows", file=sys.stderr)
PY

echo "starting Node reference on :$NODE_PORT ..." >&2
# Detach every inherited fd (same reasoning as serve_golden.sh) so a caller that
# captures output does not block on a lingering subshell holding the pipes.
( cd "$NODE_DIR" && exec env SIMULATION_SECRET="$SIM_SECRET" \
    npx tsx src/index.ts ) >"$NODE_DB_DIR/node_server.log" 2>&1 </dev/null &
WRAPPER_PID=$!
echo "$WRAPPER_PID" >"$NODE_DB_DIR/node_server.pid"

for i in $(seq 1 60); do
  if curl -sf -m 2 "http://localhost:$NODE_PORT/.well-known/ucp" >/dev/null 2>&1; then
    # npx/tsx fork the runtime, so $! is the wrapper, not the listener. Record the
    # real listener so a teardown frees the port.
    LISTEN_PID="$(lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
    echo "${LISTEN_PID:-$WRAPPER_PID}" >"$NODE_DB_DIR/node_server.pid"
    echo "node reference UP on :$NODE_PORT (listener ${LISTEN_PID:-unknown}, wrapper $WRAPPER_PID)"
    exit 0
  fi
  sleep 0.5
done
echo "Node reference failed to become healthy on :$NODE_PORT; last log lines:" >&2
tail -20 "$NODE_DB_DIR/node_server.log" >&2 || true
kill "$WRAPPER_PID" 2>/dev/null || true
exit 1
