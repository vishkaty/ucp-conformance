#!/usr/bin/env python3
"""
validate_run_suite_only.py — `run_suite.py --only <gate>` must run exactly the gates it
names, boot only the servers those gates need, and refuse an unknown name loudly.

Why this exists (PLAN-v3 D1-22): every acceptance command in the task board is written
as `run_suite.py --only <gate>`. If `--only` silently ran the whole table, or ran
nothing, or booted every fixture regardless, an acceptance line would "pass" while
proving nothing about the gate it names. This selftest pins the contract in-process
(it imports run_suite's real gate table — no private copy of the names to drift):

  1. `--only verdict`            runs exactly ONE gate (the table has ~80) and reports
                                 "1 gate run"; no controlled fixture / proxy is booted.
  2. `--only nope`               exit 2, message "unknown gate: nope" (never a silent
                                 no-op, never a full run).
  3. `--only golden-check-08-25` the gate boots its own golden on :8198 and tears it
                                 down: a listener is observed DURING the run and none
                                 remains after (lsof). Skips honestly (like the gate) if
                                 the ucp-schema oracle is not built.
  4. select_gates() keeps table order and accepts comma lists.

    --selftest   exit 0 pass, 1 fail.
"""
import contextlib
import io
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / "conformance" / "ci"
sys.path.insert(0, str(CI))
import run_suite  # noqa: E402

FIXTURE_PORTS = (8183, 8184, 8185, 8193)   # proxy + the three controlled fixtures


def _listening(port):
    p = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                       capture_output=True, text=True)
    return bool(p.stdout.strip())


def _run(argv):
    """Run run_suite.main() in-process with argv, capturing stdout+stderr."""
    old_argv = sys.argv
    sys.argv = ["run_suite.py", *argv]
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = run_suite.main()
            except SystemExit as e:                      # argparse errors land here
                rc = e.code if isinstance(e.code, int) else 2
    finally:
        sys.argv = old_argv
    return rc, out.getvalue() + err.getvalue()


def _gate_lines(text):
    """Rows of the result table: '<name> <mark> <STATUS> ...' with a known mark."""
    names = {g[0] for g in run_suite.gates("http://localhost:1")}
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in names and parts[1] in ("✓", "✗", "·"):
            rows.append((parts[0], parts[2]))
    return rows


def selftest():
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    # 4. the selector itself (in-process, real table)
    table = run_suite.gates("http://localhost:1")
    try:
        sel = run_suite.select_gates(table, "coverage,verdict")
        check("select-keeps-table-order", [g[0] for g in sel] == ["coverage", "verdict"],
              f"got {[g[0] for g in sel]}")
    except AttributeError as e:
        check("select-keeps-table-order", False, f"{e}")
    try:
        run_suite.select_gates(table, "verdict,nope")
        check("select-unknown-raises", False, "no exception")
    except AttributeError as e:
        check("select-unknown-raises", False, f"{e}")
    except run_suite.UnknownGate as e:
        check("select-unknown-raises", "nope" in str(e), str(e))

    # 1. exactly one gate, no fixture boot
    pre = {p: _listening(p) for p in FIXTURE_PORTS}
    rc, text = _run(["--only", "verdict"])
    rows = _gate_lines(text)
    check("only-verdict-rc0", rc == 0, f"rc={rc}; tail: {text.strip().splitlines()[-1:] }")
    check("only-verdict-exactly-one-gate", [r[0] for r in rows] == ["verdict"],
          f"gates run: {[r[0] for r in rows]}")
    check("only-verdict-reports-1-gate-run", "1 gate run" in text, "no '1 gate run' line")
    check("only-verdict-pass-line", "✓ PASS verdict" in text, "no '✓ PASS verdict' line")
    for p, was in pre.items():
        if not was:
            check(f"only-verdict-no-boot-{p}", not _listening(p),
                  f"port {p} listening after --only verdict (fixture booted needlessly)")

    # 2. unknown gate → rc 2, named
    rc, text = _run(["--only", "nope"])
    check("only-unknown-rc2", rc == 2, f"rc={rc}")
    check("only-unknown-named", "unknown gate: nope" in text, text.strip()[-200:])
    check("only-unknown-runs-nothing", _gate_lines(text) == [], f"ran {_gate_lines(text)}")

    # 3. a self-booting gate boots :8198 during the run and leaves nothing behind
    if _listening(8198):
        check("golden-check-port-free-before", False, ":8198 already in use — cannot prove teardown")
    else:
        seen = {"up": False, "stop": False}

        def poll():
            while not seen["stop"]:
                if _listening(8198):
                    seen["up"] = True
                time.sleep(0.25)
        t = threading.Thread(target=poll, daemon=True); t.start()
        rc, text = _run(["--only", "golden-check-08-25"])
        seen["stop"] = True; t.join(timeout=2)
        rows = _gate_lines(text)
        check("only-golden-check-exactly-one-gate", [r[0] for r in rows] == ["golden-check-08-25"],
              f"gates run: {[r[0] for r in rows]}")
        status = rows[0][1] if rows else None
        if status == "SKIP":
            print("    · golden-check-08-25 skipped (oracle/vendor unavailable) — boot not provable here")
        else:
            check("only-golden-check-pass", rc == 0 and status == "PASS", f"rc={rc} status={status}")
            check("only-golden-check-booted-8198", seen["up"], "no listener observed on :8198 during the run")
        check("only-golden-check-teardown-8198", not _listening(8198), ":8198 still listening after the run")

    print(f"run-suite-only: {'PASS' if not fails else 'FAIL'}"
          + (f" ({len(fails)} failed: {', '.join(fails)})" if fails else ""))
    return 0 if not fails else 1


if __name__ == "__main__":
    if "--selftest" not in sys.argv:
        print("usage: validate_run_suite_only.py --selftest"); sys.exit(2)
    sys.exit(selftest())
