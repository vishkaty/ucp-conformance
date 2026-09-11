#!/usr/bin/env python3
"""D4-08 (E2, decision 4) — discovery-live sampler (GET /.well-known/ucp only, named UA, robots,
<=1/s, <=1/store/day, <=50/day, hard DENYLIST, owner's machine only), capture format
`discovery-capture/1`, OFFLINE grader (no socket), role-scoped earnable rows, evidence class
`discovery-live` (predicate reaches load_capture and >=3 distinct domains within 30 days;
never promoted to live-wire). Failing-first: discovery_live.py, probe_policy.json and the
evidence class do not exist.

Run:  python3 -m pytest conformance/ci/test_discovery_live.py -q
"""
import json
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "conformance" / "coverage"))
SCRIPT = HERE / "discovery_live.py"


def test_policy_file_is_decision_4():
    p = json.loads((HERE / "probe_policy.json").read_text())
    assert p["methods_allowed"] == ["GET"] and p["paths_allowed"] == ["/.well-known/ucp"]
    assert p["rate"]["per_second"] <= 1 and p["rate"]["per_store_per_day"] == 1 and p["rate"]["per_day"] <= 50
    assert p["retention_days"] == 90 and p["redirects"] is False and p["robots"] is True
    assert p["user_agent"].startswith("spck-conformance-research/") and "vishal@katyal.ai" in p["user_agent"]


def test_evidence_class_discovery_live_exists_and_never_outranks_live_wire():
    import evidence
    assert "discovery-live" in evidence.CLASSES
    assert evidence._RANK["discovery-live"] < evidence._RANK["live-wire"]
    assert evidence._RANK["discovery-live"] > evidence._RANK["fixture-schema"]


def test_selftest_denylist_robots_rate_grader_evidence_and_no_socket():
    r = subprocess.run([sys.executable, str(SCRIPT), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for want in ("case a", "case b", "case c", "case d", "case e", "case f", "GITHUB_ACTIONS", "PASS"):
        assert want in r.stdout, r.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
