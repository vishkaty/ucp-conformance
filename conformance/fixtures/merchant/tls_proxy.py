#!/usr/bin/env python3
"""
tls_proxy.py — TLS-terminating harness for the transport-security checks:
CHK-051 ("All REST endpoints MUST be served over HTTPS with minimum TLS version
1.3", 2026-01-23 + 2026-01-11) and IDL-053 ("All communication between platform
and business MUST use HTTPS with a minimum of TLS 1.2", 2026-04-08).

Runs THREE listeners in front of the plain-HTTP controlled fixture:
  :8443  GOLDEN   — accepts ONLY TLS 1.3 (minimum_version = TLSv1_3). CHK-051 must
                    CLEAN-PASS here: https scheme, 1.3 negotiated, a 1.2-capped
                    handshake refused. (1.3-only also satisfies IDL-053's >=1.2.)
  :8444  NEGATIVE for CHK-051 — accepts up to TLS 1.2 (maximum_version = TLSv1_2).
                    Violates the 01-era 1.3-minimum MUST; CHK-051 must DEVIATE here.
                    NOTE it *enforces* a 1.2 floor, so it doubles as a SECOND golden
                    for IDL-053 (a 04-08-conformant, 01-era-violating server).
  :8445  SUB-1.2 NEGATIVE for IDL-053 — accepts TLS 1.1 (floor below 1.2 via
                    OpenSSL @SECLEVEL=0; sub-1.2 protocols are compiled out of
                    default cipher policy on modern stacks). A server like this
                    violates IDL-053's minimum; the 04-08 check must DEVIATE here.
                    If the local OpenSSL cannot re-enable TLS 1.1 (e.g. LibreSSL /
                    FIPS builds), this listener is skipped with a warning and the
                    IDL-053 gate reports the kill unprovable in this environment.

The kill-fixtures exist because transport behavior can't be injected by the
response-mutation engine — the listeners play the mutants.

The certificate is a throwaway self-signed EC cert minted at boot with the openssl
CLI (present on macOS + ubuntu CI runners) — nothing is checked into git. The checks
probe TLS VERSION policy, not certificate chains, so verification is disabled on
the probe side.

    python3 conformance/fixtures/merchant/tls_proxy.py \
        [--upstream 127.0.0.1:8184] [--golden-port 8443] [--negative-port 8444] \
        [--sub12-port 8445]
"""
import argparse, socket, ssl, subprocess, sys, tempfile, threading, pathlib, warnings

def mint_cert(tmpdir):
    """Self-signed EC P-256 cert for localhost, valid 1 day. Test-only."""
    crt, key = str(pathlib.Path(tmpdir, "tls.crt")), str(pathlib.Path(tmpdir, "tls.key"))
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
         "-keyout", key, "-out", crt, "-days", "1", "-nodes",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True)
    return crt, key

def _pump(a, b):
    """Relay bytes a -> b until EOF/error, then shut down b's write side."""
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        try: b.shutdown(socket.SHUT_WR)
        except OSError: pass

def serve(port, ssl_ctx, upstream, label):
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind(("127.0.0.1", port))
    lsock.listen(16)
    print(f"tls-proxy [{label}] listening on :{port} -> {upstream[0]}:{upstream[1]}",
          flush=True)
    while True:
        try:
            raw, _ = lsock.accept()
        except OSError:
            return
        def handle(raw=raw):
            try:
                tls = ssl_ctx.wrap_socket(raw, server_side=True)
            except (ssl.SSLError, OSError):
                try: raw.close()
                except OSError: pass
                return                      # refused handshake (e.g. capped client on golden)
            try:
                up = socket.create_connection(upstream, timeout=10)
            except OSError:
                tls.close(); return
            t = threading.Thread(target=_pump, args=(up, tls), daemon=True)
            t.start()
            _pump(tls, up)
            t.join(timeout=5)
            for s in (tls, up):
                try: s.close()
                except OSError: pass
        threading.Thread(target=handle, daemon=True).start()

def sub12_context(crt, key):
    """A server context whose protocol floor is BELOW TLS 1.2 (accepts TLS 1.1) —
    the IDL-053 mutant. Modern OpenSSL only speaks sub-1.2 at @SECLEVEL=0; returns
    None when the local stack cannot build such a listener (LibreSSL, FIPS)."""
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_1
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ctx.load_cert_chain(crt, key)
        return ctx
    except (ssl.SSLError, ValueError, OSError):
        return None

def main():
    ap = argparse.ArgumentParser(
        description="TLS 1.3 golden + TLS<=1.2 negative + sub-1.2 negative proxies.")
    ap.add_argument("--upstream", default="127.0.0.1:8184")
    ap.add_argument("--golden-port", type=int, default=8443)
    ap.add_argument("--negative-port", type=int, default=8444)
    ap.add_argument("--sub12-port", type=int, default=8445)
    args = ap.parse_args()
    host, port = args.upstream.rsplit(":", 1)
    upstream = (host, int(port))

    tmpdir = tempfile.mkdtemp(prefix="spck_tls_")
    try:
        crt, key = mint_cert(tmpdir)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"tls-proxy: cannot mint self-signed cert (openssl needed): {e}", file=sys.stderr)
        return 2

    golden = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    golden.minimum_version = ssl.TLSVersion.TLSv1_3     # the MUST, enforced
    golden.load_cert_chain(crt, key)

    negative = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    negative.minimum_version = ssl.TLSVersion.TLSv1_2   # accepts 1.2 -> violates the MUST
    negative.maximum_version = ssl.TLSVersion.TLSv1_2
    negative.load_cert_chain(crt, key)

    sub12 = sub12_context(crt, key)
    if sub12 is None:
        print("tls-proxy: local OpenSSL cannot speak TLS 1.1 — sub-1.2 negative "
              "listener SKIPPED (IDL-053 kill unprovable here)", flush=True)
    else:
        threading.Thread(target=serve, args=(args.sub12_port, sub12, upstream, "sub12"),
                         daemon=True).start()
    threading.Thread(target=serve, args=(args.negative_port, negative, upstream, "negative"),
                     daemon=True).start()
    serve(args.golden_port, golden, upstream, "golden")

if __name__ == "__main__":
    sys.exit(main() or 0)
