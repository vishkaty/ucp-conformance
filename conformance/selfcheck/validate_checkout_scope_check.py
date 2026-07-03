#!/usr/bin/env python3
"""
validate_checkout_scope_check.py — the IDL-013 kill-proof gate (validate_oauth_checks.py
pattern) for the 01-era checkout-capability-scope mode.

IDL-013@01-era: "a scope covering a capability must grant access to ALL operations
associated to the capability" (checkout_session: Get/Create/Update/Cancel/Complete).
Grading it needs a scope-GATED checkout lifecycle, which would break the DEFAULT
golden's unauthenticated checks — so it lives behind --require-checkout-scope. This
gate (01-23, the representative 01-era mode, matching validate_oauth_checks):

  * GOLDEN  = --require-checkout-scope : identity01.capability_scope_grants_ops must
    CLEAN-pass (one checkout_session token unlocks Create+Get+Cancel; an
    unauthenticated op is refused) AND be kill_safe.
  * MUTANT  = --require-checkout-scope --checkout-scope-partial : one operation now
    demands an extra per-operation scope the capability scope does NOT grant (the
    IDL-013 violation) — the SAME check must DEVIATE.

Exit 0 = proven; 1 = the check cannot detect the per-operation-scope mutant (or
deviates on the golden); 2 = environment skip.
"""
import subprocess, sys, time, pathlib, urllib.request, copy

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant" / "server.py"
PORT = 9399          # agent-private range; servers booted sequentially

CHECK_ID = "identity01.capability_scope_grants_ops"


def _config():
    from validate_merchant_checks import CONTROLLED_CONFIG
    cfg = copy.deepcopy(CONTROLLED_CONFIG)
    cfg["identity"]["checkout_scope_gated"] = True   # opt into IDL-013 grading
    return cfg


def _boot(args):
    proc = subprocess.Popen(
        [sys.executable, str(FIXTURE), "--port", str(PORT),
         "--spec-version", "2026-01-23"] + args,
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
    import merchant_checks_01_11_01_23_oauth as m01
    base = f"http://localhost:{PORT}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, _config())
    picked = [c for c in m01.CHECKS_01_11_01_23_OAUTH if c.id == CHECK_ID]
    _, detail = run_merchant_checks(ctx, checks=picked)
    return {chk.id: d for chk, d in detail}


def main():
    failures = []
    proc = _boot(["--require-checkout-scope"])
    if proc is None:
        print("checkout-scope gate: golden fixture did not come up — skip")
        return 2
    try:
        d = _grade().get(CHECK_ID, {})
        st, ks = d.get("status"), d.get("kill_safe")
        print(f"  golden --require-checkout-scope: {CHECK_ID} -> {st} (kill_safe={ks})")
        if st != "clean-pass":
            failures.append((CHECK_ID, "golden", st))
        if not ks:
            failures.append((CHECK_ID, "golden-killsafe", d.get("survivors")))
    finally:
        proc.terminate(); proc.wait()
    proc = _boot(["--require-checkout-scope", "--checkout-scope-partial"])
    if proc is None:
        print("checkout-scope gate: mutant fixture did not come up — skip")
        return 2
    try:
        st = _grade().get(CHECK_ID, {}).get("status")
        print(f"  mutant  --checkout-scope-partial: {CHECK_ID} -> {st} (want deviation)")
        if st != "deviation":
            failures.append((CHECK_ID, "partial-scope-mutant", st))
    finally:
        proc.terminate(); proc.wait()

    if failures:
        print("checkout-scope gate: FAIL —", failures)
        return 1
    print("checkout-scope gate: PASS — identity01.capability_scope_grants_ops "
          "clean-passes + is kill_safe on the checkout_session golden and DEVIATES "
          "on the per-operation-scope mutant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
