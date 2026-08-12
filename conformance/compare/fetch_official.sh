#!/usr/bin/env bash
# fetch_official.sh — clone the OFFICIAL UCP conformance suite (+ its required
# python-sdk sibling) at the SHAs pinned in OFFICIAL.lock.json, into .official/
# (gitignored). Part of the P2-13 reproduction path — see README.md.
#
#   conformance/compare/fetch_official.sh            # fetch at the pinned SHAs
#   conformance/compare/fetch_official.sh --repin    # move the pin to current main
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK="$HERE/OFFICIAL.lock.json"
DEST="$HERE/.official"

pin() { python3 -c "import json,sys; print(json.load(open('$LOCK'))['$1']['commit'])"; }

mkdir -p "$DEST"
fetch() { # name repo sha
  local name="$1" repo="$2" sha="$3" dir="$DEST/$1"
  if [ ! -d "$dir/.git" ]; then
    git clone --quiet "https://github.com/$repo.git" "$dir"
  fi
  git -C "$dir" fetch --quiet origin
  if [ "${REPIN:-}" = "1" ]; then
    sha="$(git -C "$dir" rev-parse origin/main)"
    echo "repinned $name -> $sha (update OFFICIAL.lock.json commit field to match)"
  fi
  git -C "$dir" checkout --quiet "$sha"
  echo "$name @ $(git -C "$dir" rev-parse HEAD)"
}

[ "${1:-}" = "--repin" ] && REPIN=1
fetch conformance Universal-Commerce-Protocol/conformance "$(pin official_conformance_suite)"
# The sdk sibling is checked out at the RECORDED pin, exactly like the suite —
# reproduction must not float. The verdict-defining official miss (ERR-029)
# exists because of a python-sdk model bug we intend to fix upstream: fetching
# sdk@main would silently flip that verdict the day the fix merges. --repin
# moves BOTH pins together, deliberately.
fetch python-sdk  Universal-Commerce-Protocol/python-sdk  "$(pin official_python_sdk_sibling)"
( cd "$DEST/conformance" && uv sync >/dev/null )
echo "official suite env ready (uv sync done)."
