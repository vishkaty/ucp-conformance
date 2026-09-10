#!/usr/bin/env bash
# test_release_guards.sh — hermetic kill-tests for the TAG-time release guards (D5-21 / D5-15):
#   rc_before_final   a FINAL tag (v0.4.0) with no green rc acceptance recorded in
#                     packaging/CHANGELOG.md must be refused (exit 1) — decision 17: rc first
#   prerelease_tag_ok a PEP 440 pre-release tag (v0.4.0rc1) is accepted by the tag guard
#   lane_branch_tag   (D5-15) a tag on a commit that is NOT an ancestor of main → exit 1
#                     (scratch git repo + stub gh)
# Usage: bash packaging/test_release_guards.sh   (exit 0 pass · 1 fail)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0
ok(){  printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad(){ printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }

scratch() {
  local d; d="$(mktemp -d)"
  mkdir -p "$d/conformance/common" "$d/packaging/spck_conformance"
  cp "$ROOT"/conformance/common/*.py "$d/conformance/common/"
  cp "$ROOT/packaging/release_guards.sh" "$d/packaging/"
  cp "$ROOT/packaging/pyproject.toml" "$d/packaging/"
  echo '__version__ = "0.4.0"' > "$d/packaging/spck_conformance/__init__.py"
  sed -i.bak 's/^version = .*/version = "0.4.0"/' "$d/packaging/pyproject.toml"
  echo "$d"
}
# tag-guard only (no wheel build): RELEASE_GUARDS_TAG_ONLY=1 restricts release_guards.sh to
# the tag-shape / rc-before-final / ancestry checks so this test stays fast and hermetic.
tagguard() { ( cd "$1" && RELEASE_GUARDS_TAG_ONLY=1 bash packaging/release_guards.sh "$2" 2>&1 ); }

echo "release tag-guard kill-tests"
# rc_before_final: CHANGELOG has the 0.4.0 entry but NO rc entry with a green acceptance
D="$(scratch)"; printf '# Changelog\n\n## 0.4.0 — unreleased\n- things\n' > "$D/packaging/CHANGELOG.md"
OUT="$(tagguard "$D" v0.4.0)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "rc"; then ok "rc_before_final: final tag v0.4.0 with no green rc acceptance -> guard exit 1"; else bad "rc_before_final not enforced (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# a recorded green rc acceptance unlocks the final tag
D="$(scratch)"; printf '# Changelog\n\n## 0.4.0 — unreleased\n- things\n\n## 0.4.0rc1 — 2026-09-12\n- rc\nacceptance: green 2026-09-12 abc1234 (clean venv vs golden-0825 :8197, deviations 0)\n' > "$D/packaging/CHANGELOG.md"
OUT="$(tagguard "$D" v0.4.0)"; RC=$?
if [ $RC -eq 0 ]; then ok "final tag v0.4.0 with a green rc acceptance recorded -> accepted"; else bad "green rc acceptance not honoured (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# prerelease_tag_ok: the rc tag itself needs only its own CHANGELOG entry
D="$(scratch)"; sed -i.bak 's/^version = .*/version = "0.4.0rc1"/' "$D/packaging/pyproject.toml"; echo '__version__ = "0.4.0rc1"' > "$D/packaging/spck_conformance/__init__.py"
printf '# Changelog\n\n## 0.4.0 — unreleased\n\n## 0.4.0rc1 — unreleased\n- rc\nacceptance: pending\n' > "$D/packaging/CHANGELOG.md"
OUT="$(tagguard "$D" v0.4.0rc1)"; RC=$?
if [ $RC -eq 0 ]; then ok "prerelease_tag_ok: v0.4.0rc1 accepted (PEP 440 pre-release)"; else bad "pre-release tag refused (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# malformed tag shape
D="$(scratch)"; printf '# Changelog\n\n## 0.4.0 — unreleased\n' > "$D/packaging/CHANGELOG.md"
OUT="$(tagguard "$D" v0.4)"; RC=$?
if [ $RC -ne 0 ]; then ok "malformed tag v0.4 refused"; else bad "malformed tag accepted"; fi
rm -rf "$D"

# ── D5-15: release-time provenance guards ─────────────────────────────────────
# lane_branch_tag: the tag's commit must be an ancestor of origin/main — a tag on a lane
# branch commit is refused (scratch git repo: main at c1, tag on a side commit c2).
gitrepo() {   # $1 = scratch tree → turns it into a repo with origin/main at HEAD
  ( cd "$1" && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -q -m c1 \
      && git update-ref refs/remotes/origin/main HEAD )
}
D="$(scratch)"; printf '# Changelog\n\n## 0.4.0 — unreleased\n\n## 0.4.0rc1 — unreleased\n- rc\nacceptance: pending\n' > "$D/packaging/CHANGELOG.md"
sed -i.bak 's/^version = .*/version = "0.4.0rc1"/' "$D/packaging/pyproject.toml"; echo '__version__ = "0.4.0rc1"' > "$D/packaging/spck_conformance/__init__.py"; rm -f "$D"/packaging/*.bak
gitrepo "$D"; ( cd "$D" && echo lane > lane.txt && git add lane.txt && git -c user.name=t -c user.email=t@t commit -q -m c2 )
OUT="$(cd "$D" && RELEASE_GUARDS_TAG_ONLY=1 DEPLOY_NO_FETCH=1 GH_BIN=/bin/true bash packaging/release_guards.sh v0.4.0rc1 2>&1)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "ancestor"; then ok "lane_branch_tag: tag on a non-main commit -> guard exit 1"; else bad "lane_branch_tag not enforced (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# the same tag on a commit that IS origin/main passes the ancestry guard
D="$(scratch)"; printf '# Changelog\n\n## 0.4.0 — unreleased\n\n## 0.4.0rc1 — unreleased\n- rc\nacceptance: pending\n' > "$D/packaging/CHANGELOG.md"
sed -i.bak 's/^version = .*/version = "0.4.0rc1"/' "$D/packaging/pyproject.toml"; echo '__version__ = "0.4.0rc1"' > "$D/packaging/spck_conformance/__init__.py"; rm -f "$D"/packaging/*.bak
gitrepo "$D"
OUT="$(cd "$D" && RELEASE_GUARDS_TAG_ONLY=1 DEPLOY_NO_FETCH=1 GH_BIN=/bin/true bash packaging/release_guards.sh v0.4.0rc1 2>&1)"; RC=$?
if [ $RC -eq 0 ]; then ok "tag on origin/main -> ancestry guard passes"; else bad "ancestry guard rejects origin/main itself (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# selftest check-run failure with the CI half REQUIRED (release.yml) → refused
D="$(scratch)"; printf '# Changelog\n\n## 0.4.0 — unreleased\n\n## 0.4.0rc1 — unreleased\n- rc\nacceptance: pending\n' > "$D/packaging/CHANGELOG.md"
sed -i.bak 's/^version = .*/version = "0.4.0rc1"/' "$D/packaging/pyproject.toml"; echo '__version__ = "0.4.0rc1"' > "$D/packaging/spck_conformance/__init__.py"; rm -f "$D"/packaging/*.bak
gitrepo "$D"; mkdir -p "$D/bin"; printf '#!/usr/bin/env bash\necho failure\n' > "$D/bin/gh"; chmod +x "$D/bin/gh"
OUT="$(cd "$D" && RELEASE_GUARDS_TAG_ONLY=1 RELEASE_GUARDS_REQUIRE_CI=1 DEPLOY_NO_FETCH=1 GH_BIN="$D/bin/gh" bash packaging/release_guards.sh v0.4.0rc1 2>&1)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "check-run"; then ok "selftest check-run 'failure' with RELEASE_GUARDS_REQUIRE_CI=1 -> guard exit 1"; else bad "failed check-run not refused under REQUIRE_CI (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# missing CHANGELOG entry: a bump without an entry is refused
D="$(scratch)"; printf '# Changelog\n\n## 0.3.1 — old\n' > "$D/packaging/CHANGELOG.md"
OUT="$(cd "$D" && RELEASE_GUARDS_TAG_ONLY=1 bash packaging/release_guards.sh v0.4.0 2>&1)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "CHANGELOG"; then ok "missing CHANGELOG entry for the version -> guard exit 1"; else bad "missing CHANGELOG entry not caught (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

echo
if [ $FAIL -eq 0 ]; then echo "release tag-guard kill-tests: PASS"; exit 0; else echo "release tag-guard kill-tests: FAIL"; exit 1; fi
