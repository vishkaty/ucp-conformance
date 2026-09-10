#!/usr/bin/env python3
"""D4-02 (B3) — gen_reach_report.py --selftest is the hermetic kill-proof of the CI drift step.

Failing-first: today the generator has no `--selftest` (and no `--check`): any invocation
probes the differential targets and, with none reachable, refuses to write (rc 2). Target:
`--selftest` plants a status flip in a temp copy of the committed report and asserts a
non-empty drift, then an unchanged rerun asserts none; `--check` regenerates without
writing and prints `reach report: N drift` (rc 1 when N > 0) for the workflow step.

Run:  python3 -m pytest conformance/coverage/test_gen_reach_selftest.py -q
"""
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
GEN = HERE / "gen_reach_report.py"


def test_selftest_flag_exists_and_passes():
    r = subprocess.run([sys.executable, str(GEN), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, f"rc {r.returncode}: {r.stdout}{r.stderr}"
    out = r.stdout
    assert "PASS" in out
    assert "flip" in out and "1 drift" in out, out          # planted flip → non-empty drift
    assert "0 drift" in out, out                            # unchanged rerun → none


def test_check_mode_writes_nothing():
    before = (HERE / "reach_report.json").read_bytes()
    r = subprocess.run([sys.executable, str(GEN), "--check"], capture_output=True, text=True)
    assert (HERE / "reach_report.json").read_bytes() == before
    # with no target reachable --check must refuse (rc 2), never report "0 drift"
    assert r.returncode in (0, 1, 2), r.stdout + r.stderr
    if r.returncode == 2:
        assert "0 drift" not in r.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
