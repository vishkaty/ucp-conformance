#!/usr/bin/env python3
"""D4-06 (E1) — official-suite cross-checker with a self-expiring allowlist.

Failing-first: conformance/ci/official_crosscheck.py does not exist. Target: `--selftest` is
hermetic — a synthetic junit {A fail, B pass} against an allowlist {A unexpired, B unexpired,
C expired-by-date} classifies A USED, B STALE (red), C EXPIRED (red); `or_pr_merged` resolved
true through a STUBBED `gh` -> EXPIRED; a `samples_pin_not` that no longer equals the lock's
samples pin -> EXPIRED (the pin moved; RV2 T5).

Run:  python3 -m pytest conformance/ci/test_official_crosscheck.py -q
"""
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib_dir = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SCRIPT = HERE / "official_crosscheck.py"


def test_selftest_used_stale_expired_with_stubbed_gh():
    r = subprocess.run([sys.executable, str(SCRIPT), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for want in ("A used", "B stale", "C expired", "or_pr_merged", "samples_pin_not", "PASS"):
        assert want in r.stdout, r.stdout


def test_allowlist_file_registers_an_expiry_clock():
    import json
    regs = json.loads((HERE.parent / "coverage" / "expiry_registers.json").read_text())["registers"]
    assert any(r["file"] == "conformance/ci/official_crosscheck_allowlist.json" for r in regs)
    import official_crosscheck as oc   # red: ModuleNotFoundError
    assert callable(oc.classify)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
