#!/usr/bin/env python3
"""
validate_order_auth_check.py — the ORD-012 kill-proof gate (validate_oauth_checks.py
pattern) for the config-gated order-authentication mode.

ORD-012: "the business MUST authenticate requests to order data before returning a
response." The DEFAULT golden serves order reads UNauthenticated (so the existing
ORD-* checks stay sound), so this MUST is graded against a SEPARATE golden booted
with --require-order-auth. This gate:

  * GOLDEN  = fixture --require-order-auth : order.data_requires_auth must CLEAN-pass
    (an unauthenticated GET /orders/{id} is refused 401; a valid minted token unlocks
    it — the positive control) AND be kill_safe (its response mutations all caught).
  * MUTANT  = fixture WITHOUT the flag (the merchant that does NOT authenticate order
    data): the SAME check must DEVIATE (unauthenticated read returns 200).

It also proves the DEFAULT merchant-ctrl gates are unaffected: order.data_requires_auth
is cfg-gated on order.require_auth, which is NOT in CONTROLLED_CONFIG, so it skips
there — this gate supplies that key itself.

Exit 0 = proven; 1 = the check cannot detect its no-auth mutant (or deviates on the
golden); 2 = environment skip (fixture didn't boot).
"""
import subprocess, sys, time, pathlib, urllib.request, copy

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant" / "server.py"
PORT = 9398          # agent-private range; servers booted sequentially

CHECK_ID = "order.data_requires_auth"


def _config():
    from validate_merchant_checks import CONTROLLED_CONFIG
    cfg = copy.deepcopy(CONTROLLED_CONFIG)
    cfg.setdefault("order", {})["require_auth"] = True   # opt into ORD-012 grading
    return cfg


def _boot(args):
    proc = subprocess.Popen([sys.executable, str(FIXTURE), "--port", str(PORT)] + args,
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
    import merchant_checks_04_08_order as mo
    base = f"http://localhost:{PORT}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, _config())
    picked = [c for c in mo.CHECKS_04_08_ORDER if c.id == CHECK_ID]
    _, detail = run_merchant_checks(ctx, checks=picked)
    return {chk.id: d for chk, d in detail}


def main():
    failures = []
    # GOLDEN: --require-order-auth -> clean-pass + kill_safe
    proc = _boot(["--require-order-auth"])
    if proc is None:
        print("order-auth gate: golden fixture did not come up — skip")
        return 2
    try:
        d = _grade().get(CHECK_ID, {})
        st, ks = d.get("status"), d.get("kill_safe")
        print(f"  golden --require-order-auth: {CHECK_ID} -> {st} (kill_safe={ks})")
        if st != "clean-pass":
            failures.append((CHECK_ID, "golden", st))
        if not ks:
            failures.append((CHECK_ID, "golden-killsafe", d.get("survivors")))
    finally:
        proc.terminate(); proc.wait()
    # MUTANT: no flag -> the merchant does NOT authenticate order data -> deviation
    proc = _boot([])
    if proc is None:
        print("order-auth gate: mutant fixture did not come up — skip")
        return 2
    try:
        st = _grade().get(CHECK_ID, {}).get("status")
        print(f"  mutant  (no --require-order-auth): {CHECK_ID} -> {st} (want deviation)")
        if st != "deviation":
            failures.append((CHECK_ID, "no-auth-mutant", st))
    finally:
        proc.terminate(); proc.wait()

    if failures:
        print("order-auth gate: FAIL —", failures)
        return 1
    print("order-auth gate: PASS — order.data_requires_auth clean-passes + is "
          "kill_safe on the authenticated golden and DEVIATES on the no-auth mutant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
