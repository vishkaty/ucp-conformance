#!/usr/bin/env python3
"""
selfcheck.py — prove the controlled merchant fixture is spec-conformant, independently.

The fixture is only a trustworthy golden if its profile and responses are valid per the
OFFICIAL schemas — not merely per our own checks. This validates each artifact the
fixture serves against the pinned 2026-04-08 schemas using the ucp-schema oracle.

Exit 0 = every artifact schema-valid; 1 = a deviation (the fixture is buggy, fix it
before it can be a golden); 2 = oracle unavailable (skip).
"""
import sys, json, pathlib, tempfile, os
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "selfcheck"))
import server                                              # noqa: E402
from schema_oracle import validate, validate_against, validate_root, validate_profile, OracleUnavailable  # noqa: E402

BASE = "http://localhost:8184"
HDRS = {"UCP-Agent": 'profile="https://spck.dev/agent"'}   # minimal valid headers

def validate_obj(payload, op):
    """Validate an in-memory response object via the oracle's op/direction resolution
    (for ROOT schemas like checkout.json that have no named $def): the payload's own
    ucp.capabilities schema URL selects the schema, --op picks the lifecycle filter."""
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        pathlib.Path(path).write_text(json.dumps(payload))
        return validate(path, op, response=True, version=server.VERSION)
    finally:
        os.unlink(path)

def _expect(status_want, got, name):
    status, payload = got
    if status != status_want:
        return False, f"{name}: expected HTTP {status_want}, got {status} {payload}"
    return True, payload

def checkout_artifacts():
    """Drive the full checkout lifecycle in-process and yield (name, validate_fn) pairs,
    one per lifecycle response the fixture can serve."""
    li = [{"item": {"id": "teapot_ceramic"}, "quantity": 2},
          {"item": {"id": "mug_enamel_v1"}, "quantity": 1}]
    ok, created = _expect(201, server.create_checkout({"line_items": li}, HDRS), "create")
    if not ok:
        raise RuntimeError(created)
    sid = created["id"]
    upd = {"line_items": li[:1]}                 # 04-08: id is ucp_request:omit (CHK-035)
    if server.VERSION != "2026-04-08":
        upd["id"] = sid                          # 01-era: id required on update (CHK-016)
    ok, updated = _expect(200, server.update_checkout(sid, upd, HDRS), "update")
    if not ok:
        raise RuntimeError(updated)
    ok, got = _expect(200, server.get_checkout(sid, HDRS), "get")
    if not ok:
        raise RuntimeError(got)
    payment = {"payment": {"instruments": [{"id": "instr_1", "type": "card",
        "credential": {"type": "token", "token": "success_token"}}]}}
    ok, completed = _expect(200, server.complete_checkout(sid, payment, HDRS), "complete")
    if not ok:
        raise RuntimeError(completed)
    if not (completed.get("order") or {}).get("id"):
        raise RuntimeError("complete response is missing the order confirmation")
    ok, order = _expect(200, server.get_order(completed["order"]["id"], HDRS), "order get")
    if not ok:
        raise RuntimeError(order)
    ok, canceled = _expect(200, server.cancel_checkout(
        server.create_checkout({"line_items": li}, HDRS)[1]["id"], HDRS), "cancel")
    if not ok:
        raise RuntimeError(canceled)
    # one response exercising ALL discount kinds: code-based order-level (10OFF),
    # item-level with allocations (MUGLOVE), and threshold-automatic (subtotal>=5000)
    ok, discounted = _expect(201, server.create_checkout(
        {"line_items": [{"item": {"id": "teapot_ceramic"}, "quantity": 2},
                        {"item": {"id": "mug_enamel"}, "quantity": 2}],
         "discounts": {"codes": ["10OFF", "MUGLOVE"]}}, HDRS), "discounted create")
    if not ok:
        raise RuntimeError(discounted)
    ap = (discounted.get("discounts") or {}).get("applied") or []
    if not any(a.get("automatic") is True and "code" not in a for a in ap):
        raise RuntimeError("discounted create did not surface the automatic discount")
    if not any(a.get("allocations") for a in ap):
        raise RuntimeError("discounted create did not surface item-level allocations")
    # rejected-code semantics (discount.md): invalid code echoed in codes[], absent
    # from applied[], and communicated via a warning in messages[]
    ok, rejected = _expect(201, server.create_checkout(
        {"line_items": [{"item": {"id": "teapot_ceramic"}, "quantity": 1}],
         "discounts": {"codes": ["10OFF", "NOPE_NOT_A_CODE"]}}, HDRS), "rejected-code create")
    if not ok:
        raise RuntimeError(rejected)
    rd = rejected.get("discounts") or {}
    if "NOPE_NOT_A_CODE" not in (rd.get("codes") or []):
        raise RuntimeError("rejected code was not echoed in discounts.codes")
    if any(a.get("code") == "NOPE_NOT_A_CODE" for a in rd.get("applied") or []):
        raise RuntimeError("rejected code wrongly appears in discounts.applied")
    if not any("NOPE_NOT_A_CODE" in (m.get("content") or "")
               for m in rejected.get("messages") or []):
        raise RuntimeError("rejected code was not communicated via messages[]")
    out = [
        ("checkout create response",   lambda: validate_obj(created, "create")),
        ("checkout get response",      lambda: validate_obj(got, "read")),
        ("checkout update response",   lambda: validate_obj(updated, "update")),
        ("checkout complete response", lambda: validate_obj(completed, "complete")),
        ("checkout cancel response",   lambda: validate_obj(canceled, "cancel")),
        ("order get response",         lambda: validate_obj(order, "read")),
        ("discounted checkout response", lambda: validate_obj(discounted, "create")),
        ("rejected-code checkout response", lambda: validate_obj(rejected, "create")),
    ]
    if server.VERSION != "2026-04-08":
        # pre-04-08 extension schemas can't be COMPOSED by the oracle (their extension
        # def is named e.g. 'checkout', not the capability name), so the extension
        # subtrees are anchored directly to their official $defs instead.
        out.append(("discounts subtree (discounts_object)", lambda: validate_against(
            discounted["discounts"], "schemas/shopping/discount.json",
            "discounts_object", op="read", version=server.VERSION)))
        out.append(("ap2 subtree (merchant_authorization)", lambda: validate_against(
            created["ap2"], "schemas/shopping/ap2_mandate.json",
            "ap2_with_merchant_authorization", op="read", version=server.VERSION)))
    return out

def _getproduct_configurable():
    """get_product by PRODUCT id on the configurable product: product.selected MUST be
    present (configurable options), variants narrowed to the effective selection."""
    status, resp = server.get_product_response({"id": "teacup_glaze",
                                                "selected": [{"name": "Color", "label": "Red"}]})
    if status != 200:
        return False, f"get_product returned HTTP {status}"
    prod = resp.get("product") or {}
    sel = prod.get("selected")
    if not sel or not any(s.get("name") == "Color" and s.get("label") == "Red" for s in sel):
        return False, f"product.selected missing/ignored the request selection: {sel}"
    if not prod.get("variants") or any(
            {"name": "Color", "label": "Red"} not in v.get("options", [])
            for v in prod["variants"]):
        return False, "variants were not narrowed to the Color=Red selection"
    return validate_against(resp, "schemas/shopping/catalog_lookup.json",
                            "get_product_response", op="get_product",
                            version=server.VERSION)

def _getproduct_by_variant():
    """get_product by VARIANT id: the requested variant MUST be first (featured) and
    product.selected reflects that variant's options."""
    status, resp = server.get_product_response({"id": "teacup_glaze_red_s"})
    if status != 200:
        return False, f"get_product returned HTTP {status}"
    prod = resp.get("product") or {}
    variants = prod.get("variants") or []
    if not variants or variants[0].get("id") != "teacup_glaze_red_s":
        return False, f"requested variant is not first: {[v.get('id') for v in variants]}"
    if {"name": "Color", "label": "Red"} not in (prod.get("selected") or []):
        return False, f"selected does not reflect the variant options: {prod.get('selected')}"
    return validate_against(resp, "schemas/shopping/catalog_lookup.json",
                            "get_product_response", op="get_product",
                            version=server.VERSION)

def _getproduct_not_found():
    """Unknown id: HTTP 200 application error with ucp.status=error, unrecoverable."""
    status, resp = server.get_product_response({"id": "ucp_no_such_product"})
    if status != 200:
        return False, f"not-found must be HTTP 200 (application outcome), got {status}"
    if (resp.get("ucp") or {}).get("status") != "error":
        return False, "not-found response is missing ucp.status=error"
    if not any(m.get("severity") == "unrecoverable" for m in resp.get("messages", [])):
        return False, "not-found message must carry severity=unrecoverable (rest.md)"
    return validate_root(resp, "schemas/shopping/types/error_response.json",
                         op="get_product", version=server.VERSION)

def _pagination_walk():
    """Cursor pagination: match-all search pages at the default limit 10, the cursor
    resumes exactly where page 1 ended, and the final page closes the stream."""
    total = len(server.PRODUCTS)
    if total <= 10:
        return False, f"seed catalog must exceed the default page size, has {total}"
    p1 = server.search_response("*")
    pag1 = p1.get("pagination") or {}
    if len(p1.get("products", [])) != 10 or pag1.get("has_next_page") is not True \
       or not pag1.get("cursor"):
        return False, f"page 1 wrong: {len(p1.get('products', []))} items, {pag1}"
    p2 = server.search_response("*", cursor=pag1["cursor"])
    pag2 = p2.get("pagination") or {}
    if len(p2.get("products", [])) != total - 10 or pag2.get("has_next_page") is not False:
        return False, f"page 2 wrong: {len(p2.get('products', []))} items, {pag2}"
    ids = [p["id"] for p in p1["products"]] + [p["id"] for p in p2["products"]]
    if len(ids) != len(set(ids)) or len(ids) != total:
        return False, "pages overlap or drop products"
    for resp in (p1, p2):
        ok, detail = validate_against(resp, "schemas/shopping/catalog_search.json",
                                      "search_response", op="search",
                                      version=server.VERSION)
        if not ok:
            return ok, detail
    return True, "ok"

def _dedup_lookup():
    """lookup.md dedup MUSTs: a batch with a duplicate product id AND that product's
    variant id must return the product exactly ONCE — and still be schema-valid."""
    resp = server.lookup_response(["teapot_ceramic", "teapot_ceramic", "teapot_ceramic_v1"])
    if len(resp.get("products", [])) != 1:
        return False, f"dedup lookup returned {len(resp.get('products', []))} products, want 1"
    return validate_against(resp, "schemas/shopping/catalog_lookup.json",
                            "lookup_response", op="lookup", version=server.VERSION)

# ---- discovery/negotiation-area artifacts (2026-04-08) --------------------------
def _profile_cache_control():
    """DISC-003 hosting policy: Cache-Control has `public` + max-age >= 60 and none
    of private/no-store/no-cache. Headers are not schema territory, so this is a
    rule-check against the pinned spec text (overview.md #L1055-L1057)."""
    directives = [d.strip().lower() for d in server.PROFILE_CACHE_CONTROL.split(",")]
    if "public" not in directives:
        return False, f"missing `public` in {server.PROFILE_CACHE_CONTROL!r}"
    for bad in ("private", "no-store", "no-cache"):
        if bad in directives:
            return False, f"forbidden directive {bad!r} in {server.PROFILE_CACHE_CONTROL!r}"
    ages = [d for d in directives if d.startswith("max-age=")]
    if not ages or int(ages[0].split("=", 1)[1]) < 60:
        return False, f"max-age missing or < 60 in {server.PROFILE_CACHE_CONTROL!r}"
    return True, "ok"

def _neg_flat(url, want_status, want_code):
    """Discovery/version failures are TRANSPORT errors: flat {code, content[,
    continue_url]} at the mapped HTTP status (overview.md Transport Bindings).
    No official schema exists for that body, so the shape is rule-checked against
    the pinned examples; the status/code mapping is the register row's MUST."""
    got = server.negotiate_platform(f'profile="{url}"')
    if not got:
        return False, f"negotiate_platform did not fail for {url}"
    status, payload = got
    if status != want_status:
        return False, f"expected HTTP {want_status}, got {status}"
    if payload.get("code") != want_code:
        return False, f"expected code {want_code!r}, got {payload.get('code')!r}"
    if not isinstance(payload.get("content"), str) or not payload["content"]:
        return False, "transport error body must carry a human-readable `content`"
    return True, "ok"

def _neg_caps_incompatible():
    """NEG-002: empty capability intersection -> HTTP 200 with the error in the UCP
    body (error_response envelope, ucp.status=error) — oracle-validated."""
    got = server.negotiate_platform(f'profile="{server.SIM_NO_COMMON_CAPS}"')
    if not got or got[0] != 200:
        return False, f"expected HTTP 200 (error in UCP body), got {got and got[0]}"
    payload = got[1]
    if (payload.get("ucp") or {}).get("status") != "error":
        return False, "capabilities_incompatible response is missing ucp.status=error"
    if not any(m.get("code") == "capabilities_incompatible"
               for m in payload.get("messages", [])):
        return False, "messages[] is missing code=capabilities_incompatible"
    return validate_root(payload, "schemas/shopping/types/error_response.json",
                         op="create", version=server.VERSION)

def _neg_compatible_default():
    """The default platform profile (spck.dev/agent, used by every existing check)
    must keep negotiating cleanly — the simulation only fires on seeded URLs."""
    if server.negotiate_platform('profile="https://spck.dev/agent"') is not None:
        return False, "default platform profile unexpectedly failed negotiation"
    st, _ = server.create_checkout(
        {"line_items": [{"item": {"id": "teapot_ceramic"}, "quantity": 1}]},
        {"UCP-Agent": 'profile="https://spck.dev/agent"'})
    return (st == 201), f"create with the default profile returned HTTP {st}"

def main():
    artifacts = [
        ("profile [04-08]", lambda: validate_profile(server.profile(BASE), version=server.VERSION,
                                                     role="business")),
        ("catalog.search response", lambda: validate_against(
            server.search_response("*"), "schemas/shopping/catalog_search.json",
            "search_response", op="search", version=server.VERSION)),
        ("catalog.lookup response", lambda: validate_against(
            server.lookup_response(["teapot_ceramic"]), "schemas/shopping/catalog_lookup.json",
            "lookup_response", op="lookup", version=server.VERSION)),
        ("catalog.lookup dedup response", _dedup_lookup),
        ("catalog.get_product configurable", _getproduct_configurable),
        ("catalog.get_product by variant", _getproduct_by_variant),
        ("catalog.get_product not-found", _getproduct_not_found),
        ("catalog.search pagination walk", _pagination_walk),
        ("catalog search rejection (error_response)", lambda: validate_root(
            server.catalog_error("dev.ucp.shopping.catalog.search", "invalid_request",
                                 "search requires at least one input"),
            "schemas/shopping/types/error_response.json", op="search",
            version=server.VERSION)),
        ("catalog batch-cap rejection (error_response)", lambda: validate_root(
            server.catalog_error("dev.ucp.shopping.catalog.lookup", "request_too_large",
                                 "lookup batch exceeds the maximum"),
            "schemas/shopping/types/error_response.json", op="lookup",
            version=server.VERSION)),
        ("cart response", lambda: validate_against(
            server.cart_response({"line_items": [{"item": {"id": "teapot_ceramic"}, "quantity": 2}]}),
            "schemas/shopping/cart.json", "checkout", op="read", version=server.VERSION)),
        # discovery/negotiation area (04-08): profile hosting policy + the simulated
        # negotiation failures (seeded platform-profile URLs; see server.py)
        ("profile Cache-Control policy", _profile_cache_control),
        ("negotiation invalid_profile_url (http)", lambda: _neg_flat(
            "http://spck.dev/agent", 400, "invalid_profile_url")),
        ("negotiation profile_unreachable (424)", lambda: _neg_flat(
            server.SIM_UNREACHABLE, 424, "profile_unreachable")),
        ("negotiation profile_malformed (422)", lambda: _neg_flat(
            server.SIM_MALFORMED, 422, "profile_malformed")),
        ("negotiation version_unsupported (422)", lambda: _neg_flat(
            server.SIM_LEGACY_VERSION, 422, "version_unsupported")),
        ("negotiation capabilities_incompatible", _neg_caps_incompatible),
        ("negotiation default profile still clean", _neg_compatible_default),
        # the MCP transport must return the SAME schema-valid payload in structuredContent
        ("mcp search_catalog result", lambda: validate_against(
            server.mcp_dispatch({"id": 1, "method": "tools/call", "params": {
                "name": "search_catalog",
                "arguments": {"meta": {"ucp-agent": {"profile": "x"}}, "catalog": {"query": "*"}}}}
            )["result"]["structuredContent"],
            "schemas/shopping/catalog_search.json", "search_response", op="search",
            version=server.VERSION)),
    ]
    try:
        # NOTE: everything is evaluated while its version is ACTIVE (validate_obj and
        # the catalog validators read server.VERSION at call time). The checkout/order/
        # discount lifecycle must be spec-conformant in EVERY version the fixture can
        # serve — each validates against ITS pinned schemas (sign conventions and
        # line-item discount shapes differ across versions).
        rows = [(name, *fn()) for name, fn in artifacts]          # 04-08 catalog/cart/mcp
        for ver in server.SUPPORTED_VERSIONS:
            server.set_version(ver)
            tag = f" [{ver[5:]}]"
            batch = list(checkout_artifacts())
            if ver != "2026-04-08":
                batch.insert(0, ("profile", lambda: validate_profile(
                    server.profile(BASE), version=server.VERSION, role="business")))
            rows += [(name + tag, *fn()) for name, fn in batch]
    except OracleUnavailable as e:
        print(f"oracle unavailable: {e}", file=sys.stderr); return 2
    except RuntimeError as e:
        print(f"  ✗ lifecycle drive failed: {e}")
        print("\nfixture self-check: FAIL — fix the fixture before using it as a golden")
        return 1
    finally:
        server.set_version("2026-04-08")    # restore the default serving version

    ok = True
    for name, valid, detail in rows:
        print(f"  {'✓' if valid else '✗'} {name:36} {'schema-valid' if valid else 'INVALID'}")
        if not valid:
            ok = False
            for line in detail.splitlines()[:4]:
                print(f"      {line}")
    print("\nfixture self-check:", "PASS — every artifact is spec-conformant" if ok
          else "FAIL — fix the fixture before using it as a golden")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
