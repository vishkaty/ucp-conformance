#!/usr/bin/env python3
"""
validate_tls_check.py — the reference gate for the TLS-layer checks: CHK-051
(HTTPS + TLS 1.3 minimum, 01-era), IDL-053 (HTTPS + TLS 1.2 minimum, 2026-04-08)
and DISC-001@04-08 (profile over HTTPS).

Transport behavior can't be exercised by the response-mutation engine, so this gate
plays the kill-rate role with real listeners (conformance/fixtures/merchant/tls_proxy.py):

  https://127.0.0.1:8443  golden   (TLS 1.3 ONLY)      -> CHK-051 CLEAN, IDL-053 CLEAN
  https://127.0.0.1:8444  negative (1.2 floor+ceiling) -> CHK-051 must DEVIATE (its
                          mutant); IDL-053 must stay CLEAN (a 1.2 floor SATISFIES the
                          04-08 minimum — this cross-check pins the two floors apart)
  https://127.0.0.1:8445  sub-1.2  (accepts TLS 1.1)   -> IDL-053 must DEVIATE (its
                          mutant). Served only where OpenSSL can re-enable TLS 1.1
                          (@SECLEVEL=0); where it can't, the IDL-053 check must be
                          INCONCLUSIVE everywhere (floor unprobeable — honest skip),
                          and this gate asserts THAT instead.
  http://127.0.0.1:8184   plain    (dev fixture)       -> all checks INCONCLUSIVE
                                                          (not-tested; never a deviation)

A check that can't kill its mutant, or that false-flags a plain-HTTP dev golden,
fails this gate and cannot ship. Exit 0 sound / 1 broken / 2 harness down.
DISC-001 ("Business profile MUST be served over HTTPS",
merchant_checks_04_08_discovery.py) gates on the same listeners: HTTPS service
itself is the MUST there (ANY TLS version), and the kill-mutant is an https
profile URL with NO TLS service behind it (the plain upstream port probed over
https).

Ports are parameterizable (defaults preserve the run_suite wiring) so parallel-area
work can gate against a private tls_proxy instance:
    validate_tls_check.py [--golden-port 8443] [--negative-port 8444]
                          [--sub12-port 8445] [--plain-port 8184]
"""
import sys, argparse, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))
from tls_check_01_11_01_23 import tls_probe, p_tls13_minimum, chk051_resp  # noqa: E402
from merchant_checks_04_08_tls import tls12_probe, p_tls12_minimum, idl053_resp, \
    _tls11_client_ctx                                                      # noqa: E402
from merchant_checks_04_08_discovery import f_profile_https, p_profile_https  # noqa: E402
from engine import Resp, CLEAN, DEVIATION                                  # noqa: E402
from verdict_gate import INCONCLUSIVE                                      # noqa: E402

class _Ctx:
    def __init__(self, url): self.shopping_endpoint = url; self.base = url

def grade(url):
    return p_tls13_minimum(chk051_resp(_Ctx(url)))

def grade_idl053(url):
    """IDL-053@04-08: HTTPS with a minimum of TLS 1.2 (merchant_checks_04_08_tls)."""
    return p_tls12_minimum(idl053_resp(_Ctx(url)))

def grade_disc001(url):
    """DISC-001@04-08 (discovery area): profile served over HTTPS — probes
    <url>/.well-known/ucp's TLS service, any TLS version satisfies the row."""
    return p_profile_https(f_profile_https(_Ctx(url)))

def main():
    ap = argparse.ArgumentParser(description="Reference gate for the TLS-layer checks.")
    ap.add_argument("--golden-port", type=int, default=8443)
    ap.add_argument("--negative-port", type=int, default=8444)
    ap.add_argument("--sub12-port", type=int, default=8445)
    ap.add_argument("--plain-port", type=int, default=8184)
    args = ap.parse_args()
    g, n, s, p = (f"https://127.0.0.1:{args.golden_port}",
                  f"https://127.0.0.1:{args.negative_port}",
                  f"https://127.0.0.1:{args.sub12_port}",
                  f"http://127.0.0.1:{args.plain_port}")
    golden = tls_probe(g)
    if not golden.get("handshake_ok"):
        print(f"tls harness DOWN (no TLS 1.3 handshake on :{args.golden_port}) — cannot gate")
        return 2

    rows = [
        # CHK-051 @ 2026-01-23/2026-01-11 (HTTPS + TLS 1.3 minimum)
        (f"CHK-051 golden :{args.golden_port} (TLS 1.3 only)", grade(g), CLEAN),
        (f"CHK-051 negative :{args.negative_port} (accepts 1.2)", grade(n), DEVIATION),
        (f"CHK-051 plain http :{args.plain_port} (dev golden)", grade(p), INCONCLUSIVE),
        # DISC-001 @ 2026-04-08 (profile over HTTPS; version-agnostic on purpose)
        (f"DISC-001 golden :{args.golden_port} (https profile)", grade_disc001(g), CLEAN),
        (f"DISC-001 :{args.negative_port} (1.2 is still https)", grade_disc001(n), CLEAN),
        (f"DISC-001 mutant https->plain :{args.plain_port}",
         grade_disc001(f"https://127.0.0.1:{args.plain_port}"), DEVIATION),
        (f"DISC-001 plain http :{args.plain_port} (dev golden)", grade_disc001(p),
         INCONCLUSIVE),
    ]
    # IDL-053 @ 2026-04-08 (HTTPS + TLS 1.2 minimum): the kill needs a sub-1.2
    # listener AND a probe that can speak TLS 1.1. Where either is unavailable the
    # check must degrade to INCONCLUSIVE everywhere — assert that instead.
    can_probe_11 = _tls11_client_ctx() is not None
    sub12_up = can_probe_11 and tls12_probe(s).get("handshake_ok")
    if can_probe_11 and not sub12_up:
        # this stack CAN speak TLS 1.1, so the same-stack tls_proxy must be serving
        # the sub-1.2 listener — its absence is a harness wiring failure, not an
        # environment limitation. Refuse to pass without the kill.
        print(f"  ✗ IDL-053 sub-1.2 listener :{args.sub12_port} is DOWN but this "
              f"OpenSSL can probe TLS 1.1 — start tls_proxy.py (it now serves the "
              f"sub-1.2 negative) before gating")
        return 1
    if can_probe_11 and sub12_up:
        rows += [
            (f"IDL-053 golden :{args.golden_port} (1.3-only >= 1.2)", grade_idl053(g), CLEAN),
            (f"IDL-053 :{args.negative_port} (1.2 floor conforms)", grade_idl053(n), CLEAN),
            (f"IDL-053 sub-1.2 :{args.sub12_port} (accepts 1.1)", grade_idl053(s), DEVIATION),
            (f"IDL-053 mutant https->plain :{args.plain_port}",
             grade_idl053(f"https://127.0.0.1:{args.plain_port}"), DEVIATION),
            (f"IDL-053 plain http :{args.plain_port} (dev golden)", grade_idl053(p),
             INCONCLUSIVE),
        ]
    else:
        print("  ! IDL-053 kill UNPROVABLE here (no TLS 1.1 client in this OpenSSL) "
              "— asserting the honest degrade instead")
        rows += [
            (f"IDL-053 golden :{args.golden_port} (must degrade honestly)",
             grade_idl053(g), INCONCLUSIVE),
            (f"IDL-053 plain http :{args.plain_port} (dev golden)", grade_idl053(p),
             INCONCLUSIVE),
        ]
    ok = True
    for name, got, want in rows:
        good = (got == want)
        ok &= good
        print(f"  {'✓' if good else '✗'} {name:48} -> {got} (want {want})")
    print("tls-check gate:", "PASS — CHK-051 + IDL-053 + DISC-001 are sound (clean on "
          "their goldens, kill their mutants, honest on plain HTTP)" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
