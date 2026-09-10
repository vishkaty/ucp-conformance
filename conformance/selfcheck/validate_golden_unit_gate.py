#!/usr/bin/env python3
"""
validate_golden_unit_gate.py — `golden-0825-unit` (D3-29): golden-0825's own unit and
smoke tests are EXECUTED by run_suite, not merely present in the tree.

Every D3 task names a server test (server/*_test.py) or a smoke test (smoke/) as its
failing-first test; until this gate existed those ran only by hand (`uv run pytest`),
so a red one could sit in the tree unnoticed (RV2 #10). This gate runs

    cd conformance/testbed/golden-0825/server && uv run --group dev pytest -q ../smoke .

and propagates pytest's verdict. Exit codes: 0 = every test passed; 1 = a test failed
(or pytest could not collect); 2 = `uv` is not on PATH (honest SKIP, the same code every
oracle-backed gate uses when its toolchain is absent; run_suite turns it into FAIL under
--require-server).

--selftest kill-tests the gate itself: a scratch copy of the server directory with a
PLANTED failing test must make the gate red (rc 1), and a PATH with no `uv` must make it
rc 2 -- never a silent green either way.

Usage:
    python3 conformance/selfcheck/validate_golden_unit_gate.py
    python3 conformance/selfcheck/validate_golden_unit_gate.py --selftest
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "conformance" / "testbed" / "golden-0825"
SERVER_DIR = GOLDEN_DIR / "server"
PLANTED = "zz_planted_failing_test.py"


def _scratch_server_copy(tmp: pathlib.Path) -> pathlib.Path:
    """A scratch copy of the server directory: every entry symlinked (the synced
    .venv included, so no second `uv sync`), plus one planted failing test."""
    scratch = tmp / "server"
    scratch.mkdir()
    for entry in list(SERVER_DIR.iterdir()) + [SERVER_DIR / ".venv", SERVER_DIR / ".python-version"]:
        if entry.exists() and not (scratch / entry.name).exists():
            (scratch / entry.name).symlink_to(entry)
    (scratch / PLANTED).write_text(
        "def test_planted_failure():\n    assert False, 'planted by --selftest'\n"
    )
    return scratch


def selftest() -> int:
    with tempfile.TemporaryDirectory(prefix="ucp_golden_unit_gate_selftest_") as tmp:
        tmp = pathlib.Path(tmp)
        scratch = _scratch_server_copy(tmp)
        # case A: a planted failing test in the scratch copy -> the gate is red.
        rc_planted = run_gate(server_dir=scratch, tests=[PLANTED, "defects_test.py"], quiet=True)
        # case B: no `uv` anywhere on PATH -> honest skip, rc 2.
        empty_path = tmp / "no-uv-on-path"
        empty_path.mkdir()
        rc_no_uv = run_gate(server_dir=SERVER_DIR, tests=["defects_test.py"], quiet=True,
                            path=str(empty_path))
    ok = rc_planted == 1 and rc_no_uv == 2
    print(f"selftest: planted failing test -> rc {rc_planted} (want 1); "
          f"uv absent -> rc {rc_no_uv} (want 2) · {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="golden-0825-unit gate (D3-29).")
    ap.add_argument("--selftest", action="store_true",
                    help="kill-test the gate: planted failing test -> red; uv absent -> rc 2")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return run_gate(server_dir=SERVER_DIR, tests=["../smoke", "."])


if __name__ == "__main__":
    sys.exit(main())
