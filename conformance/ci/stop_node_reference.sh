#!/usr/bin/env bash
# stop_node_reference.sh — tear down the Node reference started by
# serve_node_reference.sh.
#   NODE_PORT=3000 conformance/ci/stop_node_reference.sh
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NODE_PORT="${NODE_PORT:-3000}"
NODE_DB_DIR="${NODE_DB_DIR:-$ROOT/conformance/.vendor/samples/rest/nodejs/databases}"
PIDF="$NODE_DB_DIR/node_server.pid"
if [ -f "$PIDF" ]; then
  PID="$(cat "$PIDF")"
  kill "$PID" 2>/dev/null && echo "stopped node reference (pid $PID)" || echo "node reference (pid $PID) not running"
  rm -f "$PIDF"
else
  echo "no pid file at $PIDF"
fi
# tsx/npx fork the runtime, so the recorded listener and its wrapper may differ;
# fall back to killing whatever still holds the port so the next boot can bind.
for _ in $(seq 1 20); do
  lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 0.5
done
if lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  STRAY="$(lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  [ -n "$STRAY" ] && kill "$STRAY" 2>/dev/null || true
  sleep 1
fi
# ESCALATE. A plain SIGTERM is not always enough: on 2026-09-03 the CI teardown
# reported "stopped node reference (pid 5644)" and twelve seconds later the SAME
# pid 5644 still held :3000, so the run failed on teardown after a fully green
# suite. Under tsx the listener can ignore or outlive SIGTERM, so follow up with
# SIGKILL and wait again before calling it stuck. Escalating is safe here: the
# port is ours by construction and the process is on its way out either way.
if lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  STRAY="$(lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  if [ -n "$STRAY" ]; then
    echo "node reference (pid $STRAY) survived SIGTERM; sending SIGKILL" >&2
    kill -9 "$STRAY" 2>/dev/null || true
  fi
  for _ in $(seq 1 20); do
    lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.5
  done
fi
if lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "WARNING: something still listens on :$NODE_PORT after teardown:" >&2
  lsof -nP -iTCP:"$NODE_PORT" -sTCP:LISTEN >&2
  exit 1
fi
