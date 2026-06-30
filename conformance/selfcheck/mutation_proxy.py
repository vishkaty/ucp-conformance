#!/usr/bin/env python3
"""
mutation_proxy.py — anti-false-PASS engine (red-team blocker #1).

A transparent HTTP proxy in front of the known-compliant reference server. On a
per-request basis (driven by the `X-Mutate` request header) it deliberately
corrupts the upstream RESPONSE before returning it. A conformance check run
through the proxy with a mutation active MUST fail; a check that still passes is a
no-op that can false-certify, and the mutant is reported as "survived".

This turns "the server is compliant, so my check passed" into a provable claim:
every check is exercised against both the clean response (must pass) and a battery
of mutants (must fail). The fraction of mutants caught is the kill-rate.

Mutations (X-Mutate header value; comma-separated for several):
  none                      passthrough
  drop:<key>                remove top-level (or dotted) key from a JSON response
  status:<code>             override the HTTP status code
  corrupt-json              return syntactically broken JSON
  empty                     return an empty body
  truncate:<n>              keep only the first n bytes of the body
  strip-header:<name>       remove a response header (e.g. Retry-After)
  set-field:<key>=<json>    overwrite a field with an arbitrary JSON value
  dup-id                    duplicate products[0] (breaks lookup-dedup expectations)

Run:  python3 mutation_proxy.py --upstream http://localhost:8182 --port 8183
"""
import argparse, json, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "http://localhost:8182"

def _apply(mut, status, headers, body):
    """Return (status, headers, body_bytes) after applying one mutation token."""
    name, _, arg = mut.partition(":")
    name = name.strip()
    def as_json():
        try: return json.loads(body.decode("utf-8"))
        except Exception: return None
    if name in ("", "none"):
        return status, headers, body
    if name == "status":
        return int(arg), headers, body
    if name == "empty":
        return status, headers, b""
    if name == "truncate":
        return status, headers, body[:int(arg)]
    if name == "corrupt-json":
        return status, headers, body[:-1] if body else b'{"oops'
    if name == "strip-header":
        return status, [(k, v) for k, v in headers if k.lower() != arg.lower()], body
    if name == "drop":
        d = as_json()
        if isinstance(d, dict):
            cur = d
            *parents, last = arg.split(".")
            for p in parents:
                cur = cur.get(p) if isinstance(cur, dict) else None
                if cur is None: break
            if isinstance(cur, dict):
                cur.pop(last, None)
            return status, headers, json.dumps(d).encode()
        return status, headers, body
    if name == "set-field":
        key, _, raw = arg.partition("=")
        d = as_json()
        if isinstance(d, dict):
            d[key] = json.loads(raw)
            return status, headers, json.dumps(d).encode()
        return status, headers, body
    if name == "dup-id":
        d = as_json()
        if isinstance(d, dict) and isinstance(d.get("products"), list) and d["products"]:
            d["products"].append(json.loads(json.dumps(d["products"][0])))
            return status, headers, json.dumps(d).encode()
        return status, headers, body
    # unknown mutation -> passthrough (and flag on stderr)
    print(f"[mutation_proxy] unknown mutation: {mut!r}", file=sys.stderr)
    return status, headers, body

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):  # quiet
        pass
    def _proxy(self):
        mutators = [m for m in self.headers.get("X-Mutate", "none").split(",") if m.strip()]
        length = int(self.headers.get("Content-Length", 0) or 0)
        req_body = self.rfile.read(length) if length else None
        url = self.server.upstream + self.path
        fwd = {k: v for k, v in self.headers.items()
               if k.lower() not in ("host", "x-mutate", "content-length", "connection")}
        req = urllib.request.Request(url, data=req_body, method=self.command, headers=fwd)
        try:
            with urllib.request.urlopen(req) as r:
                status, body = r.status, r.read()
                headers = [(k, v) for k, v in r.getheaders()
                           if k.lower() not in ("transfer-encoding", "connection", "content-length")]
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()
            headers = [(k, v) for k, v in e.headers.items()
                       if k.lower() not in ("transfer-encoding", "connection", "content-length")]
        except Exception as e:
            self.send_response(502); self.end_headers()
            self.wfile.write(f"proxy error: {e}".encode()); return
        for m in mutators:
            status, headers, body = _apply(m.strip(), status, headers, body)
        self.send_response(status)
        sent = False
        for k, v in headers:
            self.send_header(k, v); sent |= (k.lower() == "content-type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _proxy

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", default=UPSTREAM)
    ap.add_argument("--port", type=int, default=8183)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.upstream = args.upstream.rstrip("/")
    print(f"mutation proxy on :{args.port} -> {srv.upstream}", flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
