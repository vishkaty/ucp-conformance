#!/usr/bin/env python3
"""D4-05 (B5b) — pydantic third leg (python-sdk generated models) on the 2026-08-25 dual-oracle
corpus, in two venvs: `ucp-sdk==0.5.0` (the tag = PyPI latest; gated through
known_sdk_drops.json with `expires_on.pypi_ucp_sdk_gt`) and python-sdk main (report-only).

Failing-first: validate_dual_oracle.py has no `--pydantic` flag (argparse: unrecognized
arguments, rc 2) and pydantic_leg.py does not exist. Target: `--selftest --version 2026-08-25
--pydantic` runs the B5b cases — the #43 payload with the referee disabled reds on the
pydantic-tag leg alone; a JWK without `crv` is accepted by the tag and rejected by the referee
(acknowledged drop); deleting the entry reds; an offline venv exits 2, never green.

Run:  python3 -m pytest conformance/selfcheck/test_pydantic_leg.py -q
"""
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
GATE = HERE / "validate_dual_oracle.py"


def test_pydantic_flag_exists_and_selftest_runs():
    r = subprocess.run([sys.executable, str(GATE), "--selftest", "--version", "2026-08-25", "--pydantic"],
                       capture_output=True, text=True)
    assert "unrecognized arguments" not in r.stderr, r.stderr
    assert r.returncode in (0, 2), r.stdout + r.stderr          # 2 = venv not built (honest skip)
    if r.returncode == 0:
        assert "pydantic-tag" in r.stdout and "case 7" in r.stdout, r.stdout


def test_pydantic_leg_module_reports_unmapped_as_not_judged():
    sys.path.insert(0, str(HERE))
    import pydantic_leg  # noqa: F401  (red: ModuleNotFoundError)
    assert pydantic_leg.model_for("schemas/nope.json", None) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
