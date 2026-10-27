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
    committed TEST public JWK (top-level ``signing_keys[]``, the 2026-04-08
    profile-schema shape this golden declares). SIG-002 must
    CLEAN-pass (tampered ES256 signature rejected 401 signature_invalid, valid
    signature accepted) AND be kill_safe.
  * PERMISSIVE: same boot WITHOUT --require_signatures (the upstream default, and
    exactly what a #122 revert looks like on the wire): the SAME check must NOT
    clean-pass — a merchant that accepts a tampered signature is caught. This leg
    is the gate's own kill-test: it proves the gate can detect the regression it
    exists to watch for, on every run.

The loopback profile requires --allow_insecure_profile_urls in BOTH legs so the
only variable between them is enforcement.

  * SIGNER-PROFILE FAULTS (samples#263 watch, Phase 5, 2026-10-27): in the SAME
    enforcing boot, the four signature.signer_profile_* checks (a malformed profile
    URL -> 400 invalid_profile_url; a non-object published key -> 422
    profile_malformed; an unhashable kid -> 401 key_not_found; a wrong-typed
    coordinate -> 401 signature_invalid) are graded with
    signature.resolves_signer_profile declared. At the samples pin that CARRIES the
    defect (DEFECT_PIN, the pin samples#263 was filed against) they must DEVIATE
    with an observed bare 500: that is the tripwire, and a clean-pass there means
    the pin moved under us or the defect changed shape, so the leg goes red rather
    than quietly passing. At ANY OTHER samples pin they must clean-pass AND be
    kill_safe, so once the fix is merged and re-pinned the deviation CLEARS by
    construction, and a later regression REAPPEARS as a red gate. The decision
    function is pure (evaluate_profile_faults) and kill-proved on every run before
    anything boots.

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

# samples#263 (OPEN at filing, 2026-10-27): the samples pin that carries the four 500s.
PROFILE_FAULT_CHECKS = (
    "signature.signer_profile_url_malformed_400",
    "signature.signer_profile_nonobject_key_422",
    "signature.signer_profile_unhashable_kid_401",
    "signature.signer_profile_bad_coordinate_401",
)
DEFECT_PIN = "e7c9c124864108cbb35e5102beeac9cbef80a154"
DEFECT_UPSTREAM = "https://github.com/Universal-Commerce-Protocol/samples/pull/263"
LOCK = ROOT / "conformance" / "SOURCES.lock.json"


def samples_pin():
    return json.loads(LOCK.read_text())["reference_sample_server"]["commit"]


def evaluate_profile_faults(pin, details):
    """Pure: failures for the signer-profile leg. `details` = {check id: runner detail}.
    At DEFECT_PIN every check must be a `deviation` whose observed status is 500 (the
    filed defect reproduces: tripwire holds). At any other pin every check must be
    `clean-pass` and kill_safe (the fix landed and stays landed). A check that did not
    run is a failure at either pin: a skip can never stand in for a verdict."""
    fails = []
    for cid in PROFILE_FAULT_CHECKS:
        d = details.get(cid) or {}
        st = d.get("status")
        obs = (d.get("observed") or {}).get("status")
        if pin == DEFECT_PIN:
            if st != "deviation" or obs != 500:
                fails.append(("tripwire-fired", cid, st, obs,
                              f"at the defect pin {DEFECT_PIN[:8]} this must still deviate "
                              f"with a bare 500 ({DEFECT_UPSTREAM}); it did not, so either the "
                              "defect no longer reproduces (re-pin past the merged fix: this leg "
                              "then requires clean-pass) or its shape changed (investigate)"))
        else:
            if st != "clean-pass":
                fails.append(("regression-or-unfixed", cid, st, obs,
                              f"samples pin {pin[:8]} is not the defect pin, so the spec code "
                              f"is required; a 500 here is the samples#263 class regressing"))
            elif not d.get("kill_safe"):
                fails.append(("not-kill-safe", cid, st, d.get("survivors"), ""))
    return fails


def _profile_faults_selftest():
    """Kill-proof of evaluate_profile_faults: each arm must be able to fail."""
    dev500 = {c: {"status": "deviation", "observed": {"status": 500}} for c in PROFILE_FAULT_CHECKS}
    clean = {c: {"status": "clean-pass", "kill_safe": True, "observed": {"status": 400}} for c in PROFILE_FAULT_CHECKS}
    other = "0123456789abcdef0123456789abcdef01234567"
    cases = [
        ("defect pin + 4x 500 deviations -> holds", not evaluate_profile_faults(DEFECT_PIN, dev500)),
        ("defect pin + a clean-pass -> tripwire fires",
         any(f[0] == "tripwire-fired" for f in evaluate_profile_faults(DEFECT_PIN, {**dev500,
             PROFILE_FAULT_CHECKS[0]: clean[PROFILE_FAULT_CHECKS[0]]}))),
        ("defect pin + a deviation that is NOT a 500 -> tripwire fires (shape changed)",
         any(f[0] == "tripwire-fired" for f in evaluate_profile_faults(DEFECT_PIN, {**dev500,
             PROFILE_FAULT_CHECKS[1]: {"status": "deviation", "observed": {"status": 422}}}))),
        ("other pin + 4x clean-pass kill_safe -> holds", not evaluate_profile_faults(other, clean)),
        ("other pin + a deviation -> regression",
         any(f[0] == "regression-or-unfixed" for f in evaluate_profile_faults(other, {**clean,
             PROFILE_FAULT_CHECKS[2]: dev500[PROFILE_FAULT_CHECKS[2]]}))),
        ("other pin + a clean-pass that is not kill_safe -> fails",
         any(f[0] == "not-kill-safe" for f in evaluate_profile_faults(other, {**clean,
             PROFILE_FAULT_CHECKS[3]: {"status": "clean-pass", "kill_safe": False, "survivors": ["status:500"]}}))),
        ("either pin + a check that did not run -> fails",
         bool(evaluate_profile_faults(DEFECT_PIN, {**dev500, PROFILE_FAULT_CHECKS[0]: {"status": "not-tested (needs config: x)"}}))
         and bool(evaluate_profile_faults(other, {**clean, PROFILE_FAULT_CHECKS[0]: {"status": "not-tested (needs config: x)"}}))),
    ]
    bad = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print(f"  {'ok  ' if ok else 'FAIL'} tripwire logic: {n}")
    return bad

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
    """Serve the platform profile document at every path.

    The golden declares 2026-04-08, and at that pin
    `source/discovery/profile_schema.json` `$defs/base` places `signing_keys`
    as a TOP-LEVEL SIBLING of `ucp` (L67-73); it declares no `keys` property
    anywhere. ucp#566 renames the field to a top-level `keys[]` only for
    2026-08-25 and later, so the 08-25 shape is wrong for this golden.
    """

    def do_GET(self):  # noqa: N802 (http.server API)
        body = json.dumps(
            {"ucp": {"version": "2026-04-08"},
             "signing_keys": [TEST_JWK_PUBLIC]}
        ).encode()
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


def _grade(ids=(CHECK_ID,), resolves_signer_profile=False):
    """{check id: runner detail} for `ids` against the booted reference."""
    from merchant import MerchantCtx, discover
    from merchant_checks import run_merchant_checks
    import merchant_checks_04_08_signatures as ms
    import merchant_checks_04_08_events as me
    from validate_merchant_checks import REF_CONFIG
    cfg = copy.deepcopy(REF_CONFIG)
    cfg["signature"] = {
        "request_private_jwk": TEST_JWK_PRIVATE,
        "platform_profile_url": f"http://localhost:{PROFILE_PORT}/agent-profile",
    }
    if resolves_signer_profile:
        cfg["signature"]["resolves_signer_profile"] = True
    base = f"http://localhost:{REF_PORT}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, cfg)
    picked = [c for c in ms.CHECKS_04_08_SIGNATURES + me.CHECKS_04_08_EVENTS if c.id in ids]
    _, detail = run_merchant_checks(ctx, checks=picked)
    return {chk.id: d for chk, d in detail}


def main():
    if not SERVER_DIR.is_dir() or not (DATA_DIR / "products.csv").is_file():
        print("sig002 gate: vendored reference not present — run fetch_sources.sh (skip)")
        return 2
    if shutil.which("uv") is None or shutil.which("lsof") is None:
        print("sig002 gate: uv/lsof not available (skip)")
        return 2

    bad = _profile_faults_selftest()
    if bad:
        print("sig002 gate: FAIL — the signer-profile tripwire logic cannot fail:", bad)
        return 1

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
                d = _grade().get(CHECK_ID, {})
                st, ks = d.get("status"), d.get("kill_safe")
                print(f"  enforcing (--require_signatures): {CHECK_ID} -> {st} "
                      f"(kill_safe={ks})")
                if st != "clean-pass":
                    failures.append(("enforcing", st))
                if not ks:
                    failures.append(("enforcing-killsafe", d.get("survivors")))
                # samples#263 watch: the four signer-profile faults, same enforcing boot.
                pin = samples_pin()
                faults = _grade(PROFILE_FAULT_CHECKS, resolves_signer_profile=True)
                for cid in PROFILE_FAULT_CHECKS:
                    fd = faults.get(cid, {})
                    print(f"  enforcing, signer-profile fault: {cid} -> {fd.get('status')} "
                          f"(observed {(fd.get('observed') or {}).get('status')}, "
                          f"kill_safe={fd.get('kill_safe')})")
                mode = ("TRIPWIRE (samples pin carries samples#263: must deviate with a 500)"
                        if pin == DEFECT_PIN else
                        f"WATCH (samples pin {pin[:8]} is past the defect pin: must clean-pass)")
                print(f"  samples#263 leg mode: {mode}")
                failures.extend(evaluate_profile_faults(pin, faults))
            finally:
                _teardown(proc)
        # PERMISSIVE: what a #122 revert looks like — the check must catch it.
        with tempfile.TemporaryDirectory() as db:
            proc = _boot_reference(db, enforce=False)
            if proc is None:
                print("sig002 gate: permissive reference did not come up — skip")
                return 2
            try:
                st = _grade().get(CHECK_ID, {}).get("status")
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
          "(a samples#122 regression cannot pass unnoticed); the samples#263 "
          "signer-profile leg holds in "
          + ("tripwire mode at the defect pin" if samples_pin() == DEFECT_PIN
             else "watch mode (clean-pass + kill_safe past the defect pin)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
