#!/usr/bin/env bash
# serve_golden_0825.sh — bring up OUR OWN v2026-08-25 reference server ("golden-0825")
# with seeded data, wait for health, and print its PID. Mirrors
# conformance/ci/serve_golden.sh (the 2026-04-08 flower-shop golden), adapted for
# a server that lives IN THIS REPO (there is no upstream 08-25 reference to vendor —
# see STATUS.md).
#
#   PORT=8283 SIM_SECRET=selfcheck-secret DB_DIR=/tmp/ucp_golden_0825 \
#       conformance/testbed/golden-0825/serve_golden_0825.sh
#   REQUIRE_SIGNATURES=1 PORT=8196 DB_DIR=$(mktemp -d) \
#       conformance/testbed/golden-0825/serve_golden_0825.sh   # signatures REQUIRED
#
# Writes the server PID to $DB_DIR/server.pid so stop_golden_0825.sh can kill it.
# Requires: uv. No conformance/ci/fetch_sources.sh needed for the server itself
# (it lives in this repo, not .vendor) -- but the accompanying smoke suite validates
# wire bodies against the vendored v2026-08-25 release under conformance/.vendor/
# ucp-2026-08-25, so that vendor MUST be present (fetch_sources.sh) for the smoke
# suite, even though the server boots without it.
set -euo pipefail

PORT="${PORT:-8283}"
SIM_SECRET="${SIM_SECRET:-selfcheck-secret}"
DB_DIR="${DB_DIR:-/tmp/ucp_golden_0825}"
# R11 defect-injection mode (PLAN-0825 SS C.4): OFF unless a caller exports
# DEFECTS_CONFIG (normally only conformance/selfcheck/validate_golden_0825_battery.py
# does). Unset means the --defects_config/--defects_state_file flags are never
# passed, so a normal boot is identical to a build with no defects code linked.
DEFECTS_CONFIG="${DEFECTS_CONFIG:-}"
DEFECTS_STATE_FILE="${DEFECTS_STATE_FILE:-}"
# C3 signature enforcement (D3-05): REQUIRE_SIGNATURES=1 passes BOTH
# --require_signatures (unsigned/invalid requests are 401 signature_missing /
# signature_invalid, SIG-031) and --allow_insecure_profile_urls (the localhost
# carve-out: a platform profile at http://localhost:... is the only kind a
# lane-local proof can host). Unset/0 (the default) boots exactly as before:
# signatures verified when present, never required.
REQUIRE_SIGNATURES="${REQUIRE_SIGNATURES:-}"
# D3-09: ALLOW_INSECURE_PROFILE_URLS=1 passes --allow_insecure_profile_urls on its
# own (signatures stay optional): a permissive golden may then resolve a signer's
# keys from a loopback http:// platform profile. The R11 battery sets it so its
# signed MCP check (mcp_headers_after_verify) can host the profile in-process.
# Implied by REQUIRE_SIGNATURES=1 (which passes the flag already); unset = never.
ALLOW_INSECURE_PROFILE_URLS="${ALLOW_INSECURE_PROFILE_URLS:-}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER="$ROOT/server"
DATA_DIR="${DATA_DIR:-$ROOT/test_data/flower_shop}"

[ -d "$SERVER" ] || { echo "golden-0825 server not found at $SERVER" >&2; exit 3; }
[ -f "$DATA_DIR/products.csv" ] || { echo "seed data not found at $DATA_DIR" >&2; exit 3; }
mkdir -p "$DB_DIR"

# Port-occupancy guard -- see serve_golden.sh for why this matters: without it a
# stale listener on $PORT would answer the health check and this script would
# report "golden-0825 UP" while validating nothing about the server it just tried
# (and failed) to start.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT is already in use — refusing to boot golden-0825." >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  exit 3
fi

# SDK-pin guard. golden-0825 exists BECAUSE ucp-sdk==0.5.0 is the first PyPI
# release whose generated models match the v2026-08-25 schemas (python-sdk#87,
# tag v2026-08-25). pyproject.toml pins it exactly; assert the synced env actually
# resolved that pin so a PyPI/lockfile drift can never change what this golden
# proves without being noticed, the same discipline serve_golden.sh applies to the
# 04-08 golden's pypi_pin.
WANT_SDK="$(grep -o 'ucp-sdk==[0-9.]*' "$SERVER/pyproject.toml" | head -1 | cut -d= -f3)"
[ -n "$WANT_SDK" ] || { echo "pyproject.toml does not pin an exact ucp-sdk version" >&2; exit 3; }

echo "seeding golden-0825 database in $DB_DIR (from $DATA_DIR) ..." >&2
( cd "$SERVER" && uv sync >/dev/null 2>&1 )
GOT_SDK="$( cd "$SERVER" && uv pip show ucp-sdk 2>/dev/null | awk '/^Version:/{print $2}' )"
[ -n "$GOT_SDK" ] || { echo "ucp-sdk not installed in the golden-0825 env after uv sync" >&2; exit 3; }
if [ "$GOT_SDK" != "$WANT_SDK" ]; then
  echo "SDK PIN DRIFT: golden-0825 resolved ucp-sdk $GOT_SDK but pyproject.toml pins $WANT_SDK." >&2
  echo "Re-pin deliberately and revalidate -- do not let the verdict float." >&2
  exit 3
fi
echo "✓ golden-0825 ucp-sdk $GOT_SDK matches pyproject.toml pin" >&2

( cd "$SERVER" && \
  uv run import_csv.py \
    --data_dir="$DATA_DIR" \
    --products_db_path="$DB_DIR/products.db" \
    --transactions_db_path="$DB_DIR/transactions.db" >/dev/null 2>&1 )

# Plain (possibly-empty) strings, not an array: bash 3.2 (macOS's default) treats
# `"${ARR[@]}"` on an EMPTY array as an unbound-variable error under `set -u`, so an
# array here would make the common (defects OFF) path crash under nounset. An empty
# string word-splits to zero arguments and is safe under `set -u`.
DEFECTS_FLAG=""
DEFECTS_STATE_FLAG=""
if [ -n "$DEFECTS_CONFIG" ]; then
  DEFECTS_FLAG="--defects_config=$DEFECTS_CONFIG"
  echo "defect-injection mode ON: $DEFECTS_CONFIG" >&2
fi
if [ -n "$DEFECTS_STATE_FILE" ]; then
  DEFECTS_STATE_FLAG="--defects_state_file=$DEFECTS_STATE_FILE"
fi
SIGNATURE_FLAGS=""
SIGNATURE_MODE="signatures optional"
if [ -n "$REQUIRE_SIGNATURES" ] && [ "$REQUIRE_SIGNATURES" != "0" ]; then
  SIGNATURE_FLAGS="--require_signatures --allow_insecure_profile_urls"
  SIGNATURE_MODE="signatures REQUIRED"
  echo "signature enforcement ON: --require_signatures (localhost platform profiles allowed)" >&2
fi

PROFILE_FLAGS=""
if [ -z "$SIGNATURE_FLAGS" ] && [ -n "$ALLOW_INSECURE_PROFILE_URLS" ] && [ "$ALLOW_INSECURE_PROFILE_URLS" != "0" ]; then
  PROFILE_FLAGS="--allow_insecure_profile_urls"
  echo "loopback platform profiles allowed: --allow_insecure_profile_urls (signatures still optional)" >&2
fi

echo "starting golden-0825 on :$PORT ..." >&2
# See serve_golden.sh for why this is `( cd ... && exec ... ) & echo $!` with stdio
# redirected rather than a plain background job: it detaches every inherited fd so
# a caller that captures this script's own stdout never blocks on the server's.
( cd "$SERVER" && exec uv run server.py \
    --products_db_path="$DB_DIR/products.db" \
    --transactions_db_path="$DB_DIR/transactions.db" \
    --port="$PORT" \
    --simulation_secret="$SIM_SECRET" \
    $DEFECTS_FLAG $DEFECTS_STATE_FLAG $SIGNATURE_FLAGS $PROFILE_FLAGS ) >"$DB_DIR/server.log" 2>&1 </dev/null &
echo $! >"$DB_DIR/server.pid"

WRAPPER_PID="$(cat "$DB_DIR/server.pid")"
echo "$WRAPPER_PID" >"$DB_DIR/server.wrapper.pid"
for i in $(seq 1 40); do
  if curl -sf -m 2 "http://localhost:$PORT/.well-known/ucp" >/dev/null 2>&1; then
    # `uv run` forks; record the actual listener so stop_golden_0825.sh frees the
    # port instead of leaving a "stopped" golden still bound to it.
    LISTEN_PID="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
    echo "${LISTEN_PID:-$WRAPPER_PID}" >"$DB_DIR/server.pid"
    echo "golden-0825 UP on :$PORT ($SIGNATURE_MODE) [listener ${LISTEN_PID:-unknown}, wrapper $WRAPPER_PID]"; exit 0
  fi
  sleep 0.5
done
PID="$WRAPPER_PID"
echo "golden-0825 failed to become healthy on :$PORT; last log lines:" >&2
tail -20 "$DB_DIR/server.log" >&2 || true
kill "$PID" 2>/dev/null || true
exit 1
