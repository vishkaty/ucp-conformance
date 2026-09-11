#!/usr/bin/env python3
"""D4-08 (E2, decision 4) — discovery-live sampler (GET /.well-known/ucp only, named UA, robots,
<=1/s, <=1/store/day, <=50/day, hard DENYLIST, owner's machine only), capture format
`discovery-capture/1`, OFFLINE grader (no socket), role-scoped earnable rows, evidence class
`discovery-live` (predicate reaches load_capture and >=3 distinct domains within 30 days;
never promoted to live-wire). Failing-first: discovery_live.py, probe_policy.json and the
evidence class do not exist.

Run:  python3 -m pytest conformance/ci/test_discovery_live.py -q
"""
import datetime
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


def test_matrix_export_carries_the_discovery_live_slot():
    """W1 integration: matrix.export_json()['versions']['2026-08-25']['discovery_live'] must be the
    {stores, as_of} aggregate of conformance/coverage/discovery_reach.json (5 stores, 2026-09-11 —
    the D4-08 smoke). D4-08's `_discovery_live_slot` read `HERE`, a name matrix.py never defined,
    and its bare `except Exception: return None` hid the NameError — the slot was None on the
    lane branch too, so D5-13's CLAIM-COV-007 sentence could never render."""
    import json
    sys.path.insert(0, str(HERE.parents[0] / "coverage"))
    import matrix
    reach = json.load(open(HERE.parents[0] / "coverage" / "discovery_reach.json"))
    assert reach["stores"] >= 3
    slot = matrix._discovery_live_slot("2026-08-25")
    assert slot == {"stores": reach["stores"], "as_of": reach["as_of"]}, slot
    assert matrix._discovery_live_slot("2026-04-08") is None
    assert matrix.export_json()["versions"]["2026-08-25"]["discovery_live"] == slot


def test_reach_counts_only_stores_whose_document_was_graded(tmp_path):
    """W1 integration (D4-08 x D5-13): a capture whose fetch failed (http.status 0, body None,
    every earnable row `not-applicable`) is not a graded store — the public CLAIM-COV-007
    sentence says "N stores' documents graded by this suite". The 2026-09-11 smoke has one such
    capture (jlique.com, DNS failure) and D4's aggregate counted it (stores 5, while every row's
    `domains` is 4 and discovery_shapes.discovery_reach says 4 stores · 1 malformed)."""
    import json
    import discovery_live as dl
    day = tmp_path / "2026-09-11"; day.mkdir()
    def cap(domain, status, grade):
        return {"schema": "discovery-capture/1", "domain": domain, "fetched_at": "2026-09-11T00:00:00Z",
                "http": {"status": status, "headers": {}, "redirects": 0}, "body": {} if status == 200 else None,
                "body_text": "", "body_sha256": "", "frame": "hf", "robots_checked": True, "grade": grade}
    good = {r: "clean-pass" for r in dl.EARNABLE}
    dead = {r: "not-applicable" for r in dl.EARNABLE}
    for i in range(3):
        (day / f"s{i}.example.json").write_text(json.dumps(cap(f"s{i}.example", 200, good)))
    (day / "dead.example.json").write_text(json.dumps(cap("dead.example", 0, dead)))
    r = dl.reach_from(tmp_path, today=datetime.date(2026, 9, 11))
    assert r["stores"] == 3, r
    assert all(v["domains"] == 3 for v in r["rows"].values()), r["rows"]
