#!/usr/bin/env bash
# sync_bundle.sh — copy the runtime modules + register from conformance/ (the single
# source of truth) into the package bundle, preserving the conformance/ structure so
# the runner's path-relative resolution (REQ_DIR, selfcheck/) works when installed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../conformance"
DST="$HERE/spck_conformance/_bundle/conformance"
rm -rf "$DST"
mkdir -p "$DST/checks" "$DST/selfcheck" "$DST/requirements"
cp "$SRC/checks/engine.py" "$SRC/checks/merchant.py" \
   "$SRC"/checks/merchant_checks*.py \
   "$SRC/checks/tls_check_01_11_01_23.py" "$DST/checks/"
cp "$SRC/selfcheck/verdict_gate.py" "$DST/selfcheck/"
cp -R "$SRC/requirements/." "$DST/requirements/"
echo "bundle synced from $SRC -> $DST"
