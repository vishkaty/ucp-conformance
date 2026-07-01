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
import sys, json, argparse, pathlib, urllib.request, urllib.error
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import fetch, Resp                    # noqa: E402

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
        caps = ucp.get("capabilities") or {}
        self.capabilities = set(caps.keys())
        svc = (ucp.get("services") or {}).get("dev.ucp.shopping") or []
        rest = next((s for s in svc if isinstance(s, dict) and s.get("transport") == "rest"), None)
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

def main():
    ap = argparse.ArgumentParser(description="Merchant-agnostic UCP conformance (unofficial).")
    ap.add_argument("--server", required=True)
    ap.add_argument("--config", help="optional merchant config JSON (product_id, out_of_stock_id, fail_token, discount_codes, ...)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    config = json.loads(pathlib.Path(args.config).read_text()) if args.config else {}
    ctx, ctype, areas = report(args.server, config)
    supported = sorted(c for c in ctx.capabilities)
    out = {
        "server": ctx.base, "spec_version": ctx.version,
        "content_type_json": "application/json" in ctype.lower(),
        "capabilities": supported,
        "shopping_endpoint": ctx.shopping_endpoint,
        "product_for_lifecycle": ctx.product_id,
        "applicable_areas": {a: v for a, v in areas.items()},
    }
    if args.json:
        print(json.dumps(out, indent=2)); return 0
    print(f"Merchant conformance discovery (UNOFFICIAL) — {ctx.base}\n")
    print(f"  spec version:        {ctx.version}")
    print(f"  JSON content-type:   {out['content_type_json']}")
    print(f"  shopping endpoint:   {ctx.shopping_endpoint}")
    print(f"  declared capabilities: {', '.join(supported) or '(none)'}")
    print(f"  product for lifecycle: {ctx.product_id or '(none found — set --config product_id for data-dependent checks)'}")
    print(f"\n  applicable requirement areas (extensions the server does NOT declare are excluded):")
    for a, v in areas.items():
        print(f"    {'[x]' if v else '[ ]'} {a}" + ("" if v else "  (not-applicable — capability not declared)"))
    print("\n  Next: run the register-driven checks scoped to these areas (report.py). "
          "Unsupported extensions are excluded from the denominator; missing test data -> not-tested.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
