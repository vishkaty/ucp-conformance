#!/usr/bin/env bash
# reproduce.sh — one command to reproduce the P2-13 kill-rate comparison from a
# clean checkout. Anyone can rerun this and get the same table (modulo the pins
# they fetch — results.json records the exact SHAs of every input it used).
#
#   conformance/compare/reproduce.sh [PORT]        # default golden port 8290
#
# Requires: python3, uv, git, network to fetch pinned sources on first run.
# (At the pinned official SHA no test fetches external URLs at runtime — the
# only ucp.dev-fetching test, protocol_test.test_discovery_urls, is skipped.)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PORT="${1:-8290}"
PROXY_PORT=$((PORT + 1))
DB_DIR="${DB_DIR:-/tmp/ucp_p2cmp_repro}"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT is in use — stop whatever holds it first (the runner boots its own goldens)" >&2
  exit 3
fi

# 1. vendored pinned sources for the golden (no-op if already fetched)
[ -d "$ROOT/conformance/.vendor/samples" ] || "$ROOT/conformance/ci/fetch_sources.sh"

# 2. the official suite + its python-sdk sibling at the comparison pins
[ -d "$HERE/.official/conformance/.git" ] || "$HERE/fetch_official.sh"

# 3. hermetic self-test of the comparison harness itself (must be green BEFORE
#    any number is produced; exit 2 = green-but-no-results-yet, which is fine here)
python3 "$HERE/test_compare.py" || [ $? -eq 2 ]

# 4. the comparison (full mutant set, both suites, 2 repeats for determinism).
#    The runner boots a FRESH reseeded golden for every suite run itself — the
#    flower reference depletes inventory on completed checkouts, so a shared
#    long-lived golden would drift and eventually mass-fail on OUT_OF_STOCK.
python3 "$HERE/run_compare.py" --golden-port "$PORT" --db-dir "$DB_DIR" \
    --proxy-port "$PROXY_PORT" --repeat 2 --out "$HERE/results"

# 5. calibration gates over the recorded results (known-both-catch mutant,
#    spck-only exclusion, determinism) — the run is only valid if this is green
python3 "$HERE/test_compare.py"
echo "reproduction complete: see $HERE/results/results.md"
