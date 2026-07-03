#!/usr/bin/env python3
"""
validate_disc014_check.py — the DISC-014 kill-proof gate (validate_oauth_checks.py
pattern), HERMETIC by construction.

DISC-014 (01-11 + 01-23): every spec/schema URL advertised in the discovery profile
MUST resolve (200 + parseable JSON/HTML). The real check fetches the external
authority-origin URLs, so it is opt-in (config.discovery.live_url_checks) and NEVER
enabled in CONTROLLED_CONFIG — no run_suite/selftest gate does a network fetch.

This gate proves the check logic without any network: the fixture is booted with
--local-spec-urls (every advertised URL repointed to a LOOPBACK path the fixture
serves 200 for) as the GOLDEN, and with --break-spec-url (one URL 404s) as the
MUTANT. Run per 01-era version the DISC-014 rows cover.

  * GOLDEN  --local-spec-urls           : discovery.spec_urls_resolvable clean-pass + kill_safe
  * MUTANT  --local-spec-urls --break-spec-url : the SAME check DEVIATES (a 404 URL)

Exit 0 = proven; 1 = the check cannot detect an unresolvable URL (or deviates on the
golden); 2 = environment skip.

Note: only LOOPBACK URLs are ever fetched here, so the gate is hermetic and fast.
"""
import subprocess, sys, time, pathlib, urllib.request, copy

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant" / "server.py"
PORT = 9398          # agent-private range; servers booted sequentially
CHECK_ID = "discovery.spec_urls_resolvable"
VERSIONS = ["2026-01-23", "2026-01-11"]


def _config():
    from validate_merchant_checks import CONTROLLED_CONFIG
    cfg = copy.deepcopy(CONTROLLED_CONFIG)
    # opt into the OPT-IN live-URL check — supplied ONLY by this hermetic gate, never
    # by CONTROLLED_CONFIG (so no other gate performs any fetch)
    cfg.setdefault("discovery", {})["live_url_checks"] = True
    return cfg


def _boot(version, extra):
    proc = subprocess.Popen(
        [sys.executable, str(FIXTURE), "--port", str(PORT),
         "--spec-version", version, "--local-spec-urls"] + extra,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            with urllib.request.urlopen(
                    f"http://localhost:{PORT}/.well-known/ucp", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    return None


def _grade():
    from merchant import MerchantCtx, discover
    from merchant_checks import run_merchant_checks
    import merchant_checks_01_11_01_23_disc as md
    base = f"http://localhost:{PORT}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, _config())
    picked = [c for c in md.CHECKS_01_11_01_23_DISC if c.id == CHECK_ID]
    _, detail = run_merchant_checks(ctx, checks=picked)
    return {chk.id: d for chk, d in detail}


def _phase(version):
    failures = []
    proc = _boot(version, [])
    if proc is None:
        return None
    try:
        d = _grade().get(CHECK_ID, {})
        st, ks = d.get("status"), d.get("kill_safe")
        print(f"  golden [{version}] --local-spec-urls: {CHECK_ID} -> {st} (kill_safe={ks})")
        if st != "clean-pass":
            failures.append((CHECK_ID, f"golden-{version}", st))
        if not ks:
            failures.append((CHECK_ID, f"golden-killsafe-{version}", d.get("survivors")))
    finally:
        proc.terminate(); proc.wait()
    proc = _boot(version, ["--break-spec-url"])
    if proc is None:
        return None
    try:
        st = _grade().get(CHECK_ID, {}).get("status")
        print(f"  mutant [{version}] --break-spec-url: {CHECK_ID} -> {st} (want deviation)")
        if st != "deviation":
            failures.append((CHECK_ID, f"break-mutant-{version}", st))
    finally:
        proc.terminate(); proc.wait()
    return failures


def main():
    all_failures = []
    for v in VERSIONS:
        f = _phase(v)
        if f is None:
            print(f"disc014 gate: fixture did not come up ({v}) — skip")
            return 2
        all_failures += f
    if all_failures:
        print("disc014 gate: FAIL —", all_failures)
        return 1
    print("disc014 gate: PASS — discovery.spec_urls_resolvable clean-passes + is "
          "kill_safe on the loopback golden and DEVIATES on a 404 URL, at every "
          "01-era version (hermetic; loopback only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
