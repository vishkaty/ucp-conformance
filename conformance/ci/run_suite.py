#!/usr/bin/env python3
"""
run_suite.py — the TDD / CI entrypoint: "the test suite for the test suite".

Runs every self-validation gate we have, in one shot, and returns a single red/green
verdict. This is what you run before every change (and what CI runs on every push):
if a change to a check, the register, or the engine breaks soundness, this goes red.

Gates (each anchored to something we did NOT write, to avoid circularity):
  register    verify_register.py     — every register row quotes the pinned spec verbatim
  verdict     verdict_gate.py        — the no-false-green gate's own unit tests
  schema      schema_oracle.py       — our schema checks match the official ucp-schema validator
  merchant    validate_merchant_checks.py — every merchant check is clean-pass + kill_safe on a golden
  suite-01-23 run_01_23.py           — the 2026-01-23 suite vs a live golden (no false green)
  suite-04-08 run_04_08.py           — the 2026-04-08 fixture checks (schema-oracle backed)
  killrate    mutation_killrate.py   — injected defects are caught (kill-rate)

Server-dependent gates are skipped (not failed) when no golden is reachable, unless
--require-server. The schema gate skips if the ucp-schema binary isn't built (exit 2).

Usage:
    python3 conformance/ci/run_suite.py [--server http://localhost:8182]
                                        [--require-server] [--skip schema,killrate]
Exit 0 = all run gates passed; 1 = a gate failed (or a required server was missing).
"""
import sys, subprocess, argparse, pathlib, urllib.request, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SELF = ROOT / "conformance" / "selfcheck"
CHK = ROOT / "conformance" / "checks"
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant"
CONTROLLED_PORT = 8184
CONTROLLED = f"http://localhost:{CONTROLLED_PORT}"

def _py(path, *args):
    return [sys.executable, str(path), *args]

def gates(server):
    # (name, argv, needs: None|"golden"|"controlled", skip_exit_codes)
    return [
        ("register",    _py(SELF / "verify_register.py"),                       None, ()),
        ("verdict",     _py(SELF / "verdict_gate.py"),                          None, ()),
        ("schema",      _py(SELF / "schema_oracle.py"),                         None, (2,)),
        ("fixture",     _py(FIXTURE / "selfcheck.py"),                          None, (2,)),
        ("suite-04-08", _py(CHK / "run_04_08.py"),                              None, ()),
        ("merchant",    _py(SELF / "validate_merchant_checks.py", "--server", server), "golden", ()),
        ("merchant-catalog", _py(SELF / "validate_merchant_checks.py",
                                 "--server", CONTROLLED, "--golden", "controlled"), "controlled", ()),
        ("suite-01-23", _py(CHK / "run_01_23.py", server),                      "golden",  ()),
        ("killrate",    _py(SELF / "mutation_killrate.py"),                     "golden",  (2,)),
    ]

def server_up(server, timeout=3):
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/.well-known/ucp", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False

def boot_controlled():
    """Start the dependency-free controlled merchant fixture; return the Popen or None."""
    if server_up(CONTROLLED):
        return None                                   # already up (external); leave it
    p = subprocess.Popen([sys.executable, str(FIXTURE / "server.py"),
                          "--port", str(CONTROLLED_PORT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if server_up(CONTROLLED):
            return p
        time.sleep(0.25)
    return p            # return anyway; the gate will report it DOWN

def run_gate(name, argv, timeout=180):
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"rc": 124, "dt": timeout, "tail": "TIMEOUT"}
    tail = (p.stdout + p.stderr).strip().splitlines()
    return {"rc": p.returncode, "dt": time.monotonic() - t0,
            "tail": tail[-1] if tail else "", "out": p.stdout + p.stderr}

def main():
    ap = argparse.ArgumentParser(description="TDD/CI gate runner for the UCP conformance suite.")
    ap.add_argument("--server", default="http://localhost:8182",
                    help="golden UCP server for behavioral gates")
    ap.add_argument("--require-server", action="store_true",
                    help="fail (not skip) server-dependent gates if the golden is down")
    ap.add_argument("--skip", default="", help="comma-separated gate names to skip")
    ap.add_argument("-v", "--verbose", action="store_true", help="print full gate output on failure")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    up = server_up(args.server)
    ctrl_proc = boot_controlled()
    ctrl_up = server_up(CONTROLLED)
    print(f"golden server {args.server}: {'UP' if up else 'DOWN'}")
    print(f"controlled fixture {CONTROLLED}: {'UP' if ctrl_up else 'DOWN'}\n")
    avail = {"golden": up, "controlled": ctrl_up}

    results = []
    try:
      for name, argv, needs, skip_codes in gates(args.server):
        if name in skip:
            results.append((name, "SKIP", "explicitly skipped")); continue
        if needs and not avail.get(needs):
            if args.require_server:
                results.append((name, "FAIL", f"{needs} server required but DOWN"))
            else:
                results.append((name, "SKIP", f"no {needs} server"))
            continue
        r = run_gate(name, argv)
        if r["rc"] == 0:
            status = "PASS"
        elif r["rc"] in skip_codes:
            status = "SKIP"
        else:
            status = "FAIL"
        results.append((name, status, f"{r['tail']}  [{r['dt']:.1f}s, rc={r['rc']}]"))
        if status == "FAIL" and args.verbose:
            print(f"----- {name} output -----\n{r.get('out','')}\n-------------------------")
    finally:
        if ctrl_proc is not None:
            ctrl_proc.terminate()

    print(f"{'gate':14} {'status':6} detail")
    print("-" * 72)
    for name, status, detail in results:
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "·"}[status]
        print(f"{name:14} {mark} {status:4} {detail}")

    failed = [n for n, s, _ in results if s == "FAIL"]
    passed = [n for n, s, _ in results if s == "PASS"]
    skipped = [n for n, s, _ in results if s == "SKIP"]
    print("-" * 72)
    print(f"{len(passed)} passed · {len(failed)} failed · {len(skipped)} skipped")
    if failed:
        print(f"\nRED — gates failed: {', '.join(failed)}")
        return 1
    print(f"\nGREEN — every run gate passed"
          + (f" ({len(skipped)} skipped: {', '.join(skipped)})" if skipped else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())
