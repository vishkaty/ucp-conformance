#!/usr/bin/env python3
"""W1 carry-over (lane D4) — per-gate runtime budgets in run_suite.

`dual-oracle-0825` measured 82-130 s on green CI runs against run_gate's flat 180 s
timeout and TIMED OUT once on main (run 34560082606, rc 124) while the identical tree
was green minutes earlier (W0-integration.md §11). A timeout on that gate must be a
budget decision with the typical time recorded, never a flaky verdict.

Failing-first: run_suite has no `gate_budget()` / `GATE_BUDGETS`, so the gate runs
under the flat default. Kill-proof (hermetic): a planted over-budget sleep through
run_gate must still come back rc 124 TIMEOUT — a budget is a ceiling, not a waiver.

Run:  python3 -m pytest conformance/ci/test_gate_budgets.py -q
"""
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_suite  # noqa: E402


def test_dual_oracle_0825_has_a_budget_above_the_flat_default_with_typical_recorded():
    budget = run_suite.gate_budget("dual-oracle-0825")
    assert budget >= 420, f"budget {budget}s is not above the 130 s observed max with headroom"
    assert budget > run_suite.DEFAULT_GATE_TIMEOUT
    entry = run_suite.GATE_BUDGETS["dual-oracle-0825"]
    assert "typical" in entry and "82" in entry["typical"], "typical CI time must be recorded"


def test_unlisted_gate_keeps_the_flat_default():
    assert run_suite.gate_budget("verdict") == run_suite.DEFAULT_GATE_TIMEOUT == 180


def test_planted_over_budget_sleep_still_reds():
    # kill-proof: a gate that outlives its budget is rc 124 TIMEOUT, never a pass
    r = run_suite.run_gate("planted-sleeper", ["sleep", "3"], timeout=1)
    assert r["rc"] == 124 and r["tail"].startswith("TIMEOUT"), r


def test_run_gate_default_timeout_is_the_gate_budget(monkeypatch):
    seen = {}

    def fake_run(argv, cwd, capture_output, text, timeout):
        seen["timeout"] = timeout
        class P:  # minimal CompletedProcess stand-in
            returncode, stdout, stderr = 0, "ok\n", ""
        return P()

    monkeypatch.setattr(run_suite.subprocess, "run", fake_run)
    run_suite.run_gate("dual-oracle-0825", ["true"])
    assert seen["timeout"] == run_suite.gate_budget("dual-oracle-0825")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
