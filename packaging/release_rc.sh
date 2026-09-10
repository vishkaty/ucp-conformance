#!/usr/bin/env bash
# release_rc.sh — the rc-first release path (decision 17, PNR-3). Never pushes, never tags:
# it bumps, runs the guards/preflight, and PRINTS the tag line for the owner.
#
#   bash packaging/release_rc.sh --rc 1 [--dry-run] [--guards-only]
#       bump pyproject + __version__ to <target>rc1, run preflight (or the release guards
#       alone with --guards-only), print `git tag v<target>rc1 …`
#   bash packaging/release_rc.sh --final [--dry-run] [--guards-only]
#       REFUSED unless packaging/CHANGELOG.md records a green rc acceptance
#       (`acceptance: green …` under a `## <target>rcN` heading); then bump to <target> and print the tag line
#   --dry-run prints `would tag v…` (or the refusal) and changes nothing.
# <target> = the first non-rc `## X.Y.Z` heading in packaging/CHANGELOG.md (add-only).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODE=""; RC=""; DRY=0; GUARDS_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rc) MODE=rc; RC="$2"; shift 2;;
    --final) MODE=final; shift;;
    --dry-run) DRY=1; shift;;
    --guards-only) GUARDS_ONLY=1; shift;;
    *) echo "usage: release_rc.sh --rc N | --final [--dry-run] [--guards-only]"; exit 2;;
  esac
done
[ -z "$MODE" ] && { echo "usage: release_rc.sh --rc N | --final [--dry-run] [--guards-only]"; exit 2; }
CHANGELOG=packaging/CHANGELOG.md
TARGET=$(grep -oE '^## +v?[0-9]+\.[0-9]+\.[0-9]+( |$)' "$CHANGELOG" | grep -vE 'rc[0-9]+' | head -1 | sed -E 's/^## +v?//; s/ *$//')
[ -z "$TARGET" ] && { echo "refuse: no release target heading (## X.Y.Z) in $CHANGELOG"; exit 1; }

if [ "$MODE" = rc ]; then
  [[ "$RC" =~ ^[0-9]+$ ]] || { echo "refuse: --rc needs a number"; exit 2; }
  VERSION="${TARGET}rc${RC}"
  grep -qE "^## +v?${VERSION//./\\.}( |$)" "$CHANGELOG" || { echo "refuse: $CHANGELOG has no '## $VERSION' entry (add-only: write it first)"; exit 1; }
else
  VERSION="$TARGET"
  # a green rc acceptance must be recorded under some ## <target>rcN heading
  if ! awk -v t="$TARGET" '
      /^## / { insec = ($0 ~ ("^## +v?" t "rc[0-9]+")) }
      insec && /^acceptance: *green/ { found=1 }
      END { exit found ? 0 : 1 }' "$CHANGELOG"; then
    echo "refuse: no green rc acceptance recorded for $TARGET in $CHANGELOG (run --rc N, publish, record 'acceptance: green <date> <sha> (…)')"
    exit 1
  fi
fi
TAG="v$VERSION"
if [ $DRY -eq 1 ]; then echo "would tag $TAG (target $TARGET from $CHANGELOG)"; exit 0; fi

# bump (pyproject + __version__ together — release_guards.sh reds a mismatch)
sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$VERSION\"/" packaging/pyproject.toml && rm -f packaging/pyproject.toml.bak
sed -i.bak -E "s/^__version__ = \"[^\"]+\"/__version__ = \"$VERSION\"/" packaging/spck_conformance/__init__.py && rm -f packaging/spck_conformance/__init__.py.bak
echo "bumped packaging/pyproject.toml + spck_conformance.__version__ -> $VERSION"
if [ $GUARDS_ONLY -eq 1 ]; then
  bash packaging/preflight.sh --guards-only "$TAG" || { echo "release guards RED — not tagging"; exit 1; }
else
  bash packaging/preflight.sh "$TAG" || { echo "PREFLIGHT RED — not tagging"; exit 1; }
fi
echo
echo "Next (owner, PNR-3): commit the bump, then:  git tag $TAG && git push origin $TAG   (release.yml publishes via OIDC)"
[ "$MODE" = rc ] && echo "After PyPI shows $VERSION: clean-venv acceptance vs golden-0825 :8197, then record 'acceptance: green <date> <sha> (…)' under '## $VERSION' in $CHANGELOG and run --final."
exit 0
