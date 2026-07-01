#!/usr/bin/env python3
"""
mutation_killrate.py — proves each conformance check actually DETECTS defects.

Red-team blocker #1: a check that does nothing passes against a compliant server,
so "it passed" is meaningless until we show it FAILS when the server is broken.
This harness runs each check against (a) the clean reference server [must pass]
and (b) a battery of mutants produced by mutation_proxy.py [each must be caught].

A check that lets a mutant through ("survived") is a false-PASS hazard and is
flagged UNSAFE — it must not contribute to an aggregate green verdict. The
fraction of mutants caught is the check's kill-rate; a safe check kills 100% of
the mutations that target its requirement.

The deliberately-broken `noop` check below (always returns pass) demonstrates the
harness does its job: it should survive every mutant and be flagged UNSAFE.

Requires: reference server on :8182 and mutation_proxy on :8183.
"""
import json, sys, urllib.request, urllib.error

PROXY = "http://localhost:8183"

def fetch(path, method="GET", body=None, mutate="none"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(PROXY + path, data=data, method=method,
                                 headers={"X-Mutate": mutate,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()

def jbody(body):
    try: return json.loads(body)
    except Exception: return None

# ---- checks: a check returns "pass" | "fail" | "inconclusive" --------------
def check_discovery_version(status, body):
    """Discovery profile MUST be 200 JSON carrying a dated `version`. (DISC-style)"""
    if status != 200: return "fail"
    d = jbody(body)
    if not isinstance(d, dict): return "fail"
    v = d.get("version")
    import re
    return "pass" if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else "fail"

def check_discovery_services(status, body):
    """Discovery MUST declare a dev.ucp.shopping service with a rest endpoint."""
    if status != 200: return "fail"
    d = jbody(body)
    if not isinstance(d, dict): return "fail"
    svcs = (d.get("services") or {}).get("dev.ucp.shopping")
    if not isinstance(svcs, list): return "fail"
    return "pass" if any(s.get("transport") == "rest" and s.get("endpoint") for s in svcs) else "fail"

def check_noop(status, body):
    """DELIBERATELY BROKEN: always passes. Must be flagged UNSAFE by this harness."""
    return "pass"

CHECKS = [
    {"id": "disc_version", "path": "/.well-known/ucp", "fn": check_discovery_version,
     "mutations": ["drop:version", "set-field:version=\"draft\"", "corrupt-json",
                   "status:503", "empty"]},
    {"id": "disc_services", "path": "/.well-known/ucp", "fn": check_discovery_services,
     "mutations": ["drop:services", "set-field:services={}", "corrupt-json", "status:500"]},
    {"id": "noop_DEMO", "path": "/.well-known/ucp", "fn": check_noop,
     "mutations": ["drop:version", "corrupt-json", "status:500", "empty"]},
]

def run():
    overall_ok = True
    print(f"{'check':16} {'clean':6} {'kill-rate':10} verdict")
    for c in CHECKS:
        st, bd = fetch(c["path"], mutate="none")
        clean = c["fn"](st, bd)
        killed = survived = 0
        survivors = []
        for m in c["mutations"]:
            st, bd = fetch(c["path"], mutate=m)
            v = c["fn"](st, bd)
            if v == "fail": killed += 1
            else: survived += 1; survivors.append(m)
        total = killed + survived
        safe = (clean == "pass" and survived == 0)
        overall_ok &= safe or c["id"].startswith("noop")  # noop is expected-unsafe demo
        verdict = "SAFE" if safe else ("UNSAFE(expected, demo)" if c["id"].startswith("noop")
                                       else "UNSAFE")
        print(f"{c['id']:16} {clean:6} {killed}/{total:<8} {verdict}"
              + (f"  survivors={survivors}" if survivors and not c['id'].startswith('noop') else ""))
        if survivors and c["id"].startswith("noop"):
            print(f"                 -> demo confirms harness flags no-op checks "
                  f"(survived {survivors})")
    print(f"\nkill-rate harness: {'PASS' if overall_ok else 'FAIL'} "
          f"(every real check kills 100% of its targeted mutants; clean passes)")
    return 0 if overall_ok else 1

if __name__ == "__main__":
    sys.exit(run())
