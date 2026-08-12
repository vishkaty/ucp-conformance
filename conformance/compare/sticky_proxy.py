#!/usr/bin/env python3
"""
sticky_proxy.py — a WHOLE-SERVER mutant for the suite-vs-suite comparison (P2-13).

The selfcheck mutation_proxy applies a mutation only to requests that carry an
X-Mutate header — perfect for kill-testing OUR checks one probe at a time, but
useless for a fair head-to-head: the official suite does not (and must not need
to) cooperate with our harness. This proxy instead applies ONE fixed mutation,
chosen at boot, to EVERY matching upstream response. The result behaves exactly
like a defective merchant server: both suites are pointed at the same URL, see
the same defect, and neither knows the harness exists.

Fairness properties (deliberate):
  * Client X-Mutate headers are STRIPPED — no suite can widen or narrow the
    mutation from the outside; the defect is fixed at boot (deterministic).
  * The match rule (method/path/status class) models a SERVER defect ("this
    server never includes `status` in checkout bodies"), not a single corrupted
    response — so a suite that probes the surface twice sees the defect twice.
  * URL rewriting (upstream base -> proxy base) is applied to ALL JSON bodies in
    BOTH passthrough and mutated modes, because both suites discover the
    shopping endpoint from /.well-known/ucp and must keep talking through the
    proxy. Identical for both suites, identical in baseline and mutant runs.

Mutation tokens: everything selfcheck/mutation_proxy.py supports (drop:,
set-field:, status:, corrupt-json, empty, truncate:, strip-header:, dup-id)
plus comparison-specific tokens for array-of-totals defects:
  tot-del:<type>            remove every top-level totals entry of <type>
  tot-negate:<type>         flip the sign of every top-level totals[<type>].amount
  tot-off-by:<type>=<n>     add n (minor units) to every totals[<type>].amount
  tot-strip-amount:<type>   remove `amount` from every totals entry of <type>
  ful-opt-del:<key>         remove <key> from every fulfillment option
  set-header:<name>=<val>   set/replace a response header

Run:
  python3 sticky_proxy.py --upstream http://localhost:8290 --port 8291 \
      --mutate tot-negate:discount --match-path '^/checkout-sessions' \
      --match-status 2xx
"""
import argparse, json, pathlib, re, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))
sys.path.insert(0, str(HERE.parents[0] / "checks"))
from mutation_proxy import _apply as _base_apply          # noqa: E402


def _walk_totals(doc, ttype):
    """Yield every TOP-LEVEL totals entry of the given well-known type."""
    if isinstance(doc, dict):
        for t in doc.get("totals") or []:
            if isinstance(t, dict) and t.get("type") == ttype:
                yield t


def _apply_compare(mut, status, headers, body):
    """Comparison-specific tokens; fall through to the selfcheck engine."""
    name, _, arg = mut.partition(":")
    name = name.strip()

    def as_json():
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    if name == "tot-del":
        d = as_json()
        if isinstance(d, dict) and isinstance(d.get("totals"), list):
            d["totals"] = [t for t in d["totals"]
                           if not (isinstance(t, dict) and t.get("type") == arg)]
            return status, headers, json.dumps(d).encode()
        return status, headers, body
    if name == "tot-negate":
        d = as_json()
        changed = False
        for t in _walk_totals(d, arg):
            if isinstance(t.get("amount"), int):
                t["amount"] = -t["amount"]
                changed = True
        return (status, headers, json.dumps(d).encode()) if changed else (status, headers, body)
    if name == "tot-off-by":
        ttype, _, n = arg.partition("=")
        d = as_json()
        changed = False
        for t in _walk_totals(d, ttype):
            if isinstance(t.get("amount"), int):
                t["amount"] += int(n)
                changed = True
        return (status, headers, json.dumps(d).encode()) if changed else (status, headers, body)
    if name == "tot-strip-amount":
        d = as_json()
        changed = False
        for t in _walk_totals(d, arg):
            if t.pop("amount", None) is not None:
                changed = True
        return (status, headers, json.dumps(d).encode()) if changed else (status, headers, body)
    if name == "ful-opt-del":
        d = as_json()
        changed = False
        if isinstance(d, dict):
            for m in ((d.get("fulfillment") or {}).get("methods") or []):
                for g in (m.get("groups") or []):
                    for o in (g.get("options") or []):
                        if isinstance(o, dict) and o.pop(arg, None) is not None:
                            changed = True
        return (status, headers, json.dumps(d).encode()) if changed else (status, headers, body)
    if name == "set-header":
        hname, _, hval = arg.partition("=")
        headers = [(k, v) for k, v in headers if k.lower() != hname.lower()]
        headers.append((hname, hval))
        return status, headers, body
    return _base_apply(mut, status, headers, body)


def _status_matches(rule, status):
    if rule in ("", "all"):
        return True
    if rule.endswith("xx"):
        return status // 100 == int(rule[0])
    return status == int(rule)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _proxy(self):
        srv = self.server
        length = int(self.headers.get("Content-Length", 0) or 0)
        req_body = self.rfile.read(length) if length else None
        url = srv.upstream + self.path
        # X-Mutate is stripped: no client can steer the defect (see module doc).
        # Accept-Encoding is stripped too (forcing identity): if the upstream
        # ever gzipped, JSON mutation tokens would silently no-op for exactly
        # the clients that negotiated compression — a per-client asymmetry this
        # proxy exists to make impossible.
        fwd = {k: v for k, v in self.headers.items()
               if k.lower() not in ("host", "x-mutate", "content-length",
                                    "connection", "accept-encoding")}
        req = urllib.request.Request(url, data=req_body, method=self.command, headers=fwd)
        try:
            with urllib.request.urlopen(req) as r:
                status, body = r.status, r.read()
                headers = [(k, v) for k, v in r.getheaders()
                           if k.lower() not in ("transfer-encoding", "connection",
                                                "content-length")]
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()
            headers = [(k, v) for k, v in e.headers.items()
                       if k.lower() not in ("transfer-encoding", "connection",
                                            "content-length")]
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"proxy error: {e}".encode())
            return
        # Endpoint rewrite so discovered endpoints keep pointing at the proxy —
        # applied identically in passthrough and mutated modes, for every client.
        for frm, to in srv.rewrites:
            body = body.replace(frm.encode(), to.encode())
        if (srv.mutations
                and self.command in srv.match_methods
                and srv.match_path.search(self.path)
                and _status_matches(srv.match_status, status)):
            for m in srv.mutations:
                status, headers, body = _apply_compare(m.strip(), status, headers, body)
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _proxy


def serve(upstream, port, mutate="", match_path=".*", match_methods=None,
          match_status="all", rewrites=()):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.upstream = upstream.rstrip("/")
    srv.mutations = [m for m in (mutate or "").split(",") if m.strip()]
    srv.match_path = re.compile(match_path)
    srv.match_methods = set((match_methods or "GET,POST,PUT,DELETE,PATCH").split(","))
    srv.match_status = match_status
    srv.rewrites = list(rewrites)
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--mutate", default="", help="comma-separated mutation tokens; empty = passthrough")
    ap.add_argument("--match-path", default=".*", help="regex on the request path")
    ap.add_argument("--match-methods", default="GET,POST,PUT,DELETE,PATCH")
    ap.add_argument("--match-status", default="all", help="all | 2xx | 4xx | <code>")
    ap.add_argument("--rewrite", action="append", default=[],
                    metavar="FROM=TO", help="rewrite FROM to TO in every response body")
    args = ap.parse_args()
    rewrites = [tuple(r.split("=", 1)) for r in args.rewrite]
    srv = serve(args.upstream, args.port, args.mutate, args.match_path,
                args.match_methods, args.match_status, rewrites)
    print(f"sticky proxy on :{args.port} -> {srv.upstream} "
          f"mutate={srv.mutations or 'passthrough'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
