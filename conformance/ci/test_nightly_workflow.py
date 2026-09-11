#!/usr/bin/env python3
"""D4-10 — nightly.yml (E1 cross-checker + E3 seqfuzz + B5b oracles feeds; NO discovery-live
job — decision 4: the sampler runs only on the owner's machine) and its validator
`validate_nightly_workflow.py --selftest`: jobs present, `timeout-minutes` on every job, ports
taken from ports.json and disjoint from the push-gate (sweep) ports, no `continue-on-error` on
an assertion step, no job named discovery-live, no step that writes under ops/.

Failing-first: .github/workflows/nightly.yml and the validator do not exist.

Run:  python3 -m pytest conformance/ci/test_nightly_workflow.py -q
"""
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VALIDATOR = HERE / "validate_nightly_workflow.py"
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"


def test_nightly_workflow_exists_with_the_three_jobs_and_no_discovery_live():
    import yaml
    assert NIGHTLY.exists(), "nightly.yml absent"
    doc = yaml.safe_load(NIGHTLY.read_text())
    jobs = doc["jobs"]
    for j in ("crosscheck", "seqfuzz", "oracles"):
        assert j in jobs, j
    assert "discovery-live" not in jobs and "discovery_live" not in jobs
    assert "schedule" in doc[True if True in doc else "on"] or "schedule" in doc.get("on", {})


def test_validator_selftest_passes():
    r = subprocess.run([sys.executable, str(VALIDATOR), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
