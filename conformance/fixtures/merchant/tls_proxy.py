#!/usr/bin/env python3
"""
tls_proxy.py — TLS-terminating harness for CHK-051 ("All REST endpoints MUST be
served over HTTPS with minimum TLS version 1.3", 2026-01-23 + 2026-01-11).

Runs TWO listeners in front of the plain-HTTP controlled fixture:
  :8443  GOLDEN   — accepts ONLY TLS 1.3 (minimum_version = TLSv1_3). The check must
                    CLEAN-PASS here: https scheme, 1.3 negotiated, a 1.2-capped
                    handshake refused.
  :8444  NEGATIVE — accepts up to TLS 1.2 (maximum_version = TLSv1_2). A server like
                    this violates the MUST; the check must DEVIATE here. This is the
                    kill-fixture: transport behavior can't be injected by the response
                    mutation engine, so the negative listener plays the mutant.

The certificate is a throwaway self-signed EC cert minted at boot with the openssl
CLI (present on macOS + ubuntu CI runners) — nothing is checked into git. The check
probes TLS VERSION policy, not certificate chains, so verification is disabled on
the probe side.

    python3 conformance/fixtures/merchant/tls_proxy.py \
        [--upstream 127.0.0.1:8184] [--golden-port 8443] [--negative-port 8444]
"""
import argparse, socket, ssl, subprocess, sys, tempfile, threading, pathlib

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

def main():
    ap = argparse.ArgumentParser(description="TLS 1.3 golden + TLS<=1.2 negative proxies.")
    ap.add_argument("--upstream", default="127.0.0.1:8184")
    ap.add_argument("--golden-port", type=int, default=8443)
    ap.add_argument("--negative-port", type=int, default=8444)
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

    threading.Thread(target=serve, args=(args.negative_port, negative, upstream, "negative"),
                     daemon=True).start()
    serve(args.golden_port, golden, upstream, "golden")

if __name__ == "__main__":
    sys.exit(main() or 0)
