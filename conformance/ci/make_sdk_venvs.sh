#!/usr/bin/env bash
# make_sdk_venvs.sh — the two python-sdk venvs the pydantic third leg runs in (D4-05 / B5b).
#
#   conformance/.sdk-venvs/ucp-sdk-tag   ucp-sdk==<pypi_pin>  (the tag cut on PyPI; GATED
#                                        through conformance/ci/known_sdk_drops.json)
#   conformance/.sdk-venvs/ucp-sdk-main  python-sdk @ <main_commit>  (REPORT-ONLY leg)
#
# Pins come from conformance/ci/known_sdk_drops.json `sdk` (pypi_pin + main_commit; the lock's
# future reference_sdk_0825 block carries the same names — D2). Idempotent: an existing venv
# whose `pip show ucp-sdk` already equals the pin is left alone; a venv at the wrong version
# is rebuilt. Never imports either SDK into the suite's interpreter (two ucp_sdk versions
# cannot coexist): pydantic_leg.py shells into these venvs. Requires `uv` (or python3 -m venv).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DROPS="$ROOT/conformance/ci/known_sdk_drops.json"
DIR="$ROOT/conformance/.sdk-venvs"
PYPI_PIN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sdk"]["pypi_pin"])' "$DROPS")"
MAIN_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sdk"]["main_commit"])' "$DROPS")"
mkdir -p "$DIR"

installed() { "$1/bin/python" -c 'import importlib.metadata as m; print(m.version("ucp-sdk"))' 2>/dev/null || true; }

make_venv() {  # name spec want_version
  local v="$DIR/$1" spec="$2" want="$3"
  if [ -x "$v/bin/python" ] && [ "$(installed "$v")" = "$want" ]; then
    echo "✓ $1 already at ucp-sdk $want"; return 0
  fi
  rm -rf "$v"
  if command -v uv >/dev/null 2>&1; then
    uv venv -q "$v" && uv pip install -q --python "$v/bin/python" "$spec"
  else
    python3 -m venv "$v" && "$v/bin/python" -m pip install -q "$spec"
  fi
  local got; got="$(installed "$v")"
  [ "$got" = "$want" ] || { echo "SDK PIN DRIFT: $1 resolved ucp-sdk '$got' but the pin is $want" >&2; exit 3; }
  echo "✓ $1 built: ucp-sdk $got ($spec)"
}

make_venv ucp-sdk-tag  "ucp-sdk==$PYPI_PIN" "$PYPI_PIN"
# main: the version string is whatever pyproject says at that SHA; record it, do not pin it.
MAIN_WANT="$("$DIR/ucp-sdk-main/bin/python" -c 'import importlib.metadata as m; print(m.version("ucp-sdk"))' 2>/dev/null || echo "")"
if [ -x "$DIR/ucp-sdk-main/bin/python" ] && [ -f "$DIR/ucp-sdk-main/.sha" ] && [ "$(cat "$DIR/ucp-sdk-main/.sha")" = "$MAIN_SHA" ]; then
  echo "✓ ucp-sdk-main already at python-sdk $MAIN_SHA (ucp-sdk $MAIN_WANT)"
else
  rm -rf "$DIR/ucp-sdk-main"
  if command -v uv >/dev/null 2>&1; then
    uv venv -q "$DIR/ucp-sdk-main" && uv pip install -q --python "$DIR/ucp-sdk-main/bin/python" "git+https://github.com/Universal-Commerce-Protocol/python-sdk@$MAIN_SHA"
  else
    python3 -m venv "$DIR/ucp-sdk-main" && "$DIR/ucp-sdk-main/bin/python" -m pip install -q "git+https://github.com/Universal-Commerce-Protocol/python-sdk@$MAIN_SHA"
  fi
  echo "$MAIN_SHA" > "$DIR/ucp-sdk-main/.sha"
  echo "✓ ucp-sdk-main built: python-sdk $MAIN_SHA (ucp-sdk $(installed "$DIR/ucp-sdk-main"), report-only)"
fi
