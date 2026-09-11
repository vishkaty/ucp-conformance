#!/usr/bin/env python3
"""D4-09 (E6, decision 4) — ucpchecker discovery-verdict comparison: our offline grade of a
capture vs ucpchecker's public /status/{domain} page (never /api/), 24 h cache, <=50 pages/week,
documented divergence classes (legacy signing_keys, redirects, payment_handlers) ->
`expected-divergence`; anything else -> `candidate` (never auto-filed).
Failing-first: conformance/ci/ucpchecker_compare.py does not exist.

Run:  python3 -m pytest conformance/ci/test_ucpchecker_compare.py -q
"""
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "ucpchecker_compare.py"


def test_selftest_agreement_matrix_and_divergence_classes():
    r = subprocess.run([sys.executable, str(SCRIPT), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for want in ("agree", "expected-divergence", "candidate", "legacy-keys", "redirects", "PASS"):
        assert want in r.stdout, r.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
