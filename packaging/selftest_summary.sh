#!/usr/bin/env bash
# selftest_summary.sh — the VERDICT on a selftest log, as a pure function of the file.
#
#   bash packaging/selftest_summary.sh <logfile>
#     exit 0  the run is green: a summary line exists and reports 0 failed and 0 skipped
#     exit 1  the summary reports a non-zero failed or skipped count
#     exit 2  no summary line at all, so the log cannot be read as green
#   On every path it prints the summary line it found, or why it found none.
#
# Why this is its own file. preflight.sh used to decide this inline, and the guard cases
# in packaging/test_preflight_guards.sh asserted only that step 1 CONTAINED a skip check
# by grepping the source. That is not the requirement. An independent review showed a
# surviving mutant: changing `bad "selftest SKIPPED` to `ok "selftest SKIPPED` keeps the
# grepped pattern, switches the refusal off, and leaves those cases green. A one-word
# edit could disable the gate unseen. Extracting the verdict makes it a unit whose exit
# codes can be asserted against crafted logs, which is what cases 22-25 now do -- the
# same reason packaging/artifact_freshness.sh was extracted out of preflight step 2.
#
# A skip is not a pass: run_suite SKIPs a gate whose server or toolchain is missing, and
# a local run without `uv` and `cargo` on PATH once reported "128 passed, 5 failed, 13
# skipped" while preflight printed "PREFLIGHT PASS - shippable" over it.
set -uo pipefail

LOG="${1:-}"
if [ -z "$LOG" ] || [ ! -f "$LOG" ]; then
  echo "selftest_summary: no such log file: ${LOG:-<none>}" >&2
  exit 2
fi

# run_suite prints "N passed · N failed · N skipped" (middle dots). Match on the words so
# the separator can change without silently matching nothing.
SUMMARY="$(grep -E '[0-9]+ passed .* [0-9]+ failed .* [0-9]+ skipped' "$LOG" | tail -1)"
if [ -z "$SUMMARY" ]; then
  echo "selftest_summary: no 'N passed / N failed / N skipped' line in $LOG — refusing to read it as green" >&2
  exit 2
fi

num_before() { printf '%s' "$SUMMARY" | sed -nE "s/.*[^0-9]([0-9]+) $1.*/\\1/p"; }
PASSED="$(num_before passed)"
FAILED="$(num_before failed)"
SKIPPED="$(num_before skipped)"

if [ -z "$FAILED" ] || [ -z "$SKIPPED" ]; then
  echo "selftest_summary: could not read the failed/skipped counts out of: $SUMMARY" >&2
  exit 2
fi

echo "$SUMMARY"
if [ "$FAILED" -gt 0 ]; then
  echo "selftest_summary: ${FAILED} gate(s) FAILED" >&2
  exit 1
fi
if [ "$SKIPPED" -gt 0 ]; then
  echo "selftest_summary: ${SKIPPED} gate(s) SKIPPED — a skip is not a pass (a missing server or toolchain skips)" >&2
  exit 1
fi
exit 0
