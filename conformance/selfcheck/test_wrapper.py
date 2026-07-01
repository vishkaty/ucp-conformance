#!/usr/bin/env python3
"""
test_wrapper.py — guard the profile `ucp`-wrapper handling.

The canonical /.well-known/ucp document wraps the business profile under a top-level
`ucp` key (`{"ucp": {version, services, capabilities, ...}, ...}`) — as served by real
production servers (Shopify) and shown in the spec (cart-rest.md). Some servers (the
Flower Shop) serve it flat. The runner must handle BOTH: MerchantCtx and profile_resp
unwrap the `ucp` key so every check sees the inner business profile.

This was a self-test blind spot — both goldens are unwrapped, so the (correct)
wrapper handling was never exercised by CI. This test closes that: it runs the
profile-facing checks against the SAME known-valid profile in both flat and wrapped
forms and requires identical clean-pass results.

Exit 0 = wrapper handled correctly; 1 = a discrepancy (regression); 2 = oracle absent
(the schema check is skipped, the rest still run).
"""
import sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "fixtures" / "merchant"))
import server                                    # noqa: E402
import merchant_checks as mc                       # noqa: E402
from merchant import MerchantCtx                    # noqa: E402
from engine import CLEAN                            # noqa: E402

PROFILE_CHECKS = ["discovery.version", "discovery.reverse_domain_names",
                  "discovery.rest_endpoint", "discovery.profile_schema"]

def _oracle_ok():
    try:
        from schema_oracle import BIN
        return BIN.exists()
    except Exception:
        return False

def main():
    inner = server.profile("http://localhost:8184")   # known schema-valid business profile
    forms = {"flat": inner, "wrapped": {"ucp": inner, "payment": {}}}
    oracle = _oracle_ok()

    ok = True
    for name, profile in forms.items():
        ctx = MerchantCtx("http://localhost:8184", profile, {})
        # ctx must resolve identically regardless of wrapping
        if ctx.version != inner["version"] or ctx.capabilities != set(inner["capabilities"]):
            print(f"  ✗ {name}: MerchantCtx did not unwrap (version={ctx.version})"); ok = False
        for cid in PROFILE_CHECKS:
            if cid == "discovery.profile_schema" and not oracle:
                continue
            chk = next(c for c in mc.CHECKS if c.id == cid)
            verdict = mc._pred(chk, chk.fetch_fn(ctx), ctx)
            mark = "✓" if verdict == CLEAN else "✗"
            if verdict != CLEAN:
                ok = False
            print(f"  {mark} {name:8} {cid:32} {verdict}")

    print("\nwrapper handling:", "PASS — flat and wrapped profiles behave identically" if ok
          else "FAIL — wrapper handling regressed")
    if not ok:
        return 1
    return 0 if oracle else 2

if __name__ == "__main__":
    sys.exit(main())
