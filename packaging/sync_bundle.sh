#!/usr/bin/env bash
# sync_bundle.sh — copy the runtime modules + register from conformance/ (the single
# source of truth) into the package bundle, preserving the conformance/ structure so
# the runner's path-relative resolution (REQ_DIR, selfcheck/) works when installed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../conformance"
DST="$HERE/spck_conformance/_bundle/conformance"
rm -rf "$DST"
mkdir -p "$DST/checks" "$DST/selfcheck" "$DST/requirements" "$DST/agent" "$DST/common"
cp "$SRC/checks/engine.py" "$SRC/checks/merchant.py" \
   "$SRC"/checks/merchant_checks*.py \
   "$SRC/checks/webhook_harness.py" "$SRC/checks/oauth_harness.py" \
   "$SRC/checks/tls_check_01_11_01_23.py" "$DST/checks/"
cp "$SRC/selfcheck/verdict_gate.py" "$DST/selfcheck/"
cp -R "$SRC/requirements/." "$DST/requirements/"
# agent lane (the reverse harness): reference agent + sandbox + checks + runner, so the
# distributed CLI is genuinely two-sided (`spck-conformance --agent`). Its only cross-tree
# dep is common/crypto, and its "parent-of-agent on sys.path" resolution works in the bundle.
cp "$SRC"/agent/*.py "$DST/agent/"
cp "$SRC"/agent/*.json "$DST/agent/"
cp "$SRC"/common/*.py "$DST/common/"
# testbed (AP2 mandate tooling): the agent lane mints mandate chains via testbed/mint,
# and the codec/verifiers are pure stdlib — ship them so the packaged two-sided CLI
# covers the AP2 scenario too.
mkdir -p "$DST/testbed"
cp "$SRC"/testbed/*.py "$DST/testbed/"
echo "bundle synced from $SRC -> $DST"
