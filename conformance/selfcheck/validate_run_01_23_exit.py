#!/usr/bin/env python3
"""
validate_run_01_23_exit.py — the exit-semantics guard for the suite-01-23 gate.

P0-2 integrity hole: conformance/checks/run_01_23.py used to compute deviations and
UNSAFE (non-kill-safe) checks, print them, then UNCONDITIONALLY `return 0`. So
`suite-01-23` in run_suite.py — the gate that maps run_01_23's exit code to PASS/FAIL —
could NEVER go red. Every one of the 88 engine checks it aggregates was enforcement-free:
`python3 run_01_23.py http://localhost:1` printed "aggregate: FAIL … UNSAFE" and exited 0.

This gate proves the hole is closed and cannot silently reopen, the golden_boot_guards
way: every case carries its own mutant so the guard cannot pass by being unable to fail.

  1. verdict_exit (the pure decision) is RED (rc 1) on a MUST deviation and on an
     unsound (surviving-mutant) check, GREEN (rc 0) only when every executed check is a
     kill-safe clean-pass, and honest-SKIP (rc 2) when the target was unreachable so
     every executed check is inconclusive. Version-skipped checks (kill_safe=None) are
     out-of-scope, never counted. Each assertion is paired with the inverse so a
     `return 0`-always regression reds this gate.
  2. End-to-end: run_01_23.main() against a NON-conformant loopback stub returns
     non-zero (the exact scenario the audit exited 0 on).

Hermetic: a loopback stub, no golden, no network. --selftest: exit 0 pass, 1 fail.
"""
import contextlib
import http.server
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))

import run_01_23                                              # noqa: E402
from verdict_gate import (Report, CLEAN, DEVIATION,           # noqa: E402
                          INCONCLUSIVE, NOT_TESTED)


def _rep(aggregate):
    """A minimal Report carrying only the fields verdict_exit reads."""
    return Report(aggregate=aggregate, coverage=0.0, counts={}, blocking=[],
                  advisories=[], scope_stamp={})


def _det(clean, kill_safe, version_skip=False):
    """A run_report detail dict as the engine emits it."""
    d = {"clean": clean, "kills": "", "kill_safe": kill_safe}
    if version_skip:
        d["version_skip"] = True
    return d


class _Case:
    """A synthetic (aggregate, details) pair + the exit code it must produce."""
    def __init__(self, name, aggregate, details, expect, allowed=frozenset()):
        self.name, self.aggregate, self.details, self.expect = name, aggregate, details, expect
        self.allowed = allowed


# A stand-in "check" — verdict_exit only ever reads detail dicts, so an object with an
# .id is enough to mirror the (check, detail) pairs run_report returns.
class _Chk:
    def __init__(self, cid):
        self.id = cid


def _cases():
    return [
        # a real MUST deviation -> RED
        _Case("deviation", "fail",
              [(_Chk("a"), _det(DEVIATION, False)),
               (_Chk("b"), _det(CLEAN, True))], 1),
        # an unsound check (clean but a mutant survived: kill_safe False) -> RED
        _Case("unsound", "incomplete",
              [(_Chk("a"), _det(CLEAN, False)),
               (_Chk("b"), _det(CLEAN, True))], 1),
        # every executed check kill-safe clean-pass -> GREEN
        _Case("all-clean", "pass",
              [(_Chk("a"), _det(CLEAN, True)),
               (_Chk("b"), _det(CLEAN, True))], 0),
        # allowlisted version-skips (kill_safe=None) are out of scope, never red
        _Case("clean-plus-allowed-skip", "incomplete",
              [(_Chk("a"), _det(CLEAN, True)),
               (_Chk("s"), _det("version-scoped", None, version_skip=True))], 0,
              allowed={"s"}),
        # a version-skip OUTSIDE the allowlist -> RED (skip-inflation vacuity guard)
        _Case("rogue-skip", "incomplete",
              [(_Chk("a"), _det(CLEAN, True)),
               (_Chk("rogue"), _det("version-scoped", None, version_skip=True))], 1,
              allowed={"s"}),
        # EVERY check skipped -> nothing executed -> fail closed (vacuity)
        _Case("all-skipped-vacuous", "incomplete",
              [(_Chk("s1"), _det("version-scoped", None, version_skip=True)),
               (_Chk("s2"), _det("version-scoped", None, version_skip=True))], 1,
              allowed={"s1", "s2"}),
        # target unreachable: every executed check inconclusive -> honest SKIP (rc 2)
        _Case("all-inconclusive", "incomplete",
              [(_Chk("a"), _det(INCONCLUSIVE, False)),
               (_Chk("b"), _det(INCONCLUSIVE, False)),
               (_Chk("s"), _det("version-scoped", None, version_skip=True))], 2,
              allowed={"s"}),
    ]


@contextlib.contextmanager
def _nonconformant_stub():
    """A loopback server that 404s everything (no UCP profile). Every check that
    reaches it deviates — the audit's `http://localhost:1` scenario, but hermetic."""
    class H(http.server.BaseHTTPRequestHandler):
        def _reply(self):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail":"not found"}')
        do_GET = do_POST = do_PUT = lambda self: self._reply()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _selftest():
    ok = True

    # (1) pure decision — every branch, each with its inverse baked into the set
    for c in _cases():
        rc = run_01_23.verdict_exit(c.details, _rep(c.aggregate), c.allowed)
        good = rc == c.expect
        ok &= good
        print(f"  {'OK ' if good else 'XX '} verdict_exit/{c.name:18} -> rc={rc} "
              f"(expected {c.expect})")

    # (2) end-to-end — the exact hole: a non-conformant target must NOT exit 0
    with _nonconformant_stub() as base:
        import io
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf                      # run_01_23.main prints the full report
        try:
            rc = run_01_23.main(base)
        finally:
            sys.stdout = old
    good = rc != 0
    ok &= good
    print(f"  {'OK ' if good else 'XX '} main(non-conformant stub) -> rc={rc} "
          f"(must be non-zero; was 0 before the P0-2 fix)")

    print(f"\nrun_01_23 exit-semantics guard: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(_selftest())
