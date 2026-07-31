#!/usr/bin/env bash
# stop_golden.sh — tear down the golden server started by serve_golden.sh.
#   DB_DIR=/tmp/ucp_test conformance/ci/stop_golden.sh
DB_DIR="${DB_DIR:-/tmp/ucp_test}"
PORT="${PORT:-8182}"
PIDF="$DB_DIR/server.pid"
WPIDF="$DB_DIR/server.wrapper.pid"
if [ -f "$PIDF" ]; then
  PID="$(cat "$PIDF")"
  kill "$PID" 2>/dev/null && echo "stopped golden (pid $PID)" || echo "golden (pid $PID) not running"
  rm -f "$PIDF"
else
  echo "no pid file at $PIDF"
fi
# `uv run` forks, so the wrapper and the listener are different processes; killing
# only one leaves the port held. A survivor is not cosmetic — the next boot cannot
# bind, and a health check on the port would pass against the stale server.
[ -f "$WPIDF" ] && { kill "$(cat "$WPIDF")" 2>/dev/null; rm -f "$WPIDF"; }
# uvicorn shuts down gracefully, so the socket lingers briefly after SIGTERM.
# Give it a bounded grace period before reporting a survivor — checking
# immediately would false-alarm (and fail CI) on every healthy teardown.
for _ in $(seq 1 20); do
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 0.5
done
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "WARNING: something still listens on :$PORT after teardown —" >&2
  echo "the next golden boot would be answered by it, not by the pinned server:" >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  exit 1
fi
