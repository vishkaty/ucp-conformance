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
    # identity-linking (04-08 rework): IDL rows apply only to businesses that
    # declare the capability — never in the denominator for merchants without it
    "identity-linking": "dev.ucp.common.identity_linking",
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
        # MCP transport (JSON-RPC tools/call): checks over MCP run if it's declared.
        mcp = next((s for s in svc if isinstance(s, dict) and s.get("transport") == "mcp"), None)
        self.has_mcp = mcp is not None
        self.mcp_endpoint = (mcp or {}).get("endpoint")
        self.product_id = self.config.get("product_id")

def _wellknown_url(base):
    """Build the discovery URL, PRESERVING any query string — multi-tenant platform
    gateways route the merchant via e.g. ?domain=store.example.com."""
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(base)
    path = (p.path.rstrip("/") or "") + "/.well-known/ucp"
    return urlunsplit((p.scheme, p.netloc, path, p.query, ""))

def discover(base):
    import engine
    try:
        with urllib.request.urlopen(_wellknown_url(base), timeout=10,
                                    context=engine._SSL_CTX) as r:
            return json.loads(r.read()), r.headers.get("Content-Type", "")
    except Exception as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            raise SystemExit(
                f"discovery failed for {base}: TLS certificate verification failed.\n"
                f"  Your Python has no CA bundle. Fix with one of:\n"
                f"    • pip install certifi         (spck-conformance uses it automatically)\n"
                f"    • macOS python.org build: run 'Install Certificates.command'\n"
                f"    • re-run with --insecure       (skips TLS verification; testing only)")
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

def scaffold_config(ctx):
    """Build a starter --config for THIS server: detected product + FILL_ME placeholders
    for the data-dependent inputs each declared capability needs. Friendly onboarding."""
    caps = ctx.capabilities
    def pay(desc):
        return {"payment": {"instruments": [{"id": "instr_1", "handler_id": "FILL_ME: handler id",
            "type": "card", "display": {"brand": "Visa", "last_digits": "1234"},
            "credential": {"type": "token", "token": desc},
            "billing_address": {"street_address": "1 Main St", "address_locality": "Town",
                "address_region": "CA", "address_country": "US", "postal_code": "12345"}}]},
            "risk_signals": {}}
    cfg = {"product_id": ctx.product_id or "FILL_ME: an in-stock product id",
           "currency": "USD"}
    if "dev.ucp.shopping.checkout" in caps:
        cfg["out_of_stock_id"] = "FILL_ME: a product id known to be out of stock"
    if "dev.ucp.shopping.order" in caps:
        cfg["fulfillment_option_id"] = "FILL_ME: a valid fulfillment option id"
        cfg["complete_payment"] = pay("FILL_ME: a payment token that SUCCEEDS")
        cfg["fail_payment"] = pay("FILL_ME: a payment token that FAILS (expect 402)")
    if "dev.ucp.shopping.discount" in caps:
        cfg["discount"] = {"valid_code": "FILL_ME: a real discount code",
                           "second_valid_code": "FILL_ME: another real code",
                           "invalid_code": "NOT_A_REAL_CODE"}
    if "dev.ucp.shopping.catalog.lookup" in caps:
        cfg["catalog"] = {"variant_id": ctx.product_id or "FILL_ME: a variant id"}
    return cfg

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
    ap.add_argument("--init", nargs="?", const="merchant.json", metavar="FILE",
                    help="probe the server and scaffold a starter --config to FILE, then exit")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS certificate verification (testing only)")
    args = ap.parse_args()
    if args.insecure:
        import engine; engine.set_insecure(True)
    config = json.loads(pathlib.Path(args.config).read_text()) if args.config else {}
    ctx, ctype, areas = report(args.server, config)
    if args.init:
        cfg = scaffold_config(ctx)
        pathlib.Path(args.init).write_text(json.dumps(cfg, indent=2) + "\n")
        fills = sum(1 for v in json.dumps(cfg).split('"') if v.startswith("FILL_ME"))
        print(f"Wrote {args.init} for {ctx.base} (spec {ctx.version}).")
        print(f"  detected capabilities: {', '.join(sorted(ctx.capabilities)) or '(none)'}")
        print(f"  product for lifecycle: {ctx.product_id or '(none auto-discovered)'}")
        print(f"\nNext: replace the {fills} FILL_ME placeholder(s) with real values, then run:")
        print(f"  spck-conformance --server {ctx.base} --config {args.init}")
        return 0
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
    # ---- grouped, scannable report ------------------------------------------
    passed  = [(c, d) for c, d in detail if d["status"] == "clean-pass"]
    devs    = [(c, d) for c, d in detail if d["status"] == "deviation"]
    nottest = [(c, d) for c, d in detail if str(d["status"]).startswith("not-tested")]
    napp    = [(c, d) for c, d in detail if str(d["status"]).startswith("not-applicable")]
    other   = [(c, d) for c, d in detail if (c, d) not in passed + devs + nottest + napp]

    print(f"═══ UCP conformance report (unofficial) — {ctx.base} ═══")
    print(f"  spec {ctx.version} · endpoint {ctx.shopping_endpoint}")
    print(f"  capabilities: {', '.join(supported) or '(none declared)'}")
    print(f"  product for lifecycle: {ctx.product_id or '(none)'}\n")
    print(f"  VERDICT: {rep.aggregate.upper()} — "
          f"{cc['musts_clean_pass']}/{cc['inscope_musts']} applicable MUSTs "
          f"({round(100*rep.coverage)}%), {cc['deviations']} deviation(s)")
    print(f"  [{len(passed)} passed · {len(devs)} deviations · {len(nottest)} not-tested "
          f"· {len(napp)} not-applicable]")

    if devs:
        print(f"\n  ✗ DEVIATIONS ({len(devs)}) — a MUST was violated:")
        for c, d in devs:
            print(f"    {c.id}")
            for x in _citations(c.req_ids, meta):
                print(f"        expected  {x['id']}: {x['requirement']}")
                print(f"        spec      {x['source']}")
            obs = d.get("observed") or {}
            print(f"        observed  HTTP {obs.get('status')}  body: {obs.get('body')}")
    if passed:
        print(f"\n  ✓ PASSED ({len(passed)}) — satisfied & kill-safe:")
        for c, _ in passed:
            print(f"        {c.id}")
    if nottest:
        print(f"\n  ⊘ NOT TESTED ({len(nottest)}) — need config/data (not a failure):")
        for c, d in nottest:
            reason = str(d["status"]).replace("not-tested", "").strip("() ")
            print(f"        {c.id:30} {reason or 'needs data'}")
    if napp:
        print(f"\n  — NOT APPLICABLE ({len(napp)}) — capability/transport not declared:")
        print(f"        {', '.join(c.id for c, _ in napp)}")
    if other:
        print(f"\n  ? INCONCLUSIVE ({len(other)}):")
        for c, d in other:
            print(f"        {c.id:30} {d['status']}")
    if args.junit:
        print(f"\n  JUnit report written to {args.junit}")

    # ---- friendly next steps -------------------------------------------------
    n_pass = sum(1 for _, d in detail if d["status"] == "clean-pass")
    n_dev = cc["deviations"]
    needed = set()
    for c, d in detail:
        st = str(d["status"])
        if st.startswith("not-tested (needs config"):
            needed.update(st.split("needs config:")[1].rstrip(")").strip().split(","))
    print("\n  Next steps:")
    if n_dev:
        print(f"    • Fix {n_dev} MUST deviation(s) above — each shows expected vs the observed response.")
    if needed:
        keys = ", ".join(sorted(k.strip() for k in needed if k.strip()))
        hint = "--init to scaffold one" if not args.config else f"add: {keys}"
        print(f"    • Unlock more checks by supplying config ({hint}).")
    if ctx.product_id is None and "dev.ucp.shopping.checkout" in ctx.capabilities:
        print(f"    • Set config.product_id to a real in-stock product to run the lifecycle checks.")
    if not n_dev and not needed:
        print(f"    • Looking good — {n_pass} checks clean-pass, 0 deviations for the checks run.")
    if not args.config:
        print(f"    • Tip: `spck-conformance --server {ctx.base} --init` scaffolds a config for this server.")
    print(f"\n  {DISCLAIMER}")
    return rc

if __name__ == "__main__":
    sys.exit(main())
