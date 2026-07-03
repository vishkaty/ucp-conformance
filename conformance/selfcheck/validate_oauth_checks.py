#!/usr/bin/env python3
"""
validate_oauth_checks.py — the OAUTH-area kill-proof gate (validate_sig_check.py
pattern, generalized to the identity-linking checks).

The generic engine mutations (status:*/set:/hset:/corrupt-json) act on the CAPTURED
response, so they cannot prove the checks detect a merchant whose OAuth SERVER is
broken end-to-end — e.g. one that redeems codes without PKCE, serves gated
operations without identity, matches redirect_uri loosely, skips client
authentication, or omits the challenge's error param (statically un-injectable when
the realm must simultaneously stay correct). This gate boots the controlled fixture
with each OAUTH MUTANT flag and requires the targeted checks to DEVIATE there while
CLEAN-passing on the normal golden — for both the 2026-04-08 and 01-era modules.

Exit 0 = every behavior proven; 1 = a check cannot detect its mutant merchant;
2 = environment skip (fixture didn't boot).
"""
import subprocess, sys, time, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance" / "checks"))
sys.path.insert(0, str(ROOT / "conformance" / "selfcheck"))
FIXTURE = ROOT / "conformance" / "fixtures" / "merchant" / "server.py"
PORT = 9322          # one port, servers booted sequentially (agent-private range)

# mutant flag -> the checks that MUST deviate against it (everything else in the
# module may legitimately still pass — each flag breaks one behavior)
MUTANTS_04_08 = [
    ("--oauth-no-pkce", ["identity.token_pkce_enforced",
                         "identity.pkce_plain_rejected",
                         "identity.none_requires_pkce"]),
    ("--oauth-no-gate", ["identity.identity_required_challenge",
                         "identity.invalid_token_challenge",
                         "identity.insufficient_scope_challenge",
                         "identity.revocation_invalidates",
                         "identity.challenge_resource_metadata",
                         "identity.continue_url_not_prebaked"]),
    ("--oauth-lax-redirect", ["identity.redirect_uri_exact"]),
    ("--oauth-no-client-auth", ["identity.client_auth_enforced"]),
    ("--oauth-challenge-no-error", ["identity.invalid_token_challenge",
                                    "identity.insufficient_scope_challenge"]),
]
MUTANTS_01_23 = [
    ("--oauth-no-client-auth", ["identity01.token_client_auth"]),
]


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


def _grade(check_ids, module_attr):
    from merchant import MerchantCtx, discover
    from merchant_checks import run_merchant_checks
    from validate_merchant_checks import CONTROLLED_CONFIG
    import merchant_checks_04_08_oauth as m48
    import merchant_checks_01_11_01_23_oauth as m01
    checks = list(m48.CHECKS_04_08_OAUTH) + list(m01.CHECKS_01_11_01_23_OAUTH)
    base = f"http://localhost:{PORT}"
    profile, _ = discover(base)
    ctx = MerchantCtx(base, profile, CONTROLLED_CONFIG)
    picked = [c for c in checks if c.id in check_ids]
    _, detail = run_merchant_checks(ctx, checks=picked)
    return {chk.id: d["status"] for chk, d in detail}


def _run_phase(version_args, mutants, golden_ids):
    """One fixture mode: golden must be all clean; each mutant's targets deviate."""
    failures = []
    proc = _boot(version_args)
    if proc is None:
        return None
    try:
        got = _grade(golden_ids, None)
        for cid, st in got.items():
            print(f"  golden{' ' + ' '.join(version_args) if version_args else ''}: "
                  f"{cid:42} -> {st}")
            if st != "clean-pass":
                failures.append((cid, "golden", st))
    finally:
        proc.terminate(); proc.wait()
    for flag, targets in mutants:
        proc = _boot(version_args + [flag])
        if proc is None:
            return None
        try:
            got = _grade(targets, None)
            for cid in targets:
                st = got.get(cid)
                print(f"  mutant {flag}: {cid:42} -> {st} (want deviation)")
                if st != "deviation":
                    failures.append((cid, flag, st))
        finally:
            proc.terminate(); proc.wait()
    return failures


def main():
    golden_48 = [cid for _, ids in MUTANTS_04_08 for cid in ids]
    # de-dup, keep order
    golden_48 = list(dict.fromkeys(golden_48))
    f1 = _run_phase([], MUTANTS_04_08, golden_48)
    if f1 is None:
        print("oauth-check gate: fixture did not come up — skip")
        return 2
    f2 = _run_phase(["--spec-version", "2026-01-23"], MUTANTS_01_23,
                    ["identity01.token_client_auth"])
    if f2 is None:
        print("oauth-check gate: 01-23 fixture did not come up — skip")
        return 2
    failures = f1 + f2
    if failures:
        print("oauth-check gate: FAIL — checks that cannot detect their mutant "
              "merchant (or deviate on the golden):")
        for cid, where, st in failures:
            print(f"    {cid} @ {where}: {st}")
        return 1
    print("oauth-check gate: PASS — every OAUTH check clean-passes on the golden "
          "and detects its broken-merchant mutant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
