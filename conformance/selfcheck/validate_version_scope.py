#!/usr/bin/env python3
"""
validate_version_scope.py — the served-version gate + envelope-predicate soundness harness.

Two soundness defects share one root cause: the pinned reference golden now speaks
spec 2026-04-08 (SOURCES.lock re-pin 2026-08-03), while several engine checks encode
01-era contracts. Before this gate, run_01_23.py ran EVERY engine check against the
golden regardless of the version the server actually speaks, so 01-era-only checks
deviated on a KNOWN-GOOD server and were reported UNSAFE — noise that trains readers
to ignore the UNSAFE list, which is the exact opposite of what it is for.

The fix is two-sided, and each side can silently rot, so each side runs here twice —
once real, once with the guard/predicate excised (the golden_boot_guards.py pattern):

  1. SERVED-VERSION GATE (engine.run_report): a check declaring `versions` that
     exclude the version the target server speaks is reported
     "version-scoped (out of scope on this server)" — never run, never UNSAFE.
     Non-vacuity: with the gate excised, the same check RUNS and DEVIATES, proving
     both that the gate is load-bearing and that the underlying check still bites.
     Fail-closed: when the served version cannot be detected, version-scoped checks
     RUN (a broken detector surfaces as visible deviations, never as silent green).

  2. ENVELOPE-TOLERANT PREDICATES (disc.profile_200, disc.rest_endpoint,
     payment.handler_ids_advertised): UCP discovery profiles come in two shapes —
     the 04-08 reference nests everything under a top-level `ucp` member, 01-era
     servers serve it flat. The predicates read through both
     (doc.get("ucp", doc)) and their mutations use `ucp?.` optional segments so
     every kill lands on the field the predicate reads IN BOTH SHAPES
     (validate_mutation_paths.py pins the walker; this pins each check).
     Non-vacuity: a root-fixed-path mutant against the WRAPPED shape must SURVIVE —
     if it ever starts killing, the optional segment has stopped being necessary
     and the premise needs rechecking.

  3. negotiation.version_unsupported_error: the register never pins 400 — 01-23
     overview.md#L1157 requires "a version_unsupported error"; 2026-04-08
     overview.md#L699 maps it to HTTP 422 (AMB-001: spec authoritative over the
     official suite's 01-era 400). The predicate accepts any 4xx and, when a
     messages[] envelope is present, requires the version_unsupported code.

Hermetic: stub HTTP servers on loopback; no golden, no network beyond loopback.

  --selftest   run the cases. Exit 0 pass, 1 fail.
"""
import contextlib
import http.server
import json
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))

import engine                                                     # noqa: E402
from engine import Check, Resp, mutate, run_report                # noqa: E402
from verdict_gate import CLEAN, DEVIATION                         # noqa: E402


# ---- fixtures: the two legitimate profile shapes ---------------------------------
WRAPPED_PROFILE = {"ucp": {
    "version": "2026-04-08",
    "services": {"dev.ucp.shopping": [
        {"transport": "rest", "endpoint": "https://ok.example/api"}]},
    "capabilities": {"dev.ucp.shopping.checkout": [{"version": "2026-04-08"}]},
    "payment_handlers": {"dev.example.pay": [{"id": "pay_1", "name": "dev.example.pay"}]},
}}
FLAT_PROFILE = {
    "version": "2026-01-23",
    "services": {"dev.ucp.shopping": [
        {"transport": "rest", "endpoint": "https://ok.example/api"}]},
    "capabilities": {"dev.ucp.shopping.checkout": [{"version": "2026-01-23"}]},
    "payment_handlers": {"dev.example.pay": [{"id": "pay_1", "name": "dev.example.pay"}]},
}


def _resp(doc, status=200):
    return Resp(status, {"Content-Type": "application/json"}, json.dumps(doc).encode())


@contextlib.contextmanager
def _stub_server(profile_doc):
    """A loopback server answering /.well-known/ucp with `profile_doc` (any JSON)."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(profile_doc).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

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


STAMP = {"spec_version": "selftest", "spec_commit": "0000000", "tool": "selftest",
         "methodology": "selftest"}


def _mk_checks():
    """One 01-era-scoped check that DEVIATES if run (predicate: always deviation on a
    wrapped profile), one 04-08-scoped check that is sound (clean + killable). XSELF-000 is
    deliberately NOT a register id: these are harness fixtures, and a real id here
    would text-scan into the coverage matrix as spurious attribution."""
    def f(base):
        return engine.fetch(base, "/.well-known/ucp")

    def p_01era(r):
        # encodes the 01-era flat contract: top-level `version` — a wrapped 04-08
        # profile legitimately lacks it, so running this against a 04-08 server is
        # a false-deviation generator. That is what the gate must prevent.
        d = r.json if isinstance(r.json, dict) else {}
        return CLEAN if isinstance(d.get("version"), str) else DEVIATION

    def p_0408(r):
        if r.status != 200:
            return DEVIATION
        d = r.json.get("ucp") if isinstance(r.json, dict) else None
        return CLEAN if isinstance(d, dict) and isinstance(d.get("version"), str) \
            else DEVIATION

    old = Check("selftest.flat_version", ["XSELF-000"], "MUST", f, p_01era,
                ["status:500"], versions=("2026-01-11", "2026-01-23"))
    new = Check("selftest.wrapped_version", ["XSELF-000"], "MUST", f, p_0408,
                ["status:500", "drop:ucp?.version"], versions=("2026-04-08",))
    return old, new


def _selftest():
    fails = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append(f"{name}: {detail}")

    old, new = _mk_checks()

    # ---- Case A: the gate skips an out-of-version check (real) ----------------------
    with _stub_server(WRAPPED_PROFILE) as base:
        _, details = run_report([old, new], base, "2026-01-23", STAMP, "d")
        d_old = dict(details)[old]
        d_new = dict(details)[new]
        check("A-skip", d_old.get("version_skip") is True and d_old["kill_safe"] is None,
              f"01-era check must be version-skipped on a 04-08 server, got {d_old}")
        check("A-not-deviation", d_old.get("clean") != DEVIATION,
              "a version-skipped check must never surface as a deviation")
        # the gate must not over-skip: the in-version check runs and is sound
        check("A-runs", d_new.get("clean") == CLEAN and d_new.get("kill_safe") is True,
              f"in-version check must run clean + kill_safe, got {d_new}")

    # ---- Case B (mutant): gate excised -> the check RUNS and BITES -------------------
    real_gate = engine.version_applicable
    try:
        engine.version_applicable = lambda chk, served: True     # excise the guard
        with _stub_server(WRAPPED_PROFILE) as base:
            _, details = run_report([old], base, "2026-01-23", STAMP, "d")
            d_old = dict(details)[old]
            check("B-mutant", d_old.get("clean") == DEVIATION,
                  "with the gate excised the 01-era check must run and deviate "
                  f"(proves the gate is load-bearing AND the check bites), got {d_old}")
    finally:
        engine.version_applicable = real_gate

    # ---- Case C (fail-closed): undetectable served version -> nothing is skipped ----
    with _stub_server({"hello": "no version here"}) as base:
        check("C-detect", engine.served_version(base) is None,
              "a profile without a version must yield served_version None")
        _, details = run_report([old], base, "2026-01-23", STAMP, "d")
        d_old = dict(details)[old]
        check("C-fail-closed", d_old.get("version_skip") is None
              and d_old.get("clean") == DEVIATION,
              "unknown served version must RUN version-scoped checks (fail closed), "
              f"got {d_old}")

    # ---- Case D: served-version detection reads both profile shapes ------------------
    with _stub_server(WRAPPED_PROFILE) as base:
        check("D-wrapped", engine.served_version(base) == "2026-04-08",
              "served_version must read the nested ucp.version")
    with _stub_server(FLAT_PROFILE) as base:
        check("D-flat", engine.served_version(base) == "2026-01-23",
              "served_version must read the flat version")

    # ---- Case E: the envelope-tolerant predicates + their declared mutants -----------
    import v2026_01_23 as core
    import area_payment
    by_id = {c.id: c for c in core.CHECKS + area_payment.CHECKS}
    fixed = ["disc.profile_200", "disc.rest_endpoint", "payment.handler_ids_advertised"]
    for cid in fixed:
        chk = by_id.get(cid)
        if chk is None:
            check(f"E-{cid}-exists", False, "check disappeared from its module")
            continue
        for label, doc in (("wrapped", WRAPPED_PROFILE), ("flat", FLAT_PROFILE)):
            r = _resp(doc)
            check(f"E-{cid}-{label}-clean", chk.predicate(r) == CLEAN,
                  f"must clean-pass the {label} profile shape")
            for m in chk.mutations:
                got = chk.predicate(mutate(r, m))
                check(f"E-{cid}-{label}-kill[{m}]", got == DEVIATION,
                      f"declared mutant must die in the {label} shape, got {got}")
        # Non-vacuity (excised-guard direction): a root-fixed-path mutant against the
        # WRAPPED shape must NOT kill — the predicate reads through the envelope, so
        # only `ucp?.`-pathed mutants reach what it reads. If this starts failing,
        # the optional segment has stopped being load-bearing — recheck the premise.
        root_muts = {"disc.profile_200": "drop:version",
                     "disc.rest_endpoint": "drop:services",
                     "payment.handler_ids_advertised": "drop:payment_handlers"}
        r = _resp(WRAPPED_PROFILE)
        got = chk.predicate(mutate(r, root_muts[cid]))
        check(f"E-{cid}-root-path-survives", got == CLEAN,
              "a root-level mutant must MISS the wrapped field the predicate reads "
              f"(proves ucp?. is load-bearing), got {got}")

    # ---- Case F: negotiation.version_unsupported_error (register-faithful 4xx) ------
    import area_negotiation
    neg = {c.id: c for c in area_negotiation.CHECKS}.get(
        "negotiation.version_unsupported_error")
    if neg is None:
        check("F-exists", False,
              "negotiation.version_unsupported_error missing from area_negotiation")
    else:
        ENV_422 = _resp({"ucp": {"version": "2026-04-08", "status": "error"},
                         "messages": [{"type": "error", "code": "VERSION_UNSUPPORTED",
                                       "severity": "unrecoverable"}]}, status=422)
        BARE_400 = _resp({"detail": "Unsupported version"}, status=400)
        check("F-422-envelope", neg.predicate(ENV_422) == CLEAN,
              "04-08 shape: 422 + version_unsupported code must clean-pass (NEG-001)")
        check("F-400-bare", neg.predicate(BARE_400) == CLEAN,
              "01-era shape: bare 400 error must clean-pass (NEG-016 pins no status)")
        check("F-2xx-deviates", neg.predicate(_resp({}, status=200)) == DEVIATION,
              "a 2xx must deviate — the register requires an error")
        for m in neg.mutations:
            got = neg.predicate(mutate(ENV_422, m))
            check(f"F-kill[{m}]", got == DEVIATION,
                  f"declared mutant must die on the envelope shape, got {got}")
        wrong = _resp({"ucp": {"version": "2026-04-08", "status": "error"},
                       "messages": [{"type": "error", "code": "some_other_error"}]},
                      status=422)
        check("F-wrong-code", neg.predicate(wrong) == DEVIATION,
              "an envelope naming a different error code must deviate")

    # ---- Case G: the 01-era-only checks now DECLARE their scope ----------------------
    # (so the Case-A gate actually applies to them on a 04-08 golden)
    import area_validation
    import area_webhook
    scoped_ids = {
        "validation.error_detail_400": area_validation.CHECKS,
        "webhook.event_stream": area_webhook.CHECKS,
        "webhook.order_placed": area_webhook.CHECKS,
        "negotiation.reverse_domain_names": area_negotiation.CHECKS,
    }
    for cid, checks in scoped_ids.items():
        chk = {c.id: c for c in checks}.get(cid)
        if chk is None:
            check(f"G-{cid}-exists", False, "check disappeared from its module")
            continue
        check(f"G-{cid}-scoped", chk.versions == ("2026-01-11", "2026-01-23"),
              f"must declare versions=('2026-01-11','2026-01-23'), got {chk.versions}")

    if fails:
        print("version-scope: FAIL")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("version-scope: PASS — the served-version gate skips out-of-version checks "
          "(and its excised mutant proves both gate and checks bite), detection is "
          "fail-closed, and every envelope-tolerant predicate clean-passes + kills "
          "in BOTH profile shapes.")
    return 0


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
