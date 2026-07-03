#!/usr/bin/env python3
"""
validate_sig_check.py — the SIG-002 kill-proof gate (adversarial-review F6).

The generic engine mutations (status:*/corrupt-json/empty) cannot exercise SIG-002's
actual requirement — that the merchant VERIFIES request signatures — because that
logic lives inside the check's probe (a tampered signature must be rejected with
code signature_invalid). This gate proves the check detects a NON-verifying
merchant, the way validate_tls_check proves CHK-051:

  1. controlled fixture, normal mode  -> signature.verifies_es256_requests CLEAN
  2. controlled fixture, --no-verify-signatures MUTANT -> the check DEVIATES

Exit 0 = both behaviors proven; 1 = the check is unsound; 2 = environment skip.
"""
import subprocess, sys, time, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant" / "server.py"
GOLDEN_PORT, MUTANT_PORT = 8186, 8187


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


def _grade(port):
    """Run the SIG-002 check against the fixture on `port`; return its status."""
    from merchant import MerchantCtx, discover
    from merchant_checks import run_merchant_checks
    from merchant_checks_04_08_signatures import CHECKS_04_08_SIGNATURES
    from validate_merchant_checks import CONTROLLED_CONFIG
    base = f"http://localhost:{port}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, CONTROLLED_CONFIG)
    chk = next(c for c in CHECKS_04_08_SIGNATURES
               if c.id == "signature.verifies_es256_requests")
    _, detail = run_merchant_checks(ctx, checks=[chk])
    return detail[0][1]["status"]


def main():
    procs = []
    try:
        procs.append(subprocess.Popen(
            [sys.executable, str(FIXTURE), "--port", str(GOLDEN_PORT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen(
            [sys.executable, str(FIXTURE), "--port", str(MUTANT_PORT),
             "--no-verify-signatures"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not (_up(GOLDEN_PORT) and _up(MUTANT_PORT)):
            print("sig-check gate: fixture(s) did not come up — skip")
            return 2
        clean = _grade(GOLDEN_PORT)
        mutant = _grade(MUTANT_PORT)
        ok = (clean == "clean-pass") and (mutant == "deviation")
        print(f"  golden (verifying) -> {clean} (want clean-pass)")
        print(f"  mutant (--no-verify-signatures) -> {mutant} (want deviation)")
        print("sig-check gate:", "PASS — SIG-002 detects a non-verifying merchant"
              if ok else "FAIL — the check cannot detect a non-verifying merchant")
        return 0 if ok else 1
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
