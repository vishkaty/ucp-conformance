#!/usr/bin/env bash
# stop_golden.sh — tear down the golden server started by serve_golden.sh.
#   DB_DIR=/tmp/ucp_test conformance/ci/stop_golden.sh
DB_DIR="${DB_DIR:-/tmp/ucp_test}"
PIDF="$DB_DIR/server.pid"
if [ -f "$PIDF" ]; then
  PID="$(cat "$PIDF")"
  kill "$PID" 2>/dev/null && echo "stopped golden (pid $PID)" || echo "golden (pid $PID) not running"
  rm -f "$PIDF"
else
  echo "no pid file at $PIDF"
fi
