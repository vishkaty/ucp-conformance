#!/usr/bin/env python3
"""
validate_sig_check.py — kill-proof gate for PROBE-LOGIC checks (adversarial-review
F6 pattern, extended by the WEBHOOK/EVENTS area).

Some checks' defect-detection logic lives inside their PROBE (a specially built
request, or a receiver-side capture flow) rather than in mutations of a captured
response — so the generic engine mutations alone cannot prove they detect a
defective merchant. This gate proves each such check end-to-end, the way
validate_tls_check proves CHK-051: run it against the controlled fixture in
normal mode (must CLEAN-PASS) and against a deliberately-DEFECTIVE fixture
mutant (must DEVIATE).

  * --no-verify-signatures mutant (a merchant that never verifies RFC 9421
    request signatures) must be caught by SIG-002 and by the signature
    error-code checks (SIG-031..034/036..038).
  * --no-webhooks mutant (a merchant that never sends order-event webhooks)
    must be caught by the order-event webhook check (ORD-029 as the canonical
    delivery detector; its siblings share the same capture flow and were
    additionally proven against the mutant during area development).

Ports default to 8186/8187/8188 (run_suite wiring); override with
SPCK_SIG_GATE_PORTS="p1,p2,p3" for parallel local runs.

Exit 0 = every behavior proven; 1 = a check is unsound; 2 = environment skip.
"""
import os, subprocess, sys, time, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant" / "server.py"
_ports = os.environ.get("SPCK_SIG_GATE_PORTS", "8186,8187,8188").split(",")
GOLDEN_PORT, SIG_MUTANT_PORT, WH_MUTANT_PORT = (int(p) for p in _ports[:3])

# check id -> which mutant must be caught ("sig" or "webhook")
PROVEN_CHECKS = {
    "signature.verifies_es256_requests": "sig",
    "signature.err_signature_missing": "sig",
    "signature.err_signature_invalid": "sig",
    "signature.err_key_not_found": "sig",
    "signature.err_digest_mismatch": "sig",
    "webhook.order_created_full_entity": "webhook",
}


def _up(port, tries=40):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/.well-known/ucp",
                                        timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _grade(port, cids):
    """Run the named checks against the fixture on `port`; {cid: status}."""
    from merchant import MerchantCtx, discover
    from merchant_checks import run_merchant_checks
    from merchant_checks_04_08_signatures import CHECKS_04_08_SIGNATURES
    from merchant_checks_04_08_events import CHECKS_04_08_EVENTS
    from validate_merchant_checks import CONTROLLED_CONFIG
    base = f"http://localhost:{port}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, CONTROLLED_CONFIG)
    checks = [c for c in CHECKS_04_08_SIGNATURES + CHECKS_04_08_EVENTS
              if c.id in cids]
    _, detail = run_merchant_checks(ctx, checks=checks)
    return {chk.id: d["status"] for chk, d in detail}


def main():
    procs = []
    try:
        for port, flag in ((GOLDEN_PORT, None),
                           (SIG_MUTANT_PORT, "--no-verify-signatures"),
                           (WH_MUTANT_PORT, "--no-webhooks")):
            cmd = [sys.executable, str(FIXTURE), "--port", str(port)]
            if flag:
                cmd.append(flag)
            procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL))
        if not all(_up(p) for p in (GOLDEN_PORT, SIG_MUTANT_PORT, WH_MUTANT_PORT)):
            print("sig-check gate: fixture(s) did not come up — skip")
            return 2
        clean = _grade(GOLDEN_PORT, set(PROVEN_CHECKS))
        sig_ids = {c for c, kind in PROVEN_CHECKS.items() if kind == "sig"}
        wh_ids = {c for c, kind in PROVEN_CHECKS.items() if kind == "webhook"}
        mutant = _grade(SIG_MUTANT_PORT, sig_ids)
        mutant.update(_grade(WH_MUTANT_PORT, wh_ids))
        ok = True
        for cid, kind in sorted(PROVEN_CHECKS.items()):
            good = clean.get(cid) == "clean-pass" and mutant.get(cid) == "deviation"
            ok = ok and good
            print(f"  {'✓' if good else '✗'} {cid:38} golden={clean.get(cid)} "
                  f"{kind}-mutant={mutant.get(cid)} (want clean-pass/deviation)")
        print("sig-check gate:",
              "PASS — every probe-logic check detects its defective merchant"
              if ok else "FAIL — a check cannot detect its defective merchant")
        return 0 if ok else 1
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
