#!/usr/bin/env python3
"""D4-01 (B5a) — the dual-oracle gate at spec 2026-08-25.

Failing-first: today `dual_oracle_referee.SCHEMA_BASE` has no 2026-08-25 entry, so
`get_referee("2026-08-25")` raises RefereeUnavailable, and validate_dual_oracle.py has no
`--version`. Target: the 08-25 referee base loads 116 schemas; `--selftest --version
2026-08-25` runs case 5 and the 04-08 output stays byte-stable. Since D4-04 (per-version oracle)
case 5 asserts the #43 boundary AGREES and the --def self-root path validates (both retired).

Run:  python3 -m pytest conformance/selfcheck/test_dual_oracle_0825.py -q
"""
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dual_oracle_referee as ref  # noqa: E402

GATE = HERE / "validate_dual_oracle.py"


def test_referee_base_0825_loads_116_schemas():
    try:
        r = ref.get_referee("2026-08-25")
    except ref.RefereeUnavailable as e:
        pytest.fail(f"RefereeUnavailable: {e}")
    assert r.schema_count == 116


def test_selftest_0825_case5_per_version_oracle_agrees_no_crash():
    # D4-04: the 08-25 layout runs the merged-main build; #43/#45 acknowledgements retired
    r = subprocess.run([sys.executable, str(GATE), "--selftest", "--version", "2026-08-25"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "case 5" in r.stdout and "crash ×0" in r.stdout and "acknowledged divergences at 2026-08-25: 0" in r.stdout


def test_0408_selftest_byte_stable_without_version_flag():
    a = subprocess.run([sys.executable, str(GATE), "--selftest"], capture_output=True, text=True)
    b = subprocess.run([sys.executable, str(GATE), "--selftest", "--version", "2026-04-08"],
                       capture_output=True, text=True)
    assert a.returncode in (0, 2) and a.returncode == b.returncode
    assert a.stdout == b.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
