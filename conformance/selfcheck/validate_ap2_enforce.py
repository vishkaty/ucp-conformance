#!/usr/bin/env python3
"""
validate_ap2_enforce.py — the AP2 ENFORCE-side kill-proof gate (ORD-012 pattern).

ap2-mandates.md Business Verification: once AP2 is negotiated the business MUST NOT
complete a checkout without a valid ap2.checkout_mandate (PAY-035/044/045/047), MUST
return an error for an invalid one (PAY-038), and the mandate must wrap THIS
session's checkout with the business's own merchant_authorization inside (PAY-042,
scope L410-411). The mandates the gate presents are minted by OUR frozen-layer
primitives (testbed/mint.py — the platform role), whose wire the official reference
verifier fully accepts (e2e.our_mint_reference_interop).

  GOLDEN  = fixture --ap2 : advertises the capability; every checkout response
    carries a VERIFYING merchant_authorization; complete is accepted ONLY with a
    valid mandate; each violation is rejected with the SPEC'S error code.
  MUTANT  = fixture --ap2 --ap2-no-enforce (the merchant that emits but does not
    enforce): the same missing-mandate completion SUCCEEDS — the gate's detector
    must flag it (kill-proof: the enforcement check cannot false-pass).

Exit 0 = proven; 1 = an assertion failed; 2 = environment skip (fixture didn't boot).
"""
import json
import pathlib
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "common"))
sys.path.insert(0, str(ROOT / "conformance" / "testbed"))
import crypto  # noqa: E402
import mint  # noqa: E402

FIXTURE = ROOT / "conformance" / "fixtures" / "merchant" / "server.py"
PORT = 9399
PROFILE_PORT = 9402
BASE = f"http://localhost:{PORT}"
PROFILE_BASE = f"http://127.0.0.1:{PROFILE_PORT}"
# The gate plays the PLATFORM role: its UCP-Agent names the loopback platform
# profile that publishes the mint platform key (real PAY-037 resolution path).
HDRS = {"Content-Type": "application/json",
        "UCP-Agent": f'profile="http://127.0.0.1:9402/with-keys.json"',
        "Idempotency-Key": "ap2-gate"}


class _ProfileHandler(BaseHTTPRequestHandler):
    """Loopback PLATFORM profiles for the PAY-037 key-resolution cases:
    /with-keys.json (the mint platform key), /no-keys.json (agent_missing_key),
    /wrong-key.json (a resolvable key that did NOT sign the mandate)."""
    def do_GET(self):
        wrong_q = crypto.keypair(b"not-the-platform")[1]
        bodies = {
            "/with-keys.json": {"signing_keys": [mint.platform_public_jwk()]},
            "/no-keys.json": {"name": "keyless platform"},
            "/wrong-key.json": {"signing_keys": [crypto.jwk_from_pub("wrong", wrong_q)]},
        }
        body = bodies.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body or {}).encode())

    def log_message(self, *a):
        pass


def _start_profile_server():
    srv = ThreadingHTTPServer(("127.0.0.1", PROFILE_PORT), _ProfileHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _boot(extra):
    proc = subprocess.Popen(
        [sys.executable, str(FIXTURE), "--port", str(PORT),
         "--no-verify-signatures"] + extra,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{BASE}/.well-known/ucp", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    return None


def _req(path, body=None, method=None, key=None):
    hdrs = dict(HDRS)
    if key:
        hdrs["Idempotency-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs,
                                 method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    return bool(cond)


def _create(key):
    st, body = _req("/checkout-sessions",
                    {"line_items": [{"item": {"id": "tin_spice_anise"}, "quantity": 1}]},
                    key=key)
    return st, body


def golden(ok):
    print("GOLDEN (--ap2):")
    st, prof = _req("/.well-known/ucp")
    ok &= check("profile advertises dev.ucp.shopping.ap2_mandate",
                "dev.ucp.shopping.ap2_mandate" in (prof.get("capabilities") or {}))

    st, co = _create("g1")
    ok &= check(f"create -> 2xx with ap2.merchant_authorization (got {st})",
                st in (200, 201) and
                isinstance((co.get("ap2") or {}).get("merchant_authorization"), str))
    sid = co.get("id")

    # PAY-031/034 LIVE: the emitted signature verifies over JCS(body minus ap2).
    _, merchant_q = crypto.keypair(b"ap2-merchant-fixture")
    auth = (co.get("ap2") or {}).get("merchant_authorization") or ""
    body_wo = {k: v for k, v in co.items() if k != "ap2"}
    ok &= check("merchant_authorization VERIFIES over the live response (PAY-031/034)",
                crypto.jws_detached_verify(auth, body_wo, merchant_q))

    # PAY-035/045/047: completion without a mandate MUST NOT complete.
    st, err = _req(f"/checkout-sessions/{sid}/complete", {})
    ok &= check(f"complete w/o mandate -> rejected mandate_required (got {st} "
                f"{err.get('code')})", st == 401 and err.get("code") == "mandate_required")
    st, cur = _req(f"/checkout-sessions/{sid}")
    ok &= check("session did NOT complete (status unchanged)",
                cur.get("status") == "ready_for_complete")

    # PAY-038: an invalid mandate -> error.
    st, err = _req(f"/checkout-sessions/{sid}/complete",
                   {"ap2": {"checkout_mandate": "not.a.chain~"}})
    ok &= check(f"garbage mandate -> mandate_invalid_signature (got {err.get('code')})",
                st == 401 and err.get("code") == "mandate_invalid_signature")

    # PAY-042 live: embedded checkout lacking merchant_authorization.
    bad = mint.mint_chain(co, strip_embedded_mauth=True)
    st, err = _req(f"/checkout-sessions/{sid}/complete", {"ap2": {"checkout_mandate": bad}})
    ok &= check(f"stripped-mAuth mandate -> merchant_authorization_invalid "
                f"(got {err.get('code')})",
                st == 401 and err.get("code") == "merchant_authorization_invalid")

    # scope (L410-411): a FULLY VALID mandate — but bound to a different, real
    # checkout (a second session's response, genuine mAuth and all). Presenting it
    # against THIS session must fail the terms-match, not the signature checks.
    _, other = _create("g2")
    st, err = _req(f"/checkout-sessions/{sid}/complete",
                   {"ap2": {"checkout_mandate": mint.mint_chain(other)}})
    ok &= check(f"other-checkout's valid mandate -> mandate_scope_mismatch "
                f"(got {err.get('code')})",
                st == 409 and err.get("code") == "mandate_scope_mismatch")

    # freshness: an expired mandate.
    st, err = _req(f"/checkout-sessions/{sid}/complete",
                   {"ap2": {"checkout_mandate": mint.mint_chain(co, exp=int(time.time()) - 3600)}})
    ok &= check(f"expired mandate -> mandate_expired (got {err.get('code')})",
                st == 401 and err.get("code") == "mandate_expired")

    # PAY-037 — key resolution from the PLATFORM PROFILE (loopback = real fetch):
    # a key-less profile MUST be agent_missing_key; a resolvable-but-wrong key MUST
    # fail signature verification; the published platform key MUST verify + complete.
    def _complete_with_profile(sid_, mandate, profile_path):
        hdrs = dict(HDRS)
        hdrs["UCP-Agent"] = f'profile="{PROFILE_BASE}{profile_path}"'
        data = json.dumps({"ap2": {"checkout_mandate": mandate}}).encode()
        req = urllib.request.Request(f"{BASE}/checkout-sessions/{sid_}/complete",
                                     data=data, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    _, co2 = _create("g3")
    sid2 = co2.get("id")
    m2 = mint.mint_chain(co2)
    st, err = _complete_with_profile(sid2, m2, "/no-keys.json")
    ok &= check(f"key-less platform profile -> agent_missing_key (got {err.get('code')})",
                st == 401 and err.get("code") == "agent_missing_key")
    st, err = _complete_with_profile(sid2, m2, "/wrong-key.json")
    ok &= check(f"wrong published key -> mandate_invalid_signature (got {err.get('code')})",
                st == 401 and err.get("code") == "mandate_invalid_signature")
    st, done = _complete_with_profile(sid2, m2, "/with-keys.json")
    ok &= check(f"profile-RESOLVED key verifies -> completed (PAY-037 positive, got {st})",
                st == 200 and done.get("status") == "completed")

    # PAY-044 happy path: a valid mandate over THIS response completes the session.
    st, done = _req(f"/checkout-sessions/{sid}/complete",
                    {"ap2": {"checkout_mandate": mint.mint_chain(co)}})
    ok &= check(f"valid mandate -> completed (got {st} {done.get('status')})",
                st == 200 and done.get("status") == "completed")
    return ok


def detector_rejects_missing_mandate(sid_status, err_code):
    """The enforcement check the register row maps to: a completion WITHOUT a
    mandate must be rejected AND the session must not complete."""
    return err_code == "mandate_required" and sid_status != "completed"


def mutant(ok):
    print("MUTANT (--ap2 --ap2-no-enforce):")
    st, co = _create("m1")
    sid = co.get("id")
    st, res = _req(f"/checkout-sessions/{sid}/complete", {})
    completed = st == 200 and res.get("status") == "completed"
    ok &= check("non-enforcing merchant completes WITHOUT a mandate (the violation)",
                completed)
    caught = not detector_rejects_missing_mandate(res.get("status"), res.get("code"))
    ok &= check("the enforcement detector FLAGS the mutant (kill-proof)", caught)
    return ok


def _grade_mchecks():
    """Run the register-mapped MChecks (merchant_checks_04_08_ap2) against the
    booted fixture through the real merchant-checks runner (ORD-012 pattern)."""
    sys.path.insert(0, str(ROOT / "conformance" / "checks"))
    import copy
    from merchant import MerchantCtx, discover
    from merchant_checks import run_merchant_checks
    import merchant_checks_04_08_ap2 as ma
    from validate_merchant_checks import CONTROLLED_CONFIG
    profile, _ = discover(BASE)
    cfg = copy.deepcopy(CONTROLLED_CONFIG)
    cfg["ap2_mandates"] = {
        "platform_profile_no_keys_url": f"{PROFILE_BASE}/no-keys.json"}
    ctx = MerchantCtx(BASE, profile, cfg)
    _, detail = run_merchant_checks(ctx, checks=list(ma.CHECKS_04_08_AP2))
    return {chk.id: d for chk, d in detail}


def mchecks_on_golden(ok):
    print("register MChecks vs GOLDEN (must clean-pass + kill_safe):")
    for cid, d in _grade_mchecks().items():
        ok &= check(f"  {cid}: {d.get('status')} (kill_safe={d.get('kill_safe')})",
                    d.get("status") == "clean-pass" and d.get("kill_safe") is True)
    return ok


def mchecks_on_mutant(ok):
    print("register MChecks vs MUTANT (must DEVIATE):")
    for cid, d in _grade_mchecks().items():
        ok &= check(f"  {cid}: {d.get('status')}", d.get("status") == "deviation")
    return ok


def main():
    profile_srv = _start_profile_server()
    _ = profile_srv  # daemon thread; dies with the gate
    proc = _boot(["--ap2"])
    if proc is None:
        print("ap2-enforce: SKIP (fixture did not boot)")
        return 2
    try:
        ok = golden(True)
        ok = mchecks_on_golden(ok)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    proc = _boot(["--ap2", "--ap2-no-enforce"])
    if proc is None:
        print("ap2-enforce: SKIP (mutant fixture did not boot)")
        return 2
    try:
        ok = mutant(ok)
        ok = mchecks_on_mutant(ok)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    print("\nap2-enforce: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
