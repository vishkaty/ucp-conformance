#!/usr/bin/env python3
"""
validate_tls_check.py — the reference gate for CHK-051 (HTTPS + TLS 1.3 minimum).

Transport behavior can't be exercised by the response-mutation engine, so this gate
plays the kill-rate role with real listeners (conformance/fixtures/merchant/tls_proxy.py):

  https://127.0.0.1:8443  golden   (TLS 1.3 ONLY)      -> check must be CLEAN
  https://127.0.0.1:8444  negative (accepts TLS 1.2)   -> check must DEVIATE (the mutant)
  http://127.0.0.1:8184   plain    (dev fixture)       -> check must be INCONCLUSIVE
                                                          (not-tested; never a deviation)

A check that can't kill the 1.2-accepting mutant, or that false-flags a plain-HTTP
dev golden, fails this gate and cannot ship. Exit 0 sound / 1 broken / 2 harness down.
"""
import sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "checks"))
sys.path.insert(0, str(HERE))
from tls_check_01_11_01_23 import tls_probe, p_tls13_minimum, chk051_resp  # noqa: E402
from engine import Resp, CLEAN, DEVIATION                                  # noqa: E402
from verdict_gate import INCONCLUSIVE                                      # noqa: E402

class _Ctx:
    def __init__(self, url): self.shopping_endpoint = url; self.base = url

def grade(url):
    return p_tls13_minimum(chk051_resp(_Ctx(url)))

def main():
    golden = tls_probe("https://127.0.0.1:8443")
    if not golden.get("handshake_ok"):
        print("tls harness DOWN (no TLS 1.3 handshake on :8443) — cannot gate"); return 2

    rows = [
        ("golden :8443 (TLS 1.3 only)", grade("https://127.0.0.1:8443"), CLEAN),
        ("negative :8444 (accepts 1.2)", grade("https://127.0.0.1:8444"), DEVIATION),
        ("plain http :8184 (dev golden)", grade("http://127.0.0.1:8184"), INCONCLUSIVE),
    ]
    ok = True
    for name, got, want in rows:
        good = (got == want)
        ok &= good
        print(f"  {'✓' if good else '✗'} {name:34} -> {got} (want {want})")
    print("tls-check gate:", "PASS — CHK-051 is sound (clean on 1.3-only, kills the "
          "1.2-accepting mutant, honest on plain HTTP)" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
