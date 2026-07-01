#!/usr/bin/env python3
"""
merchant.py — Phase A: the MERCHANT-AGNOSTIC conformance runner.

Point it at ANY UCP server. It discovers the server's spec version + declared
capabilities from /.well-known/ucp, then runs only the checks that apply:
  * discovery/structural checks run on every server (no seeded data);
  * extension checks (fulfillment, discount, catalog, cart) run ONLY if the server
    declares that capability — otherwise `not-applicable` (never a deviation);
  * data-dependent lifecycle checks run against an auto-discovered product (catalog
    search) or a product id from an optional merchant config; otherwise `not-tested`.

The verdict denominator is the set of APPLICABLE testable MUSTs (extensions a server
doesn't implement are excluded), so a lean-but-correct merchant scores honestly.

  merchant.py --server https://api.example.com [--config merchant.json] [--json]
"""
import sys, json, argparse, pathlib, urllib.request, urllib.error, glob
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import fetch, Resp                    # noqa: E402
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))
from verdict_gate import aggregate                # noqa: E402
import merchant_checks                            # noqa: E402
REQ_DIR = HERE.parents[0] / "requirements"

# register area -> the capability a server must declare for that area to be in-scope
AREA_CAPABILITY = {
    "fulfillment": "dev.ucp.shopping.fulfillment",
    "discount-consent-identity": "dev.ucp.shopping.discount",
    "discounts-consent": "dev.ucp.shopping.discount",
    "catalog": "dev.ucp.shopping.catalog.search",
    "cart": "dev.ucp.shopping.cart",
    "signals-attribution-eligibility": None,   # informational; treat as core
    "order": "dev.ucp.shopping.order",
}
CORE_CAP = "dev.ucp.shopping.checkout"

class MerchantCtx:
    def __init__(self, base, profile, config):
        self.base = base.rstrip("/")
        self.profile = profile
        self.config = config or {}
        ucp = profile.get("ucp", profile)          # profile may or may not nest under "ucp"
        self.version = ucp.get("version")
        caps = ucp.get("capabilities")
        # Spec requires capabilities to be a keyed object of reverse-domain names.
        # Never crash on a non-conformant shape (a real sample ships a list): treat it
        # as no declared capabilities (extension checks -> not-applicable) and flag it,
        # so the profile-structure check can report the deviation instead of exploding.
        if isinstance(caps, dict):
            self.capabilities = set(caps.keys())
            self.caps_malformed = False
        else:
            self.capabilities = set()
            self.caps_malformed = caps is not None
        svc = (ucp.get("services") or {}).get("dev.ucp.shopping") or []
        rest = next((s for s in svc if isinstance(s, dict) and s.get("transport") == "rest"), None)
        # A server MAY offer only MCP/embedded transports and still be fully conformant.
        # This runner is REST-scoped, so absence of a REST transport makes the REST
        # lifecycle out-of-scope (not-applicable), never a deviation.
        self.has_rest = rest is not None
        self.transports = [s.get("transport") for s in svc if isinstance(s, dict)]
        self.shopping_endpoint = (rest or {}).get("endpoint", self.base)
        self.product_id = self.config.get("product_id")

def discover(base):
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/.well-known/ucp", timeout=10) as r:
            return json.loads(r.read()), r.headers.get("Content-Type", "")
    except Exception as e:
        raise SystemExit(f"discovery failed for {base}: {e}")

def auto_discover_product(ctx):
    """If the server supports catalog.search, find a real product id to drive lifecycle."""
    if ctx.product_id:
        return ctx.product_id
    if not ctx.has_rest:                     # REST catalog search only; never probe a non-REST store
        return None
    if "dev.ucp.shopping.catalog.search" not in ctx.capabilities:
        return None
    try:
        r = fetch(ctx.shopping_endpoint, "/catalog/search", "POST", {"query": "*"},
                  {"UCP-Agent": 'profile="https://spck.dev/agent"'})
        prods = (r.json or {}).get("products") or []
        if prods:
            v = (prods[0].get("variants") or [{}])[0]
            return v.get("id") or prods[0].get("id")
    except Exception:
        pass
    return None

def applicable_areas(ctx):
    """Which register areas are in scope for THIS server, given its declared capabilities."""
    out = {}
    for area, cap in AREA_CAPABILITY.items():
        out[area] = (cap is None) or (cap in ctx.capabilities) or (cap == CORE_CAP)
    return out

def report(base, config=None):
    profile, ctype = discover(base)
    ctx = MerchantCtx(base, profile, config)
    ctx.product_id = auto_discover_product(ctx)
    areas = applicable_areas(ctx)
    return ctx, ctype, areas

def applicable_musts(ctx, areas):
    """Testable MUSTs from the merchant's spec-version register, restricted to areas
    the server implements (unsupported extensions are excluded from the denominator)."""
    ids = set()
    vdir = REQ_DIR / (ctx.version or "")
    if not vdir.is_dir():
        return ids
    for f in glob.glob(str(vdir / "*.json")):
        area = json.load(open(f)).get("_area", "?")
        if areas.get(area, True) is False:
            continue
        for r in json.load(open(f)).get("rows", []):
            if r["keyword"] in ("MUST", "MUST NOT") and r["testability"] == "testable" \
               and any(t in ("rest", "any") for t in r.get("transport", [])):
                ids.add(r["id"])
    return ids

SCOPE = {"tool": "spck.dev merchant conformance (dev)",
         "methodology": "discovery-driven, capability-adaptive, kill-rate-gated"}
DISCLAIMER = ("Unofficial. Not affiliated with or endorsed by the UCP project. A pass "
             "reflects only the checks run against this server; not certified compliance.")

def run_conformance(ctx, areas):
    results, detail = merchant_checks.run_merchant_checks(ctx)
    stamp = {**SCOPE, "spec_version": ctx.version, "server": ctx.base}
    rep = aggregate(results, applicable_musts(ctx, areas), stamp, DISCLAIMER)
    return rep, detail

# Synthetic (non-register) check ids get a plain-language description so their reports
# are still actionable (e.g. the holistic profile-schema validation).
SYNTHETIC_REQS = {
    "DISC-000": {"keyword": "MUST", "source": "ucp:source/schemas/ucp.json",
                 "requirement": "The /.well-known/ucp profile document MUST validate against "
                                "the official ucp.json profile schema (structure of version, "
                                "services array, and reverse-domain-keyed capabilities object)."},
}

def req_meta(version):
    """req_id -> {requirement, source} from the register, so every result cites its
    normative clause (the trust story: each check traces to a verbatim spec quote)."""
    meta = dict(SYNTHETIC_REQS)
    vdir = REQ_DIR / (version or "")
    if vdir.is_dir():
        for f in glob.glob(str(vdir / "*.json")):
            for r in json.load(open(f)).get("rows", []):
                meta[r["id"]] = {"requirement": r.get("requirement", ""),
                                 "source": r.get("source", ""), "keyword": r.get("keyword", "")}
    return meta

def _citations(req_ids, meta):
    return [{"id": rid, **meta.get(rid, {"requirement": "", "source": ""})} for rid in req_ids]

def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))

def junit_xml(ctx, detail, meta):
    """Render results as JUnit XML so any CI can consume the report as a test run.
    deviation -> <failure>; not-applicable/not-tested -> <skipped>; clean -> pass."""
    cases, n_fail, n_skip = [], 0, 0
    for c, d in detail:
        st = str(d["status"])
        cites = "; ".join(f"{x['id']}: {x['requirement']} [{x['source']}]"
                          for x in _citations(c.req_ids, meta))
        name = f"{c.id} ({', '.join(c.req_ids)})"
        body = ""
        if st == "deviation":
            n_fail += 1
            obs = d.get("observed") or {}
            evidence = (f"expected: {cites}\nobserved: HTTP {obs.get('status')} "
                        f"body: {obs.get('body')}")
            body = (f'\n      <failure message="MUST violated: {_xml_escape(c.req_ids)}">'
                    f'{_xml_escape(evidence)}</failure>\n    ')
        elif st.startswith(("not-applicable", "not-tested")):
            n_skip += 1
            body = f'\n      <skipped message="{_xml_escape(st)}"/>\n    '
        cases.append(f'    <testcase classname="ucp.{_xml_escape(ctx.version)}" '
                     f'name="{_xml_escape(name)}">{body}</testcase>')
    suite = (f'  <testsuite name="ucp-merchant-conformance" tests="{len(detail)}" '
             f'failures="{n_fail}" skipped="{n_skip}" '
             f'hostname="{_xml_escape(ctx.base)}">\n' + "\n".join(cases) + "\n  </testsuite>")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<testsuites>\n' + suite + "\n</testsuites>\n"

def main():
    ap = argparse.ArgumentParser(description="Merchant-agnostic UCP conformance (unofficial).")
    ap.add_argument("--server", required=True)
    ap.add_argument("--config", help="optional merchant config JSON (product_id, out_of_stock_id, fail_token, discount_codes, ...)")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    ap.add_argument("--junit", metavar="FILE", help="write a JUnit XML report to FILE (for CI)")
    args = ap.parse_args()
    config = json.loads(pathlib.Path(args.config).read_text()) if args.config else {}
    ctx, ctype, areas = report(args.server, config)
    meta = req_meta(ctx.version)
    supported = sorted(c for c in ctx.capabilities)
    out = {
        "server": ctx.base, "spec_version": ctx.version,
        "content_type_json": "application/json" in ctype.lower(),
        "capabilities": supported,
        "shopping_endpoint": ctx.shopping_endpoint,
        "product_for_lifecycle": ctx.product_id,
        "applicable_areas": {a: v for a, v in areas.items()},
    }
    rep, detail = run_conformance(ctx, areas)
    cc = rep.counts
    out["verdict"] = {"aggregate": rep.aggregate, "coverage": rep.coverage,
                      "applicable_musts": cc["inscope_musts"], "musts_passed": cc["musts_clean_pass"],
                      "deviations": cc["deviations"]}
    out["checks"] = [{"id": c.id, "req_ids": c.req_ids, "capability": c.capability,
                      "status": d["status"], "kill_safe": d["kill_safe"],
                      "requirements": _citations(c.req_ids, meta),
                      # actionable evidence: what the server actually returned
                      "observed": d.get("observed")} for c, d in detail]
    out["disclaimer"] = DISCLAIMER
    # exit code for CI: 2 if any MUST deviation, else 0 (partial coverage is not a failure)
    rc = 2 if cc["deviations"] else 0
    if args.junit:
        pathlib.Path(args.junit).write_text(junit_xml(ctx, detail, meta))
    if args.json:
        print(json.dumps(out, indent=2)); return rc
    print(f"Merchant conformance report (UNOFFICIAL) — {ctx.base}\n")
    print(f"  spec version {ctx.version} · JSON {out['content_type_json']} · endpoint {ctx.shopping_endpoint}")
    print(f"  declared capabilities: {', '.join(supported) or '(none)'}")
    print(f"  product for lifecycle: {ctx.product_id or '(none — pass --config product_id)'}\n")
    for c, d in detail:
        st = d["status"]
        mark = {"not-applicable":"— n/a","not-tested (no product)":"— not-tested"}.get(st, st)
        print(f"    {c.id:30} {str(mark):12}" + (f" kill_safe={d['kill_safe']}" if d.get("kill_safe") is not None else "")
              + (f"  survivors={d['survivors']}" if d.get("survivors") else ""))
        if st == "deviation":                       # actionable: expected vs observed
            for x in _citations(c.req_ids, meta):
                print(f"        expected  {x['id']}: {x['requirement']}")
                print(f"        spec      {x['source']}")
            obs = d.get("observed") or {}
            print(f"        observed  HTTP {obs.get('status')}  body: {obs.get('body')}")
    print(f"\n  aggregate: {rep.aggregate.upper()}   "
          f"MUST coverage: {cc['musts_clean_pass']}/{cc['inscope_musts']} applicable "
          f"({round(100*rep.coverage)}%)   deviations: {cc['deviations']}")
    if args.junit:
        print(f"  JUnit report written to {args.junit}")
    print(f"\n  {DISCLAIMER}")
    return rc

if __name__ == "__main__":
    sys.exit(main())
