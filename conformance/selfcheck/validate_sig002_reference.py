#!/usr/bin/env python3
"""
validate_sig002_reference.py — SIG-002 regression watch for samples#122 on the REAL
reference server (validate_order_auth_check.py pattern, but booting the vendored
Flower Shop golden rather than our controlled fixture).

samples#122 (merged 2026-08-03) taught the reference server to verify RFC 9421
request signatures, behind ``--require_signatures`` (default false: verify when
present, log-only on failure). Our SIG-002 check (signature.verifies_es256_requests)
is proven sound against the controlled fixture by validate_sig_check.py, but until
now it had never graded the reference itself — so a verification regression upstream
would go unnoticed: the check skips on the flower golden (REF_CONFIG carries no
``signature.request_private_jwk``), and the default golden boot does not enforce.

This gate closes that: it is the thing in our repo that goes red if samples#122
regresses upstream tomorrow.

  * ENFORCING : vendored reference booted with --require_signatures
    --allow_insecure_profile_urls, plus a loopback platform profile serving the
    committed TEST public JWK ({"keys": [...]} per ucp#566). SIG-002 must
    CLEAN-pass (tampered ES256 signature rejected 401 signature_invalid, valid
    signature accepted) AND be kill_safe.
  * PERMISSIVE: same boot WITHOUT --require_signatures (the upstream default, and
    exactly what a #122 revert looks like on the wire): the SAME check must NOT
    clean-pass — a merchant that accepts a tampered signature is caught. This leg
    is the gate's own kill-test: it proves the gate can detect the regression it
    exists to watch for, on every run.

The loopback profile requires --allow_insecure_profile_urls in BOTH legs so the
only variable between them is enforcement.

Exit 0 = proven; 1 = failed; 2 = environment skip (vendored server or uv absent).
"""
import copy
import http.server
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))

SERVER_DIR = ROOT / "conformance" / ".vendor" / "samples" / "rest" / "python" / "server"
DATA_DIR = ROOT / "conformance" / ".vendor" / "samples" / "rest" / "python" / \
    "test_data" / "flower_shop"
REF_PORT = 9412       # agent-private range; servers booted sequentially
PROFILE_PORT = 9413

CHECK_ID = "signature.verifies_es256_requests"

# The committed TEST platform key (validate_merchant_checks.CONTROLLED_CONFIG uses the
# same one; the public part below is what the loopback profile publishes).
TEST_JWK_PRIVATE = {
    "kid": "spck-platform-sig-2026", "kty": "EC", "crv": "P-256",
    "x": "fdOWNX6FUcEYKQntKv0Pb0wpcIEV6HrDZK4Ud9oF_rY",
    "y": "-Ie-pMb2OxUqg4GR_B6wObhra9-fRe5YWzWAAv7dNKk",
    "d": "EymkNYgazGbLoD16l-fw7K-C9WNJEIv4hn_RpRgW5xY",
}
TEST_JWK_PUBLIC = {k: v for k, v in TEST_JWK_PRIVATE.items() if k != "d"}


class _ProfileHandler(http.server.BaseHTTPRequestHandler):
    """Serve the platform profile document at every path (ucp#566 keys[] shape)."""

    def do_GET(self):  # noqa: N802 (http.server API)
        body = json.dumps({"keys": [TEST_JWK_PUBLIC]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve_profile():
    srv = http.server.HTTPServer(("localhost", PROFILE_PORT), _ProfileHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _listener_pids(port):
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout
        return [int(p) for p in out.split()]
    except Exception:
        return []


def _boot_reference(db_dir, enforce):
    """Seed a fresh DB and boot the vendored reference; return the wrapper Popen.

    ``uv run`` forks, so terminating the wrapper alone can leave the listener
    bound (the serve_golden.sh lesson) — teardown must kill _listener_pids too.
    """
    if _listener_pids(REF_PORT):
        print(f"sig002 gate: port {REF_PORT} already in use — refusing to boot")
        return None
    seed = subprocess.run(
        ["uv", "run", "import_csv.py", f"--data_dir={DATA_DIR}",
         f"--products_db_path={db_dir}/products.db",
         f"--transactions_db_path={db_dir}/transactions.db"],
        cwd=SERVER_DIR, capture_output=True, timeout=300)
    if seed.returncode != 0:
        print("sig002 gate: seeding failed —", seed.stderr.decode()[-300:])
        return None
    args = ["uv", "run", "server.py",
            f"--products_db_path={db_dir}/products.db",
            f"--transactions_db_path={db_dir}/transactions.db",
            f"--port={REF_PORT}", "--simulation_secret=sig002-secret",
            "--allow_insecure_profile_urls"]
    if enforce:
        args.append("--require_signatures")
    proc = subprocess.Popen(args, cwd=SERVER_DIR,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(
                    f"http://localhost:{REF_PORT}/.well-known/ucp", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.5)
    _teardown(proc)
    return None


def _teardown(proc):
    pids = _listener_pids(REF_PORT)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    for pid in pids:
        subprocess.run(["kill", str(pid)], capture_output=True)
    for _ in range(20):
        if not _listener_pids(REF_PORT):
            return
        time.sleep(0.25)


def _grade():
    from merchant import MerchantCtx, discover
    from merchant_checks import run_merchant_checks
    import merchant_checks_04_08_signatures as ms
    from validate_merchant_checks import REF_CONFIG
    cfg = copy.deepcopy(REF_CONFIG)
    cfg["signature"] = {
        "request_private_jwk": TEST_JWK_PRIVATE,
        "platform_profile_url": f"http://localhost:{PROFILE_PORT}/agent-profile",
    }
    base = f"http://localhost:{REF_PORT}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, cfg)
    picked = [c for c in ms.CHECKS_04_08_SIGNATURES if c.id == CHECK_ID]
    _, detail = run_merchant_checks(ctx, checks=picked)
    return {chk.id: d for chk, d in detail}.get(CHECK_ID, {})


def main():
    if not SERVER_DIR.is_dir() or not (DATA_DIR / "products.csv").is_file():
        print("sig002 gate: vendored reference not present — run fetch_sources.sh (skip)")
        return 2
    if shutil.which("uv") is None or shutil.which("lsof") is None:
        print("sig002 gate: uv/lsof not available (skip)")
        return 2

    profile_srv = _serve_profile()
    failures = []
    try:
        # ENFORCING: the reference verifies; SIG-002 must be sound on it.
        with tempfile.TemporaryDirectory() as db:
            proc = _boot_reference(db, enforce=True)
            if proc is None:
                print("sig002 gate: enforcing reference did not come up — skip")
                return 2
            try:
                d = _grade()
                st, ks = d.get("status"), d.get("kill_safe")
                print(f"  enforcing (--require_signatures): {CHECK_ID} -> {st} "
                      f"(kill_safe={ks})")
                if st != "clean-pass":
                    failures.append(("enforcing", st))
                if not ks:
                    failures.append(("enforcing-killsafe", d.get("survivors")))
            finally:
                _teardown(proc)
        # PERMISSIVE: what a #122 revert looks like — the check must catch it.
        with tempfile.TemporaryDirectory() as db:
            proc = _boot_reference(db, enforce=False)
            if proc is None:
                print("sig002 gate: permissive reference did not come up — skip")
                return 2
            try:
                st = _grade().get("status")
                print(f"  permissive (no enforcement):      {CHECK_ID} -> {st} "
                      f"(want deviation)")
                if st != "deviation":
                    failures.append(("permissive-mutant", st))
            finally:
                _teardown(proc)
    finally:
        profile_srv.shutdown()

    if failures:
        print("sig002 gate: FAIL —", failures)
        return 1
    print("sig002 gate: PASS — signature.verifies_es256_requests clean-passes + is "
          "kill_safe on the enforcing reference and DEVIATES on the permissive one "
          "(a samples#122 regression cannot pass unnoticed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
