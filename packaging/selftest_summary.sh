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

# run_suite prints exactly one "N passed · N failed · N skipped" line. Anchor the whole
# line and require EXACTLY ONE match. An independent review defeated the earlier loose
# version with four inputs that wrongly returned 0: two summaries (red then green, where
# `tail -1` took the green one), a later line QUOTING a green summary, a red summary with
# " 0 skipped" appearing again later on the same line (greedy last-occurrence), and a count
# too large for the shell's integer test, where `[` errored to stderr and returned false.
MATCHES="$(grep -cE '^[0-9]+ passed . [0-9]+ failed . [0-9]+ skipped$' "$LOG" 2>/dev/null || true)"
if [ "${MATCHES:-0}" -eq 0 ]; then
  echo "selftest_summary: no 'N passed / N failed / N skipped' line in $LOG — refusing to read it as green" >&2
  exit 2
fi
if [ "${MATCHES:-0}" -ne 1 ]; then
  echo "selftest_summary: $MATCHES summary lines in $LOG — refusing to choose between them" >&2
  exit 2
fi
SUMMARY="$(grep -E '^[0-9]+ passed . [0-9]+ failed . [0-9]+ skipped$' "$LOG")"

# Decide green on the LITERAL text, with no arithmetic, so no count can overflow the shell
# integer test and no regex greediness can pick a later zero.
case "$SUMMARY" in
  *" 0 failed "*" 0 skipped") ;;                                  # the only green shape
  *)
    echo "$SUMMARY"
    echo "selftest_summary: not green — a non-zero failed or skipped count (a skip is not a pass; a missing server or toolchain skips)" >&2
    exit 1 ;;
esac

echo "$SUMMARY"
exit 0
