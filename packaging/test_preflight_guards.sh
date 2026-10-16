#!/usr/bin/env bash
# test_preflight_guards.sh — hermetic kill-tests for the RELEASE guards (D5-06 / D5-15):
# a guard that cannot fail proves nothing. Each case builds a SCRATCH copy of the
# packaging tree (the repo is never touched), plants one defect, and asserts the guard
# reds with a line naming it.
#
#   1. wheel bundle missing requirements/2026-08-25  -> release_guards.sh ✗ (versions derived
#      from conformance/common/spec_versions.VERSIONS, never a hard-coded set)
#   2. __version__ != pyproject                       -> release_guards.sh ✗
#   3. pypi report-only job expiry is MECHANICAL: pypi_job_mode.py says report-only below
#      0.4.0 (incl. 0.4.0rc1) and counted at >= 0.4.0
#   4. honest tree                                    -> release_guards.sh PASS
#   5-7. artifact freshness: staged-but-uncommitted / unstaged / honest (see the block below)
# Usage: bash packaging/test_preflight_guards.sh   (exit 0 pass · 1 fail)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0
ok(){  printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad(){ printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }

scratch() {   # a minimal copy: packaging/ (with the bundle) + conformance/common (VERSIONS)
  local d; d="$(mktemp -d)"
  mkdir -p "$d/conformance/common"
  cp -R "$ROOT/packaging" "$d/packaging"
  cp "$ROOT"/conformance/common/*.py "$d/conformance/common/"
  cp "$ROOT/conformance/SOURCES.lock.json" "$d/conformance/" 2>/dev/null || true
  rm -rf "$d/packaging/build" "$d/packaging"/*.egg-info
  echo "$d"
}
guards() { ( cd "$1" && bash packaging/release_guards.sh "${2:-}" 2>&1 ); }

echo "release-guards kill-tests"
# 1. wheel missing a spec version the engine ships
D="$(scratch)"; rm -rf "$D/packaging/spck_conformance/_bundle/conformance/requirements/2026-08-25"
OUT="$(guards "$D" v0.3.1)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "2026-08-25"; then ok "wheel missing requirements/2026-08-25 -> guard ✗ naming it"; else bad "wheel missing 2026-08-25 not caught (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# 2. __version__ drift
D="$(scratch)"; sed -i.bak 's/^__version__ = .*/__version__ = "0.0.0"/' "$D/packaging/spck_conformance/__init__.py"
OUT="$(guards "$D" v0.3.1)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "__version__"; then ok "__version__ 0.0.0 != pyproject -> guard ✗"; else bad "__version__ drift not caught (rc=$RC):"; echo "$OUT" | tail -4; fi
rm -rf "$D"

# 3. mechanical expiry of the report-only pypi job
for pair in "0.3.1:report-only" "0.4.0rc1:report-only" "0.4.0:counted" "0.4.1:counted" "1.0.0:counted"; do
  v="${pair%%:*}"; want="${pair##*:}"
  got="$(python3 "$ROOT/packaging/pypi_job_mode.py" --version "$v" 2>/dev/null)"
  [ "$got" = "$want" ] && ok "pypi job at $v -> $want" || bad "pypi job at $v -> '$got' (want $want)"
done

# 4. honest tree passes (tag = the current pyproject version)
D="$(scratch)"; PKG="$(python3 -c "import tomllib;print(tomllib.load(open('$ROOT/packaging/pyproject.toml','rb'))['project']['version'])")"
OUT="$(guards "$D" "v$PKG")"; RC=$?
if [ $RC -eq 0 ]; then ok "honest tree at v$PKG -> guards PASS"; else bad "honest tree red (rc=$RC):"; echo "$OUT" | tail -6; fi
rm -rf "$D"

# ---------------------------------------------------------------------------
# artifact-freshness kill-tests (2026-10-12). The step is headed
# "committed == generated", so it must compare the worktree against HEAD and it
# must see additions. It compared against the INDEX, which made a regenerated
# artifact that conformance/ci/selftest.sh had already staged (it did so until
# 2026-10-12, see case 18) read as fresh: on
# the 2026-10-12 run it reported the pip bundle up to date while the committed
# bundle was missing three fixes. Cases 8-14 exist because an adversarial pass
# found six surviving mutants of the first version of this block.
# ---------------------------------------------------------------------------
freshrepo() {   # a scratch repo holding the three generated paths + stub generators
  local d; d="$(mktemp -d)"
  mkdir -p "$d/public" "$d/docs" "$d/conformance/coverage" \
           "$d/packaging/spck_conformance/_bundle/conformance/checks" \
           "$d/packaging/spck_conformance/_bundle/conformance/requirements"
  printf '{"v":1}\n'  > "$d/public/coverage.json"
  printf 'matrix v1\n' > "$d/docs/spec-coverage-matrix.md"
  printf 'old = 1\n'   > "$d/packaging/spck_conformance/_bundle/conformance/checks/m.py"
  printf 'ROW-1\n'     > "$d/packaging/spck_conformance/_bundle/conformance/requirements/r.txt"
  printf 'unrelated\n' > "$d/README.md"
  cp "$ROOT/packaging/artifact_freshness.sh" "$d/packaging/"
  # stub generators: --regenerate must call these two exact paths
  printf '#!/usr/bin/env bash\nexit 0\n' > "$d/packaging/sync_bundle.sh"
  printf 'import sys\nsys.exit(0)\n'     > "$d/conformance/coverage/matrix.py"
  ( cd "$d" && git init -q && git add -A \
      && git -c user.name=t -c user.email=t@t commit -q -m generated ) >/dev/null 2>&1
  echo "$d"
}
fresh()  { if [ -n "${2:-}" ]; then ( cd "$1" && bash packaging/artifact_freshness.sh "$2" 2>&1 );
           else ( cd "$1" && bash packaging/artifact_freshness.sh 2>&1 ); fi; }

echo "artifact-freshness kill-tests"

# 5. the defect that bit us: regenerated and STAGED (by selftest.sh at the time),
# not committed
D="$(freshrepo)"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
( cd "$D" && git add packaging/spck_conformance/_bundle ) >/dev/null 2>&1
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "m.py"; then ok "staged-but-uncommitted bundle -> ✗ naming it"; else bad "STAGED stale bundle read as FRESH (rc=$RC) — index compared, not HEAD:"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 6. unstaged drift still reds
D="$(freshrepo)"; printf 'matrix v2\n' > "$D/docs/spec-coverage-matrix.md"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "spec-coverage-matrix.md"; then ok "unstaged matrix drift -> ✗ naming it"; else bad "unstaged drift not caught (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 7. an honest tree passes, so the guard is not simply always-red
D="$(freshrepo)"; OUT="$(fresh "$D")"; RC=$?
if [ $RC -eq 0 ]; then ok "honest tree -> PASS"; else bad "honest tree red (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 8. ADDITIONS: git diff never reports an untracked file, and merchant_checks.py
# glob-discovers merchant_checks_*.py at runtime, so a new module that reaches the
# bundle uncommitted makes the shipped wheel run FEWER checks, silently.
D="$(freshrepo)"
printf 'new = 1\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/merchant_checks_99_zz.py"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "merchant_checks_99_zz.py"; then ok "untracked new bundle module -> ✗ naming it"; else bad "untracked ADDITION read as fresh (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 9. coverage.json must be in the watched set (kills: drop it from PATHS)
D="$(freshrepo)"; printf '{"v":2}\n' > "$D/public/coverage.json"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "coverage.json"; then ok "coverage.json drift -> ✗ naming it"; else bad "coverage.json unwatched (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 10. the WHOLE bundle is watched, not just checks/ (kills: narrow the pathspec)
D="$(freshrepo)"; printf 'ROW-2\n' > "$D/packaging/spck_conformance/_bundle/conformance/requirements/r.txt"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "r.txt"; then ok "bundle requirements/ drift -> ✗ naming it"; else bad "bundle subtree unwatched (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 11. it speaks ONLY about generated artifacts (kills: drop the pathspec entirely)
D="$(freshrepo)"; printf 'edited\n' > "$D/README.md"
OUT="$(fresh "$D")"; RC=$?
if [ $RC -eq 0 ]; then ok "unrelated file drift -> PASS (scoped to generated paths)"; else bad "unrelated drift reds the gate (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 12. --regenerate actually regenerates (kills: delete or rename the block)
D="$(freshrepo)"
printf '#!/usr/bin/env bash\nprintf "old = 1\\n" > packaging/spck_conformance/_bundle/conformance/checks/m.py\nexit 0\n' > "$D/packaging/sync_bundle.sh"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
OUT="$(fresh "$D" --regenerate)"; RC=$?
if [ $RC -eq 0 ]; then ok "--regenerate restored the artifact -> PASS"; else bad "--regenerate did not run the generator (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 13. a FAILED generator must not read as fresh (kills: swallow it with || true)
D="$(freshrepo)"
printf '#!/usr/bin/env bash\nexit 3\n' > "$D/packaging/sync_bundle.sh"
OUT="$(fresh "$D" --regenerate)"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "sync_bundle"; then ok "generator exit 3 -> ✗ naming it"; else bad "FAILED generator read as fresh (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 14. same verdict from a subdirectory (kills: relative paths with no cd to root)
D="$(freshrepo)"; printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
OUT="$( cd "$D/docs" && bash ../packaging/artifact_freshness.sh 2>&1 )"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "m.py"; then ok "run from a subdirectory -> ✗ naming it"; else bad "fails GREEN from a subdirectory (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 15. explicit paths replace the default set (what CI passes). Drift inside the
# named path reds; drift outside it does not, so the CI step keeps its own scope.
D="$(freshrepo)"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
printf '{"v":9}\n' > "$D/public/coverage.json"
OUT="$( cd "$D" && bash packaging/artifact_freshness.sh packaging/spck_conformance/_bundle 2>&1 )"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "m.py" && ! echo "$OUT" | grep -q "coverage.json"; then ok "explicit path scopes the gate -> ✗ bundle only"; else bad "explicit path ignored (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 16. an EMPTY path argument must not blank the watched set (it would make the
# gate pass by having nothing to compare). Found by case 5-11 going red when the
# explicit-paths mode of case 15 first treated "" as a selection.
D="$(freshrepo)"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
OUT="$( cd "$D" && bash packaging/artifact_freshness.sh "" 2>&1 )"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -q "m.py"; then ok "empty path argument -> ✗ default set kept"; else bad "empty path argument blanked the gate (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 17. a MISTYPED flag must not fall through as a pathspec matching nothing, which
# would exit 0 having compared no artifact (how `rename-regen` hid from case 12).
D="$(freshrepo)"
printf 'new = 2\n' > "$D/packaging/spck_conformance/_bundle/conformance/checks/m.py"
OUT="$( cd "$D" && bash packaging/artifact_freshness.sh --regenerte 2>&1 )"; RC=$?
if [ $RC -ne 0 ] && echo "$OUT" | grep -qi "unknown option"; then ok "mistyped flag -> ✗ rejected"; else bad "mistyped flag accepted as a pathspec (rc=$RC):"; echo "$OUT" | tail -3; fi
rm -rf "$D"

# 18. the test runner must not WRITE git state. selftest.sh used to re-sync the
# bundle and `git add` it, so running the tests mutated the index. That made
# preflight step 2 compare a staged artifact against the index and call it fresh
# (the 2026-10-12 false green), it never achieved its stated purpose because a
# path-limited commit ignores the staged bundle, and it could manufacture drift by
# copying unstaged source into a committed bundle. A tripwire, so a future edit
# cannot quietly reintroduce it; the behavioural protection is cases 5, 6 and 12
# plus packaging/validate_bundle.py, which names an unsynced module and exits 1.
hits="$(grep -nE '(^|[^[:alnum:]_./-])git([[:space:]]|$)' "$ROOT/conformance/ci/selftest.sh" \
        | grep -vE '^[0-9]+:[[:space:]]*#' || true)"
if [ -z "$hits" ]; then ok "selftest.sh runs no git command (no index writes from a test run)"; else bad "selftest.sh writes or reads git state:"; echo "$hits" | sed 's/^/    /'; fi

# 19-21. SKIP THEATRE. selftest.sh is `set -uo` (no -e), so a bare serve_golden.sh
# call lets the suite carry on against a dead server, and run_suite SKIPs every
# server-backed gate instead of failing. Locally that is 12 gates silently skipped
# and preflight still printing "PREFLIGHT PASS — shippable", because step 1 greps
# only "N passed" and never the skip count. CI does not have the hole: it passes
# --require-server (conformance.yml), which also turns a missing `uv` toolchain into
# a failure. These three keep the local gate honest, so "ran every CI job locally"
# means the same thing in both places.
sg="$(grep -nE 'serve_golden\.sh' "$ROOT/conformance/ci/selftest.sh" | grep -vE '^[0-9]+:[[:space:]]*#' || true)"
if echo "$sg" | grep -qE '(^|:)[[:space:]]*(if|.*\|\||.*&&)' ; then
  ok "selftest.sh checks serve_golden.sh's exit code"
else
  bad "selftest.sh calls serve_golden.sh unguarded on a set -uo script (a dead golden becomes 12 silent skips):"; echo "$sg" | sed 's/^/    /'
fi

rs="$(grep -nE 'run_suite\.py' "$ROOT/conformance/ci/selftest.sh" | grep -vE '^[0-9]+:[[:space:]]*#' || true)"
if echo "$rs" | grep -q -- '--require-server'; then
  ok "selftest.sh passes --require-server (same as CI)"
else
  bad "selftest.sh omits --require-server, so a down server or missing toolchain SKIPs instead of failing:"; echo "$rs" | sed 's/^/    /'
fi

# the requirement is behavioural: preflight must EXTRACT a skip count from the
# selftest log and refuse the run when it is non-zero. Asserting the word appears on
# the "GREEN" line itself was a proxy that the real fix does not satisfy.
# case 21 WAS a source grep over preflight step 1 asserting it "contains a skip check".
# That tested an implementation detail, not the requirement, and an independent review
# showed it could not kill the mutant that matters: `bad` -> `ok` keeps the grepped
# pattern and switches the refusal off. The requirement is now asserted behaviourally by
# cases 22-25 (exact exit codes from packaging/selftest_summary.sh against crafted logs)
# plus the delegation check below, which together DO kill that mutant. Strictly stronger,
# so the weaker grep is retired rather than kept as decoration.
# 22-25. BEHAVIOURAL, not a source grep. Cases 19-21 assert that preflight's step 1
# CONTAINS a skip check; they do not assert it REFUSES. A one-word edit from `bad` to
# `ok` keeps that source pattern and switches the refusal off unseen, and cases 19-21
# stay green. The verdict now lives in packaging/selftest_summary.sh, which is a pure
# function of a log file, so these cases assert exit codes instead of text.
SS="$ROOT/packaging/selftest_summary.sh"
mklog(){ printf '%s\n' "boot ok" "$1" "GREEN" > "$2"; }

L="$(mktemp)"; mklog "146 passed · 0 failed · 0 skipped" "$L"
out="$(bash "$SS" "$L" 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then ok "summary 0 skipped -> accepted (rc 0)"; else bad "a clean 0-skipped summary was refused (rc=$rc): $out"; fi

mklog "140 passed · 0 failed · 6 skipped" "$L"
out="$(bash "$SS" "$L" 2>&1)"; rc=$?
if [ $rc -eq 1 ]; then ok "summary 6 skipped -> REFUSED (rc 1)"; else bad "6 skipped gates: want rc 1, got rc=$rc: $out"; fi

mklog "140 passed · 2 failed · 0 skipped" "$L"
out="$(bash "$SS" "$L" 2>&1)"; rc=$?
if [ $rc -eq 1 ]; then ok "summary 2 failed -> REFUSED (rc 1)"; else bad "2 failed gates: want rc 1, got rc=$rc: $out"; fi

printf '%s\n' "boot ok" "no summary here" > "$L"
out="$(bash "$SS" "$L" 2>&1)"; rc=$?
if [ $rc -eq 2 ]; then ok "no summary line -> REFUSED (rc 2)"; else bad "no summary: want rc 2, got rc=$rc: $out"; fi
rm -f "$L"

# preflight must DELEGATE to it, so the refusal cannot be switched off in preflight itself
if grep -q 'selftest_summary.sh' "$ROOT/packaging/preflight.sh"; then
  ok "preflight delegates the verdict to selftest_summary.sh"
else
  bad "preflight still decides the skip verdict inline, where a one-word edit switches it off"
fi
# 26-27. preflight STEP 1 ITSELF, behaviourally. The delegation grep above is satisfied by
# a comment naming the file, so it cannot kill the five one-word mutants an independent
# review found: `bash` -> `true` on the delegation line, pf_rc forced to 0, `-eq 0` -> `-ge 0`,
# the delegation line deleted, and `bad` -> `ok` in the else branch (mutant P1 transplanted).
# Each left the whole suite green. Running the step against a stub selftest.sh kills all five.
pfroot(){ # $1 = the summary the stub selftest.sh prints, $2 = its exit code (default 0)
  # A failed mktemp -d used to return "", and `cd ""` is a NO-OP in bash, so the nested
  # preflight would run from the suite working directory and fire the REAL step 1: the full
  # gate run, servers and all, from inside a hermetic kill-test. Fail loudly instead.
  local d; d="$(mktemp -d)" || { echo "pfroot: mktemp -d failed" >&2; return 1; }
  [ -n "$d" ] && [ -d "$d" ] || { echo "pfroot: mktemp -d gave no directory" >&2; return 1; }
  mkdir -p "$d/packaging" "$d/conformance/ci"
  cp "$ROOT/packaging/preflight.sh" "$ROOT/packaging/selftest_summary.sh" "$d/packaging/"
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "%s"\nexit %s\n' "$1" "${2:-0}" > "$d/conformance/ci/selftest.sh"
  printf '%s' "$d"
}

D="$(pfroot "146 passed · 0 failed · 0 skipped")"
OUT="$(cd "$D" && PREFLIGHT_SELFTEST_LOG="$D/log" bash packaging/preflight.sh --selftest-only 2>&1)"; RC=$?
if [ $RC -eq 0 ] && printf '%s' "$OUT" | grep -q '✓'; then
  ok "preflight step 1 on a 0-skipped run -> exit 0 and a ✓"
else
  bad "preflight step 1 refused a clean run (rc=$RC):"; printf '%s' "$OUT" | tail -3 | sed 's/^/    /'
fi
rm -rf "$D"

D="$(pfroot "140 passed · 0 failed · 6 skipped")"
OUT="$(cd "$D" && PREFLIGHT_SELFTEST_LOG="$D/log" bash packaging/preflight.sh --selftest-only 2>&1)"; RC=$?
if [ $RC -eq 1 ] && printf '%s' "$OUT" | grep -q '✗'; then
  ok "preflight step 1 on a 6-skipped run -> exit 1 and a ✗"
else
  bad "preflight step 1 ACCEPTED a 6-skipped run (rc=$RC) — the refusal is off:"; printf '%s' "$OUT" | tail -3 | sed 's/^/    /'
fi
rm -rf "$D"

# 28. the DEFAULT log path. Cases 26-27 both SET PREFLIGHT_SELFTEST_LOG, so the `:-` default
# never evaluated and they passed while the real preflight was broken: a blanket replace had
# rewritten the definition to SELFTEST_LOG="${PREFLIGHT_SELFTEST_LOG:-$SELFTEST_LOG}", which
# is self-referential and unbound under set -u. A test that only drives the overridden branch
# cannot see the one branch every real run takes.
D="$(pfroot "146 passed · 0 failed · 0 skipped")"
OUT="$(cd "$D" && env -u PREFLIGHT_SELFTEST_LOG bash packaging/preflight.sh --selftest-only 2>&1)"; RC=$?
if [ $RC -eq 0 ] && ! printf '%s' "$OUT" | grep -qi 'unbound\|parameter not set'; then
  ok "preflight step 1 works with no PREFLIGHT_SELFTEST_LOG set (the real path)"
else
  bad "preflight step 1 broke on its own default log path (rc=$RC):"; printf '%s' "$OUT" | tail -3 | sed 's/^/    /'
fi
rm -rf "$D"

# 29. each run must get its OWN diagnostics. This is a PROPERTY, and the first version of
# this case did not test it: it planted a sentinel at the one literal path /tmp/preflight_
# selftest.log and required it to survive, which proves only that THAT NAME is unused. A
# review killed it with two mutants the suite reported green on: a DIFFERENT fixed shared
# name, and the original shared name with `>` changed to `>>`. Both are "one path shared by
# every run", the defect itself. So assert the property instead: drive two default-path runs
# whose stub selftest FAILS, which makes preflight print "see <path>", and require the two
# paths to differ, to be neither of the legacy shared names, and each file to hold only its
# own run. It touches no shared path at all, which also removes every way the old sentinel
# version could damage the real file of an operator: no backup to orphan on an interrupt, no
# symlink to follow, no directory to move a backup into, and no rm -f reachable when mktemp
# fails.
pf_run_log(){ # $1 = marker; echoes the log path preflight reported for that run
  local d out; d="$(pfroot "$1" 1)" || return 1
  out="$(cd "$d" && env -u PREFLIGHT_SELFTEST_LOG bash packaging/preflight.sh --selftest-only 2>&1)"
  rm -rf "$d"
  printf '%s' "$out" | sed -n 's/.*see \([^ )]*\).*/\1/p' | head -1
}
L29A="$(pf_run_log MARKER-RUN-A-29)"; L29B="$(pf_run_log MARKER-RUN-B-29)"
s29_why=""
[ -n "$L29A" ] && [ -n "$L29B" ] || s29_why="preflight printed no log path"
[ "$L29A" != "$L29B" ] || s29_why="${s29_why:-both runs used the same path $L29A}"
case "$L29A" in */preflight_selftest.log|*/preflight_selftest_shared.log)
  s29_why="${s29_why:-run A used a legacy shared name $L29A}" ;; esac
if [ -z "$s29_why" ]; then
  grep -q MARKER-RUN-A-29 "$L29A" 2>/dev/null || s29_why="run A log lacks its own marker"
  grep -q MARKER-RUN-B-29 "$L29A" 2>/dev/null && s29_why="${s29_why:-run A log also holds run B}"
fi
if [ -z "$s29_why" ]; then
  ok "each preflight run gets its own diagnostics ($(basename "$(dirname "$L29A")") vs $(basename "$(dirname "$L29B")"))"
else
  bad "preflight diagnostics are SHARED between runs: $s29_why. On a failure the operator is sent to a log describing a different run"
fi

echo
if [ $FAIL -eq 0 ]; then echo "release guards kill-tests: PASS"; exit 0; else echo "release guards kill-tests: FAIL"; exit 1; fi
