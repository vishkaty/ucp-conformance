#!/usr/bin/env python3
"""
merchant_checks_04_08_tls.py — IDL-053 @2026-04-08: "All communication between
platform and business MUST use HTTPS with a minimum of TLS 1.2"
(identity-linking.md Security Considerations, L610-612, citing RFC 6749 §1.6).

ID-DRIFT: IDL-053 is not a register row at 2026-01-11/2026-01-23 at all, so the
check is version-locked (versions=V0408) and the filename carries 04_08 for
matrix.py attribution. The 01-era sibling requirement is CHK-051 (TLS 1.3 minimum,
tls_check_01_11_01_23.py) — a STRICTER floor under a different id; the two checks
never share citations.

The check needs all three to pass:
  1. the declared REST endpoint's scheme is https;
  2. a normal handshake succeeds AND negotiates TLS 1.2 or higher;
  3. a handshake CAPPED at TLS 1.1 is REFUSED (the 1.2 minimum actually enforced).

Probing (3) requires a client that can SPEAK TLS 1.1 — modern OpenSSL only offers
sub-1.2 protocols at @SECLEVEL=0. Where the local stack cannot build that client
(LibreSSL/FIPS builds), the floor clause is unprobeable and the check reports
INCONCLUSIVE (not-tested) — never a weakened CLEAN, never a false DEVIATION.

Transport-layer behavior cannot be injected by the response-mutation engine, so
`mutations=[]` and the kill proof lives in the reference gate
(selfcheck/validate_tls_check.py): CLEAN on the TLS-1.3-only golden AND on the
1.2-floor listener, DEVIATION on the sub-1.2-accepting listener (tls_proxy.py
:8445) and on an https URL with no TLS service, INCONCLUSIVE on plain HTTP
(dev goldens — same convention as CHK-051).
"""
import socket, ssl, sys, pathlib, warnings
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, CLEAN, DEVIATION                     # noqa: E402
from merchant_checks import MCheck                            # noqa: E402
from verdict_gate import INCONCLUSIVE                         # noqa: E402

V0408 = ("2026-04-08",)


def _client_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # version policy, not cert chains
    return ctx


def _tls11_client_ctx():
    """A client capped at TLS 1.1, or None if this stack cannot speak sub-1.2."""
    try:
        ctx = _client_ctx()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_1   # default floor may be 1.2
            ctx.maximum_version = ssl.TLSVersion.TLSv1_1
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        return ctx
    except (ssl.SSLError, ValueError, OSError):
        return None


def _shake(ctx, host, port, timeout=8):
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                return True, tls.version()
    except ssl.SSLError as e:
        return False, getattr(e, "reason", None) or type(e).__name__
    except OSError as e:
        return False, type(e).__name__


# OpenSSL raises these LOCALLY when the client's own version/cipher config yields
# nothing to offer — i.e. the CLIENT cannot speak TLS 1.1 here (nothing was sent to
# the server), as opposed to a server refusing the offer. Must not read as CLEAN.
_CLIENT_SIDE_UNAVAILABLE = {"NO_PROTOCOLS_AVAILABLE", "NO_CIPHERS_AVAILABLE"}


def tls12_probe(url):
    """Probe a base URL's TLS-1.2-minimum policy. Returns a dict the predicate
    grades: scheme, normal-handshake outcome/negotiated version, and whether a
    TLS-1.1-capped handshake was accepted (tls11_accepted=None when this client
    cannot speak TLS 1.1 — the floor clause is then unprobeable)."""
    u = urlsplit(url)
    if u.scheme != "https":
        return {"applicable": False, "scheme": u.scheme}
    host, port = u.hostname, u.port or 443
    ok, ver = _shake(_client_ctx(), host, port)
    c11 = _tls11_client_ctx()
    if c11 is None:
        accepted11, how11 = None, "tls11-client-unsupported"
    else:
        accepted11, how11 = _shake(c11, host, port)
        if not accepted11 and how11 in _CLIENT_SIDE_UNAVAILABLE:
            accepted11 = None                # our side couldn't offer 1.1 at all
    return {"applicable": True, "scheme": "https",
            "handshake_ok": ok, "negotiated": ver,
            "tls11_accepted": accepted11, "tls11_detail": how11}


def idl053_resp(ctx):
    """Synthetic Resp carrying the probe result (no HTTP request involved)."""
    r = Resp(200, {}, b"{}")
    r.tls12 = tls12_probe(ctx.shopping_endpoint or ctx.base)
    return r


def p_tls12_minimum(r):
    t = getattr(r, "tls12", None) or {}
    if not t.get("applicable"):
        return INCONCLUSIVE                  # plain-HTTP dev server -> not-tested
    if not t.get("handshake_ok"):
        return DEVIATION                     # https declared but no TLS service
    neg = t.get("negotiated") or ""
    if neg < "TLSv1.2":                      # "TLSv1"/"TLSv1.1" sort below "TLSv1.2"
        return DEVIATION                     # negotiated below 1.2
    if t.get("tls11_accepted") is None:
        return INCONCLUSIVE                  # this stack can't probe the 1.2 floor
    if t.get("tls11_accepted"):
        return DEVIATION                     # TLS 1.1 accepted -> minimum NOT enforced
    return CLEAN


CHECKS_04_08_TLS = [
    MCheck("transport.https_tls12_minimum", ["IDL-053"], "MUST", idl053_resp,
           p_tls12_minimum,
           [],   # transport-layer: kill proof = sub-1.2 listener gate (see docstring)
           capability="dev.ucp.common.identity_linking", transport="rest",
           versions=V0408),
]

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443"
    print(tls12_probe(url))
