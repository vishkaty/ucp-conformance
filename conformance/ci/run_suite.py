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
SPECLINT = ROOT / "conformance" / "speclint"
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant"
CONTROLLED_PORT = 8184
CONTROLLED = f"http://localhost:{CONTROLLED_PORT}"
CONTROLLED_0123_PORT = 8185
CONTROLLED_0123 = f"http://localhost:{CONTROLLED_0123_PORT}"
CONTROLLED_0111_PORT = 8193          # 8186-8188 sig gate, 8189 static web, 8190/8191 webhook harness
CONTROLLED_0111 = f"http://localhost:{CONTROLLED_0111_PORT}"
PROXY_PORT = 8183
PROXY = f"http://localhost:{PROXY_PORT}"

def _py(path, *args):
    return [sys.executable, str(path), *args]

def gates(server):
    # (name, argv, needs: None|"golden"|"controlled", skip_exit_codes)
    return [
        ("register",    _py(SELF / "verify_register.py"),                       None, ()),
        ("register-complete", _py(SELF / "verify_register_completeness.py"),     None, ()),
        ("citations",   _py(SELF / "verify_citations.py"),                      None, ()),
        ("coverage-lock", _py(ROOT / "conformance" / "coverage" / "verify_coverage_lock.py"), None, ()),
        ("review-signoff", _py(ROOT / "conformance" / "coverage" / "verify_review_signoffs.py"), None, ()),
        ("coverage",    _py(ROOT / "conformance" / "coverage" / "coverage_gate.py"), None, ()),
        ("verdict",     _py(SELF / "verdict_gate.py"),                          None, ()),
        ("schema",      _py(SELF / "schema_oracle.py"),                         None, (2,)),
        ("fixture",     _py(FIXTURE / "selfcheck.py"),                          None, (2,)),
        ("wrapper",     _py(SELF / "test_wrapper.py"),                          None, (2,)),
        ("schema-01-23", _py(CHK / "schema_check_01_23.py"),                    None, (2,)),
        ("schema-04-08", _py(CHK / "run_schema_04_08.py"),                      None, (2,)),
        ("suite-04-08", _py(CHK / "run_04_08.py"),                              None, (2,)),
        ("merchant",    _py(SELF / "validate_merchant_checks.py", "--server", server), "golden", ()),
        ("merchant-catalog", _py(SELF / "validate_merchant_checks.py",
                                 "--server", CONTROLLED, "--golden", "controlled"), "controlled", ()),
        ("merchant-ctrl-01-23", _py(SELF / "validate_merchant_checks.py",
                                    "--server", CONTROLLED_0123, "--golden", "controlled"),
         "controlled-01-23", ()),
        ("merchant-ctrl-01-11", _py(SELF / "validate_merchant_checks.py",
                                    "--server", CONTROLLED_0111, "--golden", "controlled"),
         "controlled-01-11", ()),
        ("schema-01-11-01-23", _py(CHK / "schema_check_01_11_01_23.py"),        None, (2,)),
        # the CLOSED testable tier can never silently reopen (wave-2 milestone)
        ("require-testable-04-08",
         _py(ROOT / "conformance" / "coverage" / "matrix.py",
             "--require", "testable", "--version", "2026-04-08"),               None, ()),
        ("tls-check",   _py(SELF / "validate_tls_check.py"),                 "controlled", (2,)),
        ("sig-check",   _py(SELF / "validate_sig_check.py"),                    None, (2,)),
        ("oauth-check", _py(SELF / "validate_oauth_checks.py"),                  None, (2,)),
        ("order-auth-check", _py(SELF / "validate_order_auth_check.py"),         None, (2,)),
        ("checkout-scope-check", _py(SELF / "validate_checkout_scope_check.py"), None, (2,)),
        ("disc014-check", _py(SELF / "validate_disc014_check.py"),               None, (2,)),
        ("fillme-guard", _py(SELF / "validate_fillme_guard.py"),                 None, (2,)),
        ("speclint",    _py(SPECLINT / "validate_speclint.py"),                   None, ()),
        ("ap2-crypto",  _py(SELF / "validate_ap2_crypto.py"),                     None, ()),
        ("site-checkdocs", _py(ROOT / "conformance" / "ci" / "site_gates.py", "checkdocs"), None, ()),
        ("web-unit",    _py(ROOT / "conformance" / "ci" / "web_gates.py", "unit"),    None, (2,)),
        ("web-browser", _py(ROOT / "conformance" / "ci" / "web_gates.py", "browser"), "controlled", (2,)),
        # --- site-governance lane: the website held to the same red/green bar as the
        #     suite (TDD traceability, claims register, voice law, security, redirects,
        #     shared-design-system consistency, product-freshness). Runs on every
        #     public/** change; blocks a red push.
        ("site-tdd",       _py(ROOT / "conformance" / "ci" / "site_gates.py", "tdd"),       None, ()),
        ("site-claims",    _py(ROOT / "conformance" / "ci" / "site_gates.py", "claims"),    None, ()),
        ("site-voice",     _py(ROOT / "conformance" / "ci" / "site_gates.py", "voice"),     None, ()),
        ("site-security",  _py(ROOT / "conformance" / "ci" / "site_gates.py", "security"),  None, ()),
        ("site-redirects", _py(ROOT / "conformance" / "ci" / "site_gates.py", "redirects"), None, ()),
        ("site-consistency", _py(ROOT / "conformance" / "ci" / "site_gates.py", "consistency"), None, ()),
        ("site-freshness", _py(ROOT / "conformance" / "ci" / "site_gates.py", "freshness"), None, ()),
        ("suite-01-23", _py(CHK / "run_01_23.py", server),                      "golden",  ()),
        ("differential", _py(ROOT / "conformance" / "ci" / "differential.py", "--server", server,
                             "--config", str(ROOT / "conformance" / "ci" / "differential_flower.config.json")),
         "golden", (2,)),
        ("killrate",    _py(SELF / "mutation_killrate.py"),                     "proxy",   (2,)),
        # --- isolation safety net + agent-conformance lane (separate tree; can't move
        #     merchant numbers). merchant-stability fails if agent work drifts merchant output.
        ("merchant-stability", _py(ROOT / "conformance" / "ci" / "merchant_stability.py",
                                   "--server", CONTROLLED),                     "controlled", (2,)),
        ("shared-api",  _py(ROOT / "conformance" / "ci" / "shared_api_guard.py"), None, ()),
        ("crypto-interop", _py(ROOT / "conformance" / "ci" / "crypto_interop.py"), None, ()),
        ("agent-governance", _py(ROOT / "conformance" / "agent" / "agent_governance.py"), None, ()),
        ("agent-lane",  _py(ROOT / "conformance" / "agent" / "run_agent.py"),   None, ()),
        # the public interop demo (public/agent-demo.json) must stay real + in sync with the
        # harness: every case's catching check still kills its defect, no drift.
        ("agent-demo",  _py(ROOT / "conformance" / "agent" / "build_demo_data.py", "--check"), None, ()),
        # the pip package is two-sided: the bundled `--agent` lane must run + pass from the
        # bundle (proves sync_bundle shipped a working agent lane, deps + path-resolution intact).
        ("package-agent", _py(ROOT / "packaging" / "spck_conformance" / "cli.py", "--agent"), None, ()),
    ]

def server_up(server, timeout=3):
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/.well-known/ucp", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False

def _boot(argv, health_url, tries=40):
    """Spawn a background server and wait for it to answer; return the Popen or None."""
    if server_up(health_url):
        return None                                   # already up (external); leave it
    p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(tries):
        if server_up(health_url):
            return p
        time.sleep(0.25)
    return p            # return anyway; the gate will report it DOWN

def boot_controlled():
    """Start the dependency-free controlled merchant fixture (default 2026-04-08)."""
    return _boot([sys.executable, str(FIXTURE / "server.py"), "--port", str(CONTROLLED_PORT)],
                 CONTROLLED)

def boot_controlled_0123():
    """Start a second controlled fixture serving spec 2026-01-23 (the version-switched
    golden for pre-04-08 checks the Flower Shop can't exercise)."""
    return _boot([sys.executable, str(FIXTURE / "server.py"), "--port", str(CONTROLLED_0123_PORT),
                  "--spec-version", "2026-01-23"], CONTROLLED_0123)

def boot_controlled_0111():
    """Start a third controlled fixture serving spec 2026-01-11 (wave-2: the oldest
    envelope generation — array capabilities, discovery_profile def)."""
    return _boot([sys.executable, str(FIXTURE / "server.py"), "--port", str(CONTROLLED_0111_PORT),
                  "--spec-version", "2026-01-11"], CONTROLLED_0111)

def boot_tls_proxy():
    """Start the CHK-051 TLS harness (1.3-only golden :8443 + 1.2-accepting negative
    :8444) in front of the controlled fixture. No HTTP health URL (TLS listeners);
    the gate itself reports the harness down as a skip."""
    return subprocess.Popen([sys.executable, str(FIXTURE / "tls_proxy.py")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def boot_proxy(golden):
    """Start the mutation proxy (wraps the golden) that the kill-rate gate drives."""
    return _boot([sys.executable, str(SELF / "mutation_proxy.py"),
                  "--upstream", golden, "--port", str(PROXY_PORT)], PROXY)

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
    ctrl0123_proc = boot_controlled_0123()
    ctrl0111_proc = boot_controlled_0111()
    ctrl0123_up = server_up(CONTROLLED_0123)
    ctrl0111_up = server_up(CONTROLLED_0111)
    tls_proc = boot_tls_proxy() if ctrl_up else None
    if tls_proc: time.sleep(1.0)            # cert mint + listener bind
    proxy_proc = boot_proxy(args.server) if up else None   # kill-rate gate drives the proxy
    proxy_up = server_up(PROXY)
    print(f"golden server {args.server}: {'UP' if up else 'DOWN'}")
    print(f"controlled fixture {CONTROLLED}: {'UP' if ctrl_up else 'DOWN'}")
    print(f"controlled fixture (01-23) {CONTROLLED_0123}: {'UP' if ctrl0123_up else 'DOWN'}")
    print(f"controlled fixture (01-11) {CONTROLLED_0111}: {'UP' if ctrl0111_up else 'DOWN'}")
    print(f"mutation proxy {PROXY}: {'UP' if proxy_up else 'DOWN'}\n")
    avail = {"golden": up, "controlled": ctrl_up, "controlled-01-23": ctrl0123_up,
             "controlled-01-11": ctrl0111_up,
             "proxy": proxy_up and up}

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
        for proc in (ctrl_proc, ctrl0123_proc, ctrl0111_proc, tls_proc, proxy_proc):
            if proc is not None:
                proc.terminate()

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
