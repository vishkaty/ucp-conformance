#!/usr/bin/env python3
"""
test_evidence_handoff.py — CI-1 (decision 24: in-run freshness, no churn of tracked files).

The agent-lane gate must NOT rewrite the tracked conformance/agent/agent_run_evidence.json
during a run_suite/selftest run: that file ships in the pip bundle, and every entry's
`date` moved 2026-09-10 -> 2026-09-11 at the UTC rollover inside CI run 34547382551, so
`sync_bundle.sh && git diff --exit-code packaging/spck_conformance/_bundle` reported drift
on a source tree nobody had changed. The contract mirrors the R11 battery's `--battery
(in-run)` path: run_suite's agent-lane hands THIS run's evidence to a RECORD_DIR file,
agent-governance consumes it with `--in-run`, and the tracked file is rewritten only by an
explicit `run_agent.py --record` (owner, release time).

Cases (unittest; boots the in-process sandbox twice, ~10 s; never touches the tracked file):
  test_run_suite_wires_in_run_handoff   agent-lane carries `--evidence-out <RECORD_DIR>/…`
                                        and agent-governance `--in-run` the same path
  test_lane_leaves_tracked_file_alone   running the lane as run_suite does leaves the
                                        tracked evidence byte-identical AND unwritten
                                        (mtime), while the in-run file carries today's
                                        date for every sound check at the sandbox version
  test_governance_passes_on_in_run      agent_governance.py --in-run <file> -> rc 0 and
                                        names the in-run source
  test_default_invocation_writes_nothing `run_agent.py` with no flags writes nothing
                                        (the pre-CI-1 default rewrote the tracked file)
Run: cd conformance/agent && python3 test_evidence_handoff.py
"""
import datetime, json, os, pathlib, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TRACKED = HERE / "agent_run_evidence.json"
PY = sys.executable


def _gate_cmd(name, record_dir):
    """The exact argv run_suite builds for gate `name` with RUN_SUITE_RECORD_DIR=record_dir
    (a fresh interpreter, because run_suite resolves RECORD_DIR at import)."""
    code = ("import sys, json; sys.path.insert(0, 'conformance/ci'); import run_suite; "
            f"print(json.dumps([list(g[1]) for g in run_suite.gates('http://localhost:8182') if g[0] == {name!r}][0]))")
    env = dict(os.environ, RUN_SUITE_RECORD_DIR=str(record_dir))
    r = subprocess.run([PY, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout)


class EvidenceHandoff(unittest.TestCase):
    def test_run_suite_wires_in_run_handoff(self):
        with tempfile.TemporaryDirectory() as d:
            lane = _gate_cmd("agent-lane", d)
            gov = _gate_cmd("agent-governance", d)
            self.assertIn("--evidence-out", lane, f"agent-lane has no in-run evidence handoff: {lane}")
            out = lane[lane.index("--evidence-out") + 1]
            self.assertTrue(out.startswith(d), f"evidence-out {out} is not under RECORD_DIR {d}")
            self.assertIn("--in-run", gov, f"agent-governance does not consume in-run evidence: {gov}")
            self.assertEqual(gov[gov.index("--in-run") + 1], out, "governance must read the lane's in-run file")

    def test_lane_leaves_tracked_file_alone(self):
        before, mtime = TRACKED.read_bytes(), TRACKED.stat().st_mtime_ns
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "agent_run_evidence.json"
            r = subprocess.run([PY, str(HERE / "run_agent.py"), "--evidence-out", str(out)],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
            self.assertEqual(TRACKED.read_bytes(), before, "tracked agent_run_evidence.json was rewritten by the lane")
            self.assertEqual(TRACKED.stat().st_mtime_ns, mtime, "tracked agent_run_evidence.json was written (same bytes, new mtime) by the lane")
            self.assertTrue(out.exists(), "no in-run evidence file produced")
            doc = json.loads(out.read_text())
            today = datetime.date.today().isoformat()
            dates = {e["date"] for v in doc["evidence"].values() for e in v.values()}
            self.assertEqual(dates, {today}, f"in-run evidence must carry today's date only, got {dates}")
            self.assertEqual(doc.get("unrun_versions"), json.loads(before)["unrun_versions"],
                             "in-run evidence must preserve the tracked unrun_versions declarations")
            self.assertGreaterEqual(len(doc["evidence"]), 1)

    def test_governance_passes_on_in_run(self):
        before = TRACKED.read_bytes()
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "agent_run_evidence.json"
            subprocess.run([PY, str(HERE / "run_agent.py"), "--evidence-out", str(out)],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=600)
            r = subprocess.run([PY, str(HERE / "agent_governance.py"), "--in-run", str(out)],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-400:])
            self.assertIn("in-run", r.stdout, "governance must name the in-run evidence source")
        self.assertEqual(TRACKED.read_bytes(), before)

    def test_default_invocation_writes_nothing(self):
        before, mtime = TRACKED.read_bytes(), TRACKED.stat().st_mtime_ns
        r = subprocess.run([PY, str(HERE / "run_agent.py")], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout[-400:])
        self.assertEqual(TRACKED.read_bytes(), before)
        self.assertEqual(TRACKED.stat().st_mtime_ns, mtime,
                         "run_agent.py with no flags rewrote the tracked evidence file (pre-CI-1 default)")


if __name__ == "__main__":
    unittest.main(verbosity=1)
