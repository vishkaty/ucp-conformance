#!/usr/bin/env bash
# stop_golden_0825.sh — tear down the golden-0825 server started by
# serve_golden_0825.sh. Mirrors conformance/ci/stop_golden.sh.
#   DB_DIR=/tmp/ucp_golden_0825 conformance/testbed/golden-0825/stop_golden_0825.sh
set -uo pipefail
DB_DIR="${DB_DIR:-/tmp/ucp_golden_0825}"
PORT="${PORT:-8283}"
PIDF="$DB_DIR/server.pid"
WPIDF="$DB_DIR/server.wrapper.pid"
if [ -f "$PIDF" ]; then
  PID="$(cat "$PIDF")"
  kill "$PID" 2>/dev/null && echo "stopped golden-0825 (pid $PID)" || echo "golden-0825 (pid $PID) not running"
  rm -f "$PIDF"
else
  echo "no pid file at $PIDF"
fi
# `uv run` forks, so the wrapper and the listener can be different processes;
# killing only one can leave the port held for the next boot.
[ -f "$WPIDF" ] && { kill "$(cat "$WPIDF")" 2>/dev/null; rm -f "$WPIDF"; }
for _ in $(seq 1 20); do
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 0.5
done
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "WARNING: something still listens on :$PORT after teardown —" >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  exit 1
fi
