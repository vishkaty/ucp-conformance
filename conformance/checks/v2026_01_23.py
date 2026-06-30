#!/usr/bin/env python3
"""
v2026_01_23.py — Phase 1 conformance checks for spec 2026-01-23 (first slice).

Each check cites register requirement id(s), evaluates a real server response,
and declares the mutations that must break it. Run against the live Flower Shop
reference server, the engine self-validates each check (kill-rate) and the verdict
gate produces an honest, coverage-gated report. With only a few checks implemented
the aggregate MUST be `incomplete` (most MUSTs not-tested) — never a false green.

This slice is discovery-only; lifecycle/idempotency/order/payment checks follow the
same Check(...) pattern and are added incrementally, each kill-rate-validated.
"""
import sys, pathlib
from urllib.parse import urlparse
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Check, fetch, run_report  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "selfcheck"))
from verdict_gate import CLEAN, DEVIATION  # noqa: E402

LOOPBACK = {"localhost", "127.0.0.1", "::1"}

def _discovery(base):
    return fetch(base, "/.well-known/ucp")

def chk_profile_ok(r):
    # DISC-013: profile MUST return 200 with the expected structure (version present).
    if r.status != 200: return DEVIATION
    d = r.json
    if not isinstance(d, dict): return DEVIATION
    return CLEAN if isinstance(d.get("version"), str) else DEVIATION

def chk_rest_endpoint(r):
    # DISC-007: a service endpoint MUST be a valid https URL. Tolerate http on
    # loopback (dev servers) per AMB; a non-loopback non-https endpoint -> deviation.
    if r.status != 200 or not isinstance(r.json, dict): return DEVIATION
    svcs = (r.json.get("services") or {}).get("dev.ucp.shopping")
    if not isinstance(svcs, list): return DEVIATION
    rest = next((s for s in svcs if isinstance(s, dict) and s.get("transport") == "rest"), None)
    if not rest or not rest.get("endpoint"): return DEVIATION
    u = urlparse(rest["endpoint"])
    if not u.scheme or not u.netloc: return DEVIATION
    host = u.hostname or ""
    return CLEAN if (u.scheme == "https" or host in LOOPBACK) else DEVIATION

CHECKS = [
    Check("disc.profile_200", ["DISC-013"], "MUST", _discovery, chk_profile_ok,
          ["status:404", "status:500", "drop:version", "corrupt-json", "empty"]),
    Check("disc.rest_endpoint", ["DISC-007"], "MUST", _discovery, chk_rest_endpoint,
          ["drop:services", "set:services={}", "corrupt-json", "status:500"]),
]

SCOPE_STAMP = {
    "spec_version": "2026-01-23",
    "spec_commit": "dcf7eac71fc370dcc8768fcdbc5aa737037cca05",
    "tool": "spck.dev conformance (dev)",
    "methodology": "register-driven; kill-rate-gated; ucp-schema oracle",
}
DISCLAIMER = ("Independent, unofficial tool. Not affiliated with, endorsed by, or a "
             "substitute for the official UCP conformance suite. Results are limited "
             "to the checks listed and reflect the checks actually run.")

def main(base="http://localhost:8182"):
    rep, details = run_report(CHECKS, base, "2026-01-23", SCOPE_STAMP, DISCLAIMER)
    print(f"target: {base}\n")
    print(f"{'check':22} {'clean':6} {'kill':6} {'kill_safe':9}")
    for chk, det in details:
        print(f"{chk.id:22} {str(det['clean']):6} {det['kills']:6} {str(det['kill_safe']):9}"
              + (f"  survivors={det['survivors']}" if det.get("survivors") else ""))
    c = rep.counts
    print(f"\n--- REPORT (spec 2026-01-23 @ {SCOPE_STAMP['spec_commit'][:7]}) ---")
    print(f"aggregate: {rep.aggregate.upper()}")
    print(f"MUST coverage: {c['musts_clean_pass']}/{c['inscope_musts']} "
          f"({round(100*rep.coverage)}%)   deviations: {c['deviations']}   "
          f"blocking(not-tested/etc): {c['blocking']}")
    print("first blocking items:", "; ".join(rep.blocking[:3]),
          f"... (+{max(0,len(rep.blocking)-3)} more)")
    print("disclaimer present:", bool(rep.scope_stamp))
    assert rep.aggregate != "pass", "FALSE GREEN with partial coverage — gate failed!"
    print("\nOK: partial coverage correctly yields a non-green verdict (no false certification).")
    return 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
