#!/usr/bin/env python3
"""
test_battery_record_split.py — the R11 battery's `--out` / `--record` split (W1 D3
carry-over; the CI-1 class, decision 24: in-run freshness, no churn of tracked files).

Before this split every battery run rewrote the TRACKED conformance/testbed/golden-0825/
battery/LAST_RUN.json (`ran_at`, `ran_at_iso`), so `run_suite --battery` on a UTC-rollover
day left the tree dirty for packaging/preflight.sh's clean-tree step (W0-integration
§10.2 named it "same class as CI-1"). The contract now mirrors the agent lane's:
  * a plain battery run records NOTHING on disk;
  * `--out FILE` writes THIS run's report to FILE (run_suite's --battery hands it to
    RECORD_DIR/battery_LAST_RUN.json, which battery-freshness reads with --in-run);
  * `--record` rewrites the tracked LAST_RUN.json — the owner's release-time act.

Cases (unittest; hermetic — the writer is exercised directly, no golden boot):
  test_run_suite_hands_in_run_report     run_suite.battery_argv() carries `--out
                                         <RECORD_DIR>/battery_LAST_RUN.json` and the
                                         battery-freshness gate reads that same path
  test_out_writes_only_the_out_file      --out writes FILE; the tracked file keeps its
                                         bytes AND mtime, even when the clock has rolled
                                         over to the next UTC day
  test_record_rewrites_tracked           --record rewrites the tracked file (the release path)
  test_default_writes_nothing            neither flag: no file written anywhere
  test_cli_accepts_the_flags             the argparse surface exposes --out and --record
Run: python3 conformance/selfcheck/test_battery_record_split.py
"""
import json, os, pathlib, subprocess, sys, tempfile, time, unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TRACKED = ROOT / "conformance" / "testbed" / "golden-0825" / "battery" / "LAST_RUN.json"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "conformance" / "ci"))
import validate_golden_0825_battery as battery   # noqa: E402

RESULTS = [{"name": "m1", "verdict": "KILLED", "detail": "x"},
           {"name": "b1", "verdict": "KILLED", "detail": "y"}]


def _write(out=None, record=False, now=None):
    """Call the writer the way main() does after a run; `now` fakes the clock."""
    with mock.patch.object(battery.time, "time", return_value=now or time.time()):
        battery.write_report(ok=True, results=RESULTS, phase01_ok=True, killed=2, acked=0,
                             b_killed=1, b_total=1, out=out, record=record)


class BatteryRecordSplit(unittest.TestCase):
    def test_run_suite_hands_in_run_report(self):
        with tempfile.TemporaryDirectory() as d:
            code = ("import sys, json; sys.path.insert(0, 'conformance/ci'); import run_suite; "
                    "print(json.dumps({'battery': run_suite.battery_argv(), "
                    "'gate': [list(g[1]) for g in run_suite.gates('http://localhost:8182') "
                    "if g[0] == 'battery-freshness'][0]}))")
            env = dict(os.environ, RUN_SUITE_RECORD_DIR=d)
            r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
            doc = json.loads(r.stdout)
            argv, gate = doc["battery"], doc["gate"]
            self.assertIn("--out", argv, f"run_suite's battery invocation has no --out handoff: {argv}")
            out = argv[argv.index("--out") + 1]
            self.assertTrue(out.startswith(d), f"--out {out} is not under RECORD_DIR {d}")
            self.assertNotIn("--record", argv, "run_suite must never rewrite the tracked report")
            self.assertEqual(gate[gate.index("--in-run") + 1], out,
                             "battery-freshness must read the file the battery wrote")

    def test_out_writes_only_the_out_file(self):
        before, mtime = TRACKED.read_bytes(), TRACKED.stat().st_mtime_ns
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "battery_LAST_RUN.json"
            # a UTC rollover: the run happens "tomorrow" relative to the tracked report
            tomorrow = json.loads(before)["ran_at"] + 86400
            _write(out=out, now=tomorrow)
            self.assertTrue(out.exists(), "no in-run report written to --out")
            doc = json.loads(out.read_text())
            self.assertEqual(doc["killed"], 2)
            self.assertEqual(doc["behavior_total"], 1)
            self.assertEqual(doc["ran_at"], tomorrow)
        self.assertEqual(TRACKED.read_bytes(), before, "tracked LAST_RUN.json was rewritten by --out")
        self.assertEqual(TRACKED.stat().st_mtime_ns, mtime,
                         "tracked LAST_RUN.json was written (same bytes, new mtime) by --out")

    def test_record_rewrites_tracked(self):
        before, mtime = TRACKED.read_bytes(), TRACKED.stat().st_mtime_ns
        try:
            with tempfile.TemporaryDirectory() as d:
                out = pathlib.Path(d) / "also.json"
                _write(out=out, record=True, now=1_900_000_000.0)
                self.assertTrue(out.exists())
            self.assertNotEqual(TRACKED.read_bytes(), before, "--record must rewrite the tracked report")
            self.assertEqual(json.loads(TRACKED.read_text())["ran_at"], 1_900_000_000.0)
        finally:
            TRACKED.write_bytes(before)
            os.utime(TRACKED, ns=(mtime, mtime))

    def test_default_writes_nothing(self):
        before, mtime = TRACKED.read_bytes(), TRACKED.stat().st_mtime_ns
        _write()
        self.assertEqual(TRACKED.read_bytes(), before, "a plain run rewrote the tracked report")
        self.assertEqual(TRACKED.stat().st_mtime_ns, mtime, "a plain run touched the tracked report")

    def test_cli_accepts_the_flags(self):
        r = subprocess.run([sys.executable, str(HERE / "validate_golden_0825_battery.py"), "--help"],
                           capture_output=True, text=True)
        self.assertIn("--out", r.stdout)
        self.assertIn("--record", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
