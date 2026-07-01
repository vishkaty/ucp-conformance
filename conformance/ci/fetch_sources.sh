#!/usr/bin/env bash
# fetch_sources.sh — reproducibly clone the pinned upstream sources into .vendor.
#
# .vendor/ is gitignored, so CI (and a fresh checkout) must materialize the sources
# of truth at the EXACT commit SHAs recorded in conformance/SOURCES.lock.json — never
# main/feature branches. This is what makes a verdict reproducible.
#
#   conformance/ci/fetch_sources.sh
#
# Idempotent: a dir already checked out at the right SHA is left alone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENDOR="$ROOT/conformance/.vendor"
LOCK="$ROOT/conformance/SOURCES.lock.json"
mkdir -p "$VENDOR"

# Emit "dir repo sha" lines from the lock file (commit may be a bare string or nested).
ROWFILE="$(mktemp)"; trap 'rm -f "$ROWFILE"' EXIT
python3 - "$LOCK" > "$ROWFILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); s = d.get("sources", d)
def sha(x): return x["commit"] if isinstance(x, dict) else x
sp = s["spec"]["versions"]
rows = [
    ("python-sdk",     "python-sdk",  sha(s["reference_sdk"])),
    ("samples",        "samples",     sha(s["reference_sample_server"])),
    ("ucp",            "ucp",         sha(sp["2026-04-08"])),
    ("ucp-2026-01-23", "ucp",         sha(sp["2026-01-23"])),
    ("ucp-2026-01-11", "ucp",         sha(sp["2026-01-11"])),
    ("ucp-schema",     "ucp-schema",  sha(s["schema_validator"])),
    ("conformance",    "conformance", sha(s["official_conformance_suite"])),
]
for d_, r, c in rows:
    print(d_, r, c)
PY

ORG="https://github.com/Universal-Commerce-Protocol"
while read -r dir repo sha; do
  [ -n "$dir" ] || continue
  dest="$VENDOR/$dir"
  if [ -d "$dest/.git" ] && [ "$(git -C "$dest" rev-parse HEAD 2>/dev/null)" = "$sha" ]; then
    echo "✓ $dir already at $sha"; continue
  fi
  echo "→ $dir : $repo @ $sha"
  rm -rf "$dest"; mkdir -p "$dest"
  git -C "$dest" init -q
  git -C "$dest" remote add origin "$ORG/$repo.git"
  git -C "$dest" fetch -q --depth 1 origin "$sha"
  git -C "$dest" checkout -q FETCH_HEAD
done < "$ROWFILE"

# Materialize the per-version schema-base dirs the ucp-schema oracle expects:
# ucp-schemas/<ver> is the source/ tree of the matching ucp-<ver> clone (04-08 uses
# ucp/source directly). Without these the schema/fixture/profile_schema gates skip.
for ver in 2026-01-23 2026-01-11; do
  src="$VENDOR/ucp-$ver/source"
  dst="$VENDOR/ucp-schemas/$ver"
  if [ -d "$src" ]; then
    rm -rf "$dst"; mkdir -p "$VENDOR/ucp-schemas"; cp -R "$src" "$dst"
    echo "✓ ucp-schemas/$ver materialized from ucp-$ver/source"
  else
    echo "! ucp-$ver/source missing; ucp-schemas/$ver not materialized" >&2
  fi
done
echo "all sources pinned per SOURCES.lock.json"
