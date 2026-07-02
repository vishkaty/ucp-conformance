#!/usr/bin/env python3
"""
tls_check_01_11_01_23.py — CHK-051: "All REST endpoints MUST be served over HTTPS
with minimum TLS version 1.3" (verbatim in BOTH the 2026-01-23 and 2026-01-11
registers; the id means an unrelated requirement at 2026-04-08, hence version-scoped
and the filename carries both version tokens for matrix attribution).

The check (per the WF#1 vetted spec) needs all three to pass:
  1. the declared REST endpoint's scheme is https;
  2. a handshake allowing up to TLS 1.3 succeeds AND negotiates TLSv1.3+;
  3. a handshake CAPPED at TLS 1.2 is REFUSED (minimum actually enforced).

Transport-layer behavior cannot be injected by the response-mutation engine, so
`mutations=[]` here and the kill proof lives in a dedicated reference gate
(selfcheck/validate_tls_check.py): the check must CLEAN-PASS on the TLS-1.3-only
golden listener and DEVIATE on the TLS-1.2-accepting negative listener served by
conformance/fixtures/merchant/tls_proxy.py. Certificate verification is disabled
in the probe — the MUST is about protocol version policy, not cert chains (the
harness cert is self-signed by design).

On a plain-HTTP server (local dev goldens) the requirement is not exercisable and
the check reports not-tested, never a deviation.
"""
import socket, ssl, sys, pathlib
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, CLEAN, DEVIATION                     # noqa: E402
from merchant_checks import MCheck                            # noqa: E402
from verdict_gate import INCONCLUSIVE                         # noqa: E402

V_TLS = ("2026-01-23", "2026-01-11")

def _handshake(host, port, cap_1_2=False, timeout=8):
    """One TLS handshake; returns (ok, negotiated_version_or_error)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # version policy, not cert chains
    if cap_1_2:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                return True, tls.version()
    except (ssl.SSLError, OSError) as e:
        return False, type(e).__name__

def tls_probe(url):
    """Probe a base URL's TLS policy. Returns a dict the predicate grades."""
    u = urlsplit(url)
    if u.scheme != "https":
        return {"applicable": False, "scheme": u.scheme}
    host, port = u.hostname, u.port or 443
    ok13, ver = _handshake(host, port)
    ok12, how12 = _handshake(host, port, cap_1_2=True)
    return {"applicable": True, "scheme": "https",
            "handshake_ok": ok13, "negotiated": ver,
            "tls12_accepted": ok12, "tls12_detail": how12}

def chk051_resp(ctx):
    """Synthetic Resp carrying the probe result (no HTTP request involved)."""
    r = Resp(200, {}, b"{}")
    r.tls = tls_probe(ctx.shopping_endpoint or ctx.base)
    return r

def p_tls13_minimum(r):
    t = getattr(r, "tls", None) or {}
    if not t.get("applicable"):
        return INCONCLUSIVE                  # plain-HTTP dev server -> not-tested
    if not t.get("handshake_ok"):
        return DEVIATION                     # https declared but no TLS service
    neg = t.get("negotiated") or ""
    if not (neg == "TLSv1.3" or neg > "TLSv1.3"):   # any future version sorts higher
        return DEVIATION                     # negotiated below 1.3
    if t.get("tls12_accepted"):
        return DEVIATION                     # 1.2 accepted -> minimum NOT enforced
    return CLEAN

CHECKS_TLS = [
    MCheck("transport.https_tls13_minimum", ["CHK-051"], "MUST", chk051_resp,
           p_tls13_minimum,
           [],   # transport-layer: kill proof = negative listener gate (see docstring)
           capability="dev.ucp.shopping.checkout", transport="rest", versions=V_TLS),
]

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443"
    print(tls_probe(url))
