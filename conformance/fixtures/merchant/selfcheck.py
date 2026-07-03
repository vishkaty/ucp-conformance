#!/usr/bin/env python3
"""
selfcheck.py — prove the controlled merchant fixture is spec-conformant, independently.

The fixture is only a trustworthy golden if its profile and responses are valid per the
OFFICIAL schemas — not merely per our own checks. This validates each artifact the
fixture serves against the pinned 2026-04-08 schemas using the ucp-schema oracle.

Exit 0 = every artifact schema-valid; 1 = a deviation (the fixture is buggy, fix it
before it can be a golden); 2 = oracle unavailable (skip).
"""
import sys, json, pathlib, tempfile, os, base64, hashlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "selfcheck"))
import server                                              # noqa: E402
from schema_oracle import validate, validate_against, validate_root, validate_profile, validate_nested_def, OracleUnavailable  # noqa: E402

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
    if server.VERSION == "2026-04-08":
        # totals sub-lines (checkout.md "Sub-Lines"): the 04-08 renderer itemizes the
        # subtotal entry per line item; sum(lines[].amount) MUST equal the parent
        # amount (TOT-017) — the schema-validity of the lines shape itself is proven
        # by the oracle runs below (validate_obj of every lifecycle response).
        with_lines = [t for t in created["totals"] if t.get("lines")]
        if not with_lines:
            raise RuntimeError("04-08 checkout totals carry no sub-lines (TOT-017 scenario)")
        for t in with_lines:
            if sum(l["amount"] for l in t["lines"]) != t["amount"]:
                raise RuntimeError(f"sub-lines do not sum to the parent amount (TOT-017): {t}")
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
    # PAYMENT AREA: profile/response handler declarations + 3DS escalation scenario
    # (PAY-001/002/003/018). Hard behavioral assertions here; the schema anchor is
    # the oracle validations returned below (validate_profile covers the profile
    # registry; every checkout-response validation now covers the ucp envelope's
    # payment_handlers; the escalation response validates as a complete response).
    ph = server.profile(BASE).get("payment_handlers")
    if not (isinstance(ph, dict) and ph.get(server.PAYMENT_HANDLER_KEY)
            and all(h.get("id") for h in ph[server.PAYMENT_HANDLER_KEY])):
        raise RuntimeError(f"profile payment_handlers registry is malformed: {ph}")
    rph = (created.get("ucp") or {}).get("payment_handlers")
    if not (isinstance(rph, dict) and rph.get(server.PAYMENT_HANDLER_KEY)
            and all(h.get("id") for h in rph[server.PAYMENT_HANDLER_KEY])):
        raise RuntimeError(f"response ucp.payment_handlers is malformed: {rph}")
    ok, esc = _expect(201, server.create_checkout({"line_items": li}, HDRS),
                      "escalation create")
    if not ok:
        raise RuntimeError(esc)
    esc_pay = {"payment": {"instruments": [{"id": "instr_esc", "type": "card",
        "credential": {"type": "token", "token": server.ESCALATE_TOKEN}}]}}
    ok, escalated = _expect(200, server.complete_checkout(esc["id"], esc_pay, HDRS),
                            "escalation complete")
    if not ok:
        raise RuntimeError(escalated)
    if escalated.get("status") != "requires_escalation":
        raise RuntimeError(f"escalation token did not escalate: {escalated.get('status')}")
    cu = escalated.get("continue_url")
    if not (isinstance(cu, str) and "://" in cu):
        raise RuntimeError(f"requires_escalation response lacks continue_url: {cu!r}")
    if not any(m.get("severity") == "requires_buyer_input"
               for m in escalated.get("messages") or []):
        raise RuntimeError("escalation response lacks a requires_buyer_input message")
    ok, esc_done = _expect(200, server.complete_checkout(esc["id"], {
        "payment": {"instruments": [{"id": "instr_esc2", "type": "card",
            "credential": {"type": "token", "token": "success_token"}}]}}, HDRS),
        "post-escalation complete")
    if not ok:
        raise RuntimeError(esc_done)
    if esc_done.get("status") != "completed" or "continue_url" in esc_done:
        raise RuntimeError("post-escalation retry did not complete cleanly")
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
    # ORDER area (04-08 only — the hook 404s at 01-23, whose adjustment schema is
    # unsigned): drive the test-only post-order adjustment and assert the pinned
    # semantics IN-PROCESS, then oracle-validate the adjusted order response.
    adjusted = None
    if server.VERSION == "2026-04-08":
        ok, o2c = _expect(201, server.create_checkout(
            {"line_items": [{"id": "li_1", "item": {"id": "teapot_ceramic"}, "quantity": 1},
                            {"id": "li_2", "item": {"id": "mug_enamel"}, "quantity": 2}]},
            HDRS), "adjust-scenario create")
        if not ok:
            raise RuntimeError(o2c)
        ok, o2done = _expect(200, server.complete_checkout(o2c["id"], payment, HDRS),
                             "adjust-scenario complete")
        if not ok:
            raise RuntimeError(o2done)
        oid2 = o2done["order"]["id"]
        ok, adjusted = _expect(200, server.simulate_order_adjustment(
            oid2, {"line_item_id": "li_1", "quantity": 1, "type": "refund"}, HDRS),
            "adjust hook")
        if not ok:
            raise RuntimeError(adjusted)
        lis = {li["id"]: li for li in adjusted["line_items"]}
        if set(lis) != {"li_1", "li_2"}:
            raise RuntimeError("adjusted order dropped a line item that ever existed "
                               f"(ORD-002 retention): {sorted(lis)}")
        if lis["li_1"]["quantity"]["total"] != 0 or lis["li_1"]["status"] != "removed":
            raise RuntimeError(f"fully-refunded line item not rendered removed: {lis['li_1']}")
        adjs = adjusted.get("adjustments") or []
        if not adjs or adjs[0]["line_items"][0]["quantity"] >= 0 \
           or adjs[0]["totals"][0]["amount"] >= 0:
            raise RuntimeError(f"adjustment is not signed negative (reduction): {adjs}")
        ok, refetched = _expect(200, server.get_order(oid2, HDRS), "adjusted order get")
        if not ok or refetched != adjusted:
            raise RuntimeError("adjusted order GET does not match the hook's snapshot")
    out = [
        ("checkout create response",   lambda: validate_obj(created, "create")),
        ("checkout get response",      lambda: validate_obj(got, "read")),
        ("checkout update response",   lambda: validate_obj(updated, "update")),
        ("checkout complete response", lambda: validate_obj(completed, "complete")),
        ("checkout cancel response",   lambda: validate_obj(canceled, "cancel")),
        ("order get response",         lambda: validate_obj(order, "read")),
        ("discounted checkout response", lambda: validate_obj(discounted, "create")),
        ("rejected-code checkout response", lambda: validate_obj(rejected, "create")),
        # PAYMENT AREA: the requires_escalation + continue_url response and the
        # post-escalation completed response must BOTH be schema-valid
        ("escalation checkout response", lambda: validate_obj(escalated, "complete")),
        ("post-escalation complete response", lambda: validate_obj(esc_done, "complete")),
    ]
    if adjusted is not None:
        out.append(("adjusted order response", lambda: validate_obj(adjusted, "read")))
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

def _identity_capability_config():
    """The profile's identity_linking declaration MUST validate against the OFFICIAL
    nested business_schema def (identity_linking.json requires config + config.scopes;
    scope keys match the scope_token pattern). validate_profile alone cannot prove
    this — the profile oracle does not recurse into capability config schemas."""
    entries = server.profile(BASE)["capabilities"].get("dev.ucp.common.identity_linking")
    if not entries:
        return False, "profile does not declare dev.ucp.common.identity_linking"
    for e in entries:
        ok, detail = validate_nested_def(e, "schemas/common/identity_linking.json",
                                         "dev.ucp.common.identity_linking/business_schema",
                                         op="read", version=server.VERSION)
        if not ok:
            return False, detail
    return True, "ok"

def _oauth_metadata():
    """RFC 8414 metadata invariants (no UCP schema exists for this artifact — the
    assertions below are RFC 8414 / identity-linking.md requirements, plus the
    cross-artifact consistency the spec's scope-mismatch story relies on: every
    scope declared in the profile's config.scopes appears in scopes_supported)."""
    md = server.oauth_authorization_server_metadata(BASE)
    for f in ("issuer", "authorization_endpoint", "token_endpoint"):
        if not (isinstance(md.get(f), str) and md[f]):
            return False, f"metadata is missing {f}"
    if md["issuer"] != BASE:
        return False, f"issuer must be the business base URL, got {md['issuer']}"
    if not (isinstance(md.get("response_types_supported"), list)
            and "code" in md["response_types_supported"]):
        return False, "response_types_supported must include 'code'"
    if not (isinstance(md.get("scopes_supported"), list) and md["scopes_supported"]
            and all(isinstance(s, str) and s for s in md["scopes_supported"])):
        return False, "scopes_supported must be a populated list of scope strings (IDL-017)"
    missing = set(server.IDENTITY_SCOPES) - set(md["scopes_supported"])
    if missing:
        return False, f"profile config.scopes not in scopes_supported: {sorted(missing)}"
    if not (isinstance(md.get("token_endpoint_auth_methods_supported"), list)
            and md["token_endpoint_auth_methods_supported"]):
        return False, "token_endpoint_auth_methods_supported must be populated (IDL-022)"
    if md.get("authorization_response_iss_parameter_supported") is not True:
        return False, "authorization_response_iss_parameter_supported must be true (IDL-058)"
    if "S256" not in (md.get("code_challenge_methods_supported") or []):
        return False, "code_challenge_methods_supported must include S256 (IDL-058)"
    if "none" in md["token_endpoint_auth_methods_supported"] \
       and "S256" not in md["code_challenge_methods_supported"]:
        return False, "advertising 'none' requires PKCE S256"
    return True, "ok"

# ---- OAUTH area artifacts (identity-linking.md; RFC 6749/7636/7009/6750/9728) ----
# The OAuth server has no official UCP schema, so its artifacts are RULE-CHECKED
# against the pinned spec text + the RFCs it cites; the UCP error BODIES the gated
# operations emit (identity_required / insufficient_scope messages) and the order
# entities behind the gate ARE oracle-validated against the official schemas.
OAUTH_PUB, OAUTH_CONF = "spck-platform-public", "spck-platform-confidential"
OAUTH_SECRET = "spck-confidential-secret-2026"
OAUTH_RURI = "https://platform.spck.dev/oauth/callback"

def _pkce_pair():
    v = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    return v, base64.urlsafe_b64encode(
        hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()

def _qs(headers):
    import urllib.parse
    loc = headers.get("Location", "")
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(loc).query))

def _authorize(**over):
    v, ch = _pkce_pair()
    p = {"response_type": "code", "client_id": OAUTH_PUB, "redirect_uri": OAUTH_RURI,
         "scope": "dev.ucp.shopping.order:read dev.ucp.shopping.order:manage",
         "code_challenge": ch, "code_challenge_method": "S256", "state": "st1"}
    for k, val in over.items():
        if val is None:
            p.pop(k, None)
        else:
            p[k] = val
    st, hdrs, payload = server.oauth_authorize(p, BASE)
    return st, hdrs, payload, _qs(hdrs), v

def _basic(cid, secret):
    return {"Authorization": "Basic "
            + base64.b64encode(f"{cid}:{secret}".encode()).decode()}

def _token(code, verifier, cid=OAUTH_PUB, ruri=OAUTH_RURI, headers=None):
    form = {"grant_type": "authorization_code", "code": code}
    if ruri is not None:
        form["redirect_uri"] = ruri
    if verifier is not None:
        form["code_verifier"] = verifier
    if headers is None:
        form["client_id"] = cid
    return server.oauth_token(form, headers or {}, BASE)

def _challenge_params(hdrs):
    """Parse 'Bearer k="v", ...' -> dict (RFC 6750 §3)."""
    v = hdrs.get("WWW-Authenticate", "")
    if not v.startswith("Bearer"):
        return None
    out = {}
    for part in v[len("Bearer"):].split(","):
        k, eq, val = part.strip().partition("=")
        if eq:
            out[k.strip()] = val.strip().strip('"')
    return out

def _oauth_code_flow():
    """The authorization-code + PKCE happy path and every token-endpoint rejection
    the 04-08 spec pins (identity-linking.md For Businesses + Security
    Considerations): iss on the authorization response (RFC 9207), PKCE S256
    required / plain rejected (RFC 7636), invalid_scope for out-of-vocabulary
    scopes, exact redirect_uri matching with the loopback port exception
    (RFC 8252 §7.3), invalid_client vs invalid_grant error selection, and
    single-use codes."""
    st, hdrs, _, q, v = _authorize()
    if st != 302 or not hdrs.get("Location", "").startswith(OAUTH_RURI):
        return False, f"authorize happy path did not redirect to redirect_uri ({st})"
    if not q.get("code") or q.get("state") != "st1" or q.get("iss") != BASE:
        return False, f"authorization response must carry code + state echo + iss: {q}"
    st, thdrs, tok = _token(q["code"], v)
    if st != 200 or not tok.get("access_token") or tok.get("token_type") != "Bearer":
        return False, f"token exchange failed: {st} {tok}"
    if tok.get("scope") and "dev.ucp.shopping.order:read" not in tok["scope"].split():
        return False, f"granted scope lost the requested standard UCP scope: {tok}"
    if thdrs.get("Cache-Control") != "no-store":
        return False, "token response must carry Cache-Control: no-store (RFC 6749 §5.1)"
    st, _, err = _token(q["code"], v)                        # code reuse
    if st != 400 or err.get("error") != "invalid_grant":
        return False, f"authorization-code reuse must fail invalid_grant: {st} {err}"
    v2, ch2 = _pkce_pair()
    st, hdrs, _, q2, _ = _authorize(code_challenge=ch2, code_challenge_method="plain")
    if st != 302 or _qs(hdrs).get("error") != "invalid_request" or "code" in q2:
        return False, f"plain PKCE must be rejected invalid_request: {_qs(hdrs)}"
    st, hdrs, _, q2, _ = _authorize(code_challenge=None, code_challenge_method=None)
    if st != 302 or _qs(hdrs).get("error") != "invalid_request" or "code" in q2:
        return False, f"missing code_challenge must be rejected: {_qs(hdrs)}"
    st, hdrs, _, q2, _ = _authorize(scope="dev.ucp.shopping.nope:zap")
    if st != 302 or _qs(hdrs).get("error") != "invalid_scope":
        return False, f"out-of-vocabulary scope must be invalid_scope: {_qs(hdrs)}"
    st, hdrs, payload, _, _ = _authorize(redirect_uri="https://evil.example/cb")
    if st != 400 or "Location" in hdrs:
        return False, "unregistered redirect_uri must be answered directly (400, no redirect)"
    # PKCE at the token endpoint: absent and wrong verifiers are invalid_grant
    st, hdrs, _, q3, v3 = _authorize()
    st, _, err = _token(q3["code"], None)
    if st != 400 or err.get("error") != "invalid_grant":
        return False, f"token without code_verifier must be invalid_grant: {st} {err}"
    st, _, err = _token(q3["code"], v3 + "x")
    if st != 400 or err.get("error") != "invalid_grant":
        return False, f"wrong code_verifier must be invalid_grant: {st} {err}"
    st, hdrs, _, q4, v4 = _authorize()
    st, _, err = _token(q4["code"], v4, ruri=OAUTH_RURI + "/")
    if st != 400 or err.get("error") != "invalid_grant":
        return False, f"redirect_uri mismatch must be invalid_grant: {st} {err}"
    # loopback exception: the port differs between authorize and token — accepted
    st, hdrs, _, q5, v5 = _authorize(redirect_uri="http://127.0.0.1:7777/oauth/cb")
    st, _, tok5 = _token(q5["code"], v5, ruri="http://127.0.0.1:12345/oauth/cb")
    if st != 200 or not tok5.get("access_token"):
        return False, f"loopback port must be ignored (RFC 8252 §7.3): {st} {tok5}"
    # client authentication: wrong secret -> invalid_client 401; right secret -> 200
    st, hdrs, _, q6, v6 = _authorize(client_id=OAUTH_CONF)
    st, ehdrs, err = _token(q6["code"], v6, headers=_basic(OAUTH_CONF, "wrong"))
    if st != 401 or err.get("error") != "invalid_client" \
       or not ehdrs.get("WWW-Authenticate", "").startswith("Basic"):
        return False, f"bad client secret must be 401 invalid_client (+challenge): {st} {err}"
    st, _, tok6 = _token(q6["code"], v6, headers=_basic(OAUTH_CONF, OAUTH_SECRET))
    if st != 200 or not tok6.get("access_token"):
        return False, f"client_secret_basic exchange failed: {st} {tok6}"
    return True, "ok"

def _mint_token(scope):
    st, hdrs, _, q, v = _authorize(scope=scope)
    if st != 302 or not q.get("code"):
        raise RuntimeError(f"authorize failed while minting a token: {st}")
    st, _, tok = _token(q["code"], v)
    if st != 200:
        raise RuntimeError(f"token mint failed: {st} {tok}")
    return tok

def _oauth_gated():
    """The gated-operation challenges (identity-linking.md Error Handling):
    identity_required (401, realm=issuer, error omitted without a token /
    error=invalid_token with a bad one, resource_metadata pointer, non-pre-baked
    continue_url) and insufficient_scope (403, error + FULL required scope set).
    The UCP message bodies are ORACLE-validated (message_error.json); the order
    entities behind the gate are ORACLE-validated (order.json)."""
    st, hdrs, body = server.list_orders({}, BASE)
    if st != 401:
        return False, f"gated op without a token must be 401, got {st}"
    p = _challenge_params(hdrs)
    if p is None:
        return False, "401 must carry a WWW-Authenticate: Bearer challenge"
    if p.get("realm") != BASE:
        return False, f"realm must equal the issuer URI: {p}"
    if "error" in p:
        return False, "error param SHOULD be omitted when no token was presented"
    if p.get("resource_metadata") != BASE + "/.well-known/oauth-protected-resource":
        return False, f"resource_metadata must point at the RFC 9728 document: {p}"
    msgs = body.get("messages") or []
    if not msgs or msgs[0].get("code") != "identity_required":
        return False, f"401 body must carry code=identity_required: {msgs}"
    ok, detail = validate_root(msgs[0], "schemas/shopping/types/message_error.json",
                               op="get", version=server.VERSION)
    if not ok:
        return False, f"identity_required message is not schema-valid: {detail}"
    cu = body.get("continue_url", "")
    import urllib.parse
    cu_qs = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(cu).query))
    if not cu or {"response_type", "client_id", "code_challenge"} & set(cu_qs):
        return False, f"continue_url must be a non-pre-baked onboarding URL: {cu}"
    st, hdrs, _ = server.list_orders({"Authorization": "Bearer bogus_token"}, BASE)
    p = _challenge_params(hdrs)
    if st != 401 or not p or p.get("error") != "invalid_token":
        return False, f"invalid token must yield 401 + error=invalid_token: {st} {p}"
    # a real order behind the gate: drive create -> complete, then read the history
    created = server.create_checkout(
        {"line_items": [{"item": {"id": "teapot_ceramic"}, "quantity": 1}]}, HDRS)[1]
    server.complete_checkout(created["id"], {"payment": {"instruments": [
        {"id": "i1", "type": "card",
         "credential": {"type": "token", "token": "success_token"}}]}}, HDRS)
    read_tok = _mint_token("dev.ucp.shopping.order:read")
    st, _, body = server.list_orders(
        {"Authorization": "Bearer " + read_tok["access_token"]}, BASE)
    if st != 200 or not body.get("orders"):
        return False, f"order:read token must unlock the order history: {st}"
    ok, detail = validate_obj(body["orders"][0], "read")
    if not ok:
        return False, f"gated order entity is not schema-valid: {detail}"
    # insufficient_scope: an order:read-only token on the manage-gated cancel op
    st, hdrs, body = server.cancel_order(
        "ord_x", {"Authorization": "Bearer " + read_tok["access_token"]}, BASE)
    p = _challenge_params(hdrs)
    if st != 403 or not p or p.get("error") != "insufficient_scope":
        return False, f"under-scoped token must yield 403 insufficient_scope: {st} {p}"
    if p.get("realm") != BASE:
        return False, f"insufficient_scope realm must equal the issuer URI: {p}"
    if set((p.get("scope") or "").split()) != set(server.ORDER_MANAGE_SCOPES):
        return False, f"scope param must list the FULL required set: {p}"
    msgs = body.get("messages") or []
    if not msgs or msgs[0].get("code") != "insufficient_scope":
        return False, f"403 body must carry code=insufficient_scope: {msgs}"
    ok, detail = validate_root(msgs[0], "schemas/shopping/types/message_error.json",
                               op="get", version=server.VERSION)
    if not ok:
        return False, f"insufficient_scope message is not schema-valid: {detail}"
    # revocation: revoking the refresh_token invalidates its access tokens too
    st, _, _ = server.oauth_revoke({"token": read_tok["refresh_token"],
                                    "client_id": OAUTH_PUB}, {}, BASE)
    if st != 200:
        return False, f"revocation must return 200: {st}"
    st, hdrs, _ = server.list_orders(
        {"Authorization": "Bearer " + read_tok["access_token"]}, BASE)
    p = _challenge_params(hdrs)
    if st != 401 or not p or p.get("error") != "invalid_token":
        return False, "revoking the refresh_token must invalidate its access tokens"
    st, _, err = server.oauth_token(
        {"grant_type": "refresh_token", "refresh_token": read_tok["refresh_token"],
         "client_id": OAUTH_PUB}, {}, BASE)
    if st != 400 or err.get("error") != "invalid_grant":
        return False, f"a revoked refresh_token must be invalid_grant: {st} {err}"
    st, _, _ = server.oauth_revoke({"token": "never_issued",
                                    "client_id": OAUTH_PUB}, {}, BASE)
    if st != 200:
        return False, f"revoking an unknown token must still be 200 (RFC 7009 §2.2): {st}"
    return True, "ok"

def _oauth_resource_metadata():
    """RFC 9728 protected resource metadata invariants + consistency with the AS."""
    md = server.oauth_protected_resource_metadata(BASE)
    if md.get("resource") != BASE or md.get("authorization_servers") != [BASE]:
        return False, f"resource/authorization_servers must be the business base: {md}"
    if set(md.get("scopes_supported") or []) != set(server.IDENTITY_SCOPES):
        return False, "protected-resource scopes must match the identity scopes"
    return True, "ok"

def _oauth_01era():
    """The 01-era identity-linking business MUSTs (identity-linking.md@2026-01-23,
    textually identical at 2026-01-11): RFC 8414 metadata, Client Authentication
    enforced at the token endpoint (HTTP Basic / client_secret_basic), the standard
    scope ucp:scopes:checkout_session granted, RFC 7009 revocation with the same
    client credentials. Runs while the fixture serves an 01-era version."""
    md = server.oauth_authorization_server_metadata(BASE)
    if md.get("token_endpoint_auth_methods_supported") != ["client_secret_basic"]:
        return False, f"01-era metadata must advertise client_secret_basic: {md}"
    if "code_challenge_methods_supported" in md:
        return False, "01-era metadata predates the PKCE advertisement"
    if list(server.IDENTITY_SCOPES_01ERA)[0] not in md.get("scopes_supported", []):
        return False, f"01-era scopes_supported must carry the standard scope: {md}"
    for f in ("issuer", "authorization_endpoint", "token_endpoint",
              "revocation_endpoint"):
        if not md.get(f):
            return False, f"01-era metadata is missing {f}"
    scope = list(server.IDENTITY_SCOPES_01ERA)[0]
    st, hdrs, _, q, v = _authorize(client_id=OAUTH_CONF, scope=scope,
                                   code_challenge=None, code_challenge_method=None)
    if st != 302 or not q.get("code"):
        return False, f"01-era authorize failed: {st} {_qs(hdrs)}"
    st, _, err = _token(q["code"], None, headers={})    # no client authentication
    if st != 401 or err.get("error") != "invalid_client":
        return False, f"unauthenticated token request must be invalid_client: {st} {err}"
    st, _, err = _token(q["code"], None, headers=_basic(OAUTH_CONF, "wrong"))
    if st != 401 or err.get("error") != "invalid_client":
        return False, f"wrong client secret must be invalid_client: {st} {err}"
    st, _, tok = _token(q["code"], None, headers=_basic(OAUTH_CONF, OAUTH_SECRET))
    if st != 200 or not tok.get("access_token"):
        return False, f"01-era Basic-authenticated exchange failed: {st} {tok}"
    if tok.get("scope") and scope not in tok["scope"].split():
        return False, f"standard UCP scope was not granted: {tok}"
    st, _, err = server.oauth_revoke({"token": tok["refresh_token"]}, {}, BASE)
    if st != 401:
        return False, "01-era revocation must require the same client credentials"
    st, _, _ = server.oauth_revoke({"token": tok["refresh_token"]},
                                   _basic(OAUTH_CONF, OAUTH_SECRET), BASE)
    if st != 200:
        return False, f"authenticated revocation must return 200: {st}"
    st, _, err = server.oauth_token(
        {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
        _basic(OAUTH_CONF, OAUTH_SECRET), BASE)
    if st != 400 or err.get("error") != "invalid_grant":
        return False, f"a revoked 01-era refresh_token must be invalid_grant: {st} {err}"
    return True, "ok"

def _cart_artifact():
    """POST /carts behavior (create_cart): a request WITHOUT the mandatory UCP-Agent
    header MUST be rejected 400 (cart-rest.md — CART-024); a well-formed request
    yields 201 with a cart the official cart.json schema accepts."""
    cart_req = {"line_items": [{"item": {"id": "teapot_ceramic"}, "quantity": 2}]}
    st, _ = server.create_cart(cart_req, {})
    if st != 400:
        return False, f"cart create without UCP-Agent must return 400, got {st}"
    st, cart = server.create_cart(cart_req, HDRS)
    if st != 201:
        return False, f"cart create with UCP-Agent must return 201, got {st}"
    return validate_against(cart, "schemas/shopping/cart.json", "checkout",
                            op="read", version=server.VERSION)

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
# ==== SIGNATURES area artifacts (2026-04-08) ===================================
# The committed platform TEST private scalar (public part is baked into
# server.TRUSTED_PLATFORM_KEYS; the full JWK lives in CONTROLLED_CONFIG).
_PLATFORM_D = int.from_bytes(base64.urlsafe_b64decode(
    "EymkNYgazGbLoD16l-fw7K-C9WNJEIv4hn_RpRgW5xY="), "big")
_PLATFORM_KID = "spck-platform-sig-2026"

def _sig_response_artifacts():
    """The fixture's RFC 9421 response signature over a REAL lifecycle body:
    sha-256 Content-Digest over the raw bytes, parseable headers, @status
    component, no alg parameter, 64-byte raw r||s that verifies against the
    published JWK — and defect injections (tampered sig/body) are rejected."""
    body = json.dumps(server.create_checkout(
        {"line_items": [{"item": {"id": "teapot_ceramic"}, "quantity": 1}]},
        {"UCP-Agent": 'profile="x"'})[1]).encode()
    h = server.sign_response(201, body)
    want = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
    if h["Content-Digest"] != want:
        return False, "Content-Digest is not the sha-256 of the raw body bytes"
    si = server.parse_signature_input(h["Signature-Input"])
    sigs = server.parse_signature(h["Signature"])
    if not si or not sigs or "sig1" not in si or "sig1" not in sigs:
        return False, "signature headers do not parse"
    e = si["sig1"]
    if e["components"] != ["@status", "content-digest", "content-type"]:
        return False, f"unexpected response components: {e['components']}"
    if "alg" in e["params"] or e["params"].get("keyid") != server.SIG_KID:
        return False, f"bad Signature-Input params: {e['params']}"
    if len(sigs["sig1"]) != 64:
        return False, f"signature is not 64-byte raw r||s ({len(sigs['sig1'])} bytes)"
    jwk = server.signing_jwk()
    Q = (int.from_bytes(base64.urlsafe_b64decode(jwk["x"] + "="), "big"),
         int.from_bytes(base64.urlsafe_b64decode(jwk["y"] + "="), "big"))
    if not server.ec_on_curve(Q):
        return False, "published JWK point is not on P-256"
    base = server._sig_base(e["components"], e["raw"], {"@status": "201"},
                            {"content-digest": h["Content-Digest"],
                             "content-type": "application/json"})
    if not server.ecdsa_p256_verify(base, sigs["sig1"], Q):
        return False, "response signature does not verify against the published JWK"
    bad = sigs["sig1"][:-1] + bytes([sigs["sig1"][-1] ^ 1])
    if server.ecdsa_p256_verify(base, bad, Q):
        return False, "tampered signature wrongly verifies"
    return True, "ok"

def _sig_request_verification():
    """server.verify_signed_request round-trip with the committed platform test key:
    valid ES256 accepted; tampered sig -> 401 signature_invalid; wrong body ->
    400 digest_mismatch; unknown kid -> 401 key_not_found (signatures.md codes)."""
    raw = json.dumps({"line_items": [{"item": {"id": "teapot_ceramic"},
                                      "quantity": 1}]}).encode()
    digest = "sha-256=:" + base64.b64encode(hashlib.sha256(raw).digest()).decode() + ":"
    comps = ["@method", "@authority", "@path", "ucp-agent", "idempotency-key",
             "content-digest", "content-type"]
    raw_params = ("(" + " ".join(f'"{c}"' for c in comps) + ")"
                  + f';keyid="{_PLATFORM_KID}"')
    hdrs = {"Host": "localhost:8184", "UCP-Agent": 'profile="https://spck.dev/agent"',
            "Idempotency-Key": "selfcheck-idem-1", "Content-Type": "application/json",
            "Content-Digest": digest}
    values = {"@method": "POST", "@authority": "localhost:8184",
              "@path": "/checkout-sessions", "ucp-agent": hdrs["UCP-Agent"],
              "idempotency-key": hdrs["Idempotency-Key"],
              "content-digest": digest, "content-type": "application/json"}
    base = "\n".join([f'"{c}": {values[c]}' for c in comps]
                     + [f'"@signature-params": {raw_params}']).encode()
    sig = server.ecdsa_p256_sign(base, _PLATFORM_D)
    hdrs["Signature-Input"] = f"sig1={raw_params}"
    hdrs["Signature"] = "sig1=:" + base64.b64encode(sig).decode() + ":"
    if server.verify_signed_request("POST", "/checkout-sessions", hdrs, raw) is not None:
        return False, "valid ES256-signed request was rejected"
    t = dict(hdrs)
    t["Signature"] = ("sig1=:" + base64.b64encode(
        sig[:-1] + bytes([sig[-1] ^ 1])).decode() + ":")
    err = server.verify_signed_request("POST", "/checkout-sessions", t, raw)
    if not err or err[0] != 401 or err[1].get("code") != "signature_invalid":
        return False, f"tampered signature: expected 401 signature_invalid, got {err}"
    err = server.verify_signed_request("POST", "/checkout-sessions", hdrs, raw + b" ")
    if not err or err[0] != 400 or err[1].get("code") != "digest_mismatch":
        return False, f"body/digest mismatch: expected 400 digest_mismatch, got {err}"
    u = dict(hdrs)
    u["Signature-Input"] = "sig1=" + raw_params.replace(_PLATFORM_KID, "ucp-nope")
    err = server.verify_signed_request("POST", "/checkout-sessions", u, raw)
    if not err or err[0] != 401 or err[1].get("code") != "key_not_found":
        return False, f"unknown kid: expected 401 key_not_found, got {err}"
    return True, "ok"

# ---- openssl cross-anchor: the pure-Python ECDSA must interoperate with an
# INDEPENDENT implementation (LibreSSL/OpenSSL), both directions. DER/SPKI
# encoders live here (anchor-only; the fixture itself never emits DER). --------
def _der(tag, content):
    n = len(content)
    if n < 0x80:
        ln = bytes([n])
    else:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        ln = bytes([0x80 | len(b)]) + b
    return bytes([tag]) + ln + content

def _der_int(x):
    b = x.to_bytes((x.bit_length() + 7) // 8 or 1, "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return _der(0x02, b)

def _der_oid(*arcs):
    out = bytes([arcs[0] * 40 + arcs[1]])
    for a in arcs[2:]:
        chunk = [a & 0x7F]; a >>= 7
        while a:
            chunk.append((a & 0x7F) | 0x80); a >>= 7
        out += bytes(reversed(chunk))
    return _der(0x06, out)

def _spki_pem(Q):
    algo = _der(0x30, _der_oid(1, 2, 840, 10045, 2, 1)
                + _der_oid(1, 2, 840, 10045, 3, 1, 7))
    point = b"\x04" + Q[0].to_bytes(32, "big") + Q[1].to_bytes(32, "big")
    b64 = base64.encodebytes(_der(0x30, algo + _der(0x03, b"\x00" + point))).decode()
    return ("-----BEGIN PUBLIC KEY-----\n"
            + "".join(b64.split()) + "\n-----END PUBLIC KEY-----\n")

def _sig_openssl_anchor():
    import shutil, subprocess
    if not shutil.which("openssl"):
        return True, "skipped (no openssl binary on PATH)"
    tmp = tempfile.mkdtemp()
    msg = b"spck ucp signatures openssl anchor"
    mp = os.path.join(tmp, "msg"); open(mp, "wb").write(msg)
    # direction 1: openssl verifies OUR signature
    sig = server.ecdsa_p256_sign(msg, server._SIG_D)
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    sp = os.path.join(tmp, "sig.der")
    open(sp, "wb").write(_der(0x30, _der_int(r) + _der_int(s)))
    pp = os.path.join(tmp, "pub.pem"); open(pp, "w").write(_spki_pem(server._SIG_Q))
    v = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pp,
                        "-signature", sp, mp], capture_output=True, text=True)
    if v.returncode != 0:
        return False, f"openssl rejected our signature: {v.stdout} {v.stderr}"
    # direction 2: WE verify an openssl-produced signature
    kp = os.path.join(tmp, "key.pem")
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout",
                    "-out", kp], check=True, capture_output=True)
    sp2 = os.path.join(tmp, "sig2.der")
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", kp, "-out", sp2, mp],
                   check=True, capture_output=True)
    txt = subprocess.run(["openssl", "ec", "-in", kp, "-text", "-noout"],
                         capture_output=True, text=True).stdout
    hexs = "".join(c for c in txt.split("pub:")[1].split("ASN1")[0]
                   if c in "0123456789abcdef")
    pub = bytes.fromhex(hexs)
    if pub[:1] != b"\x04" or len(pub) != 65:
        return False, f"could not extract the openssl public point ({len(pub)} bytes)"
    Q2 = (int.from_bytes(pub[1:33], "big"), int.from_bytes(pub[33:], "big"))
    der = open(sp2, "rb").read()

    def read_int(i):
        ln = der[i + 1]
        return int.from_bytes(der[i + 2:i + 2 + ln], "big"), i + 2 + ln
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
    r2, i = read_int(i)
    s2, _ = read_int(i)
    raw2 = r2.to_bytes(32, "big") + s2.to_bytes(32, "big")
    if not server.ecdsa_p256_verify(msg, raw2, Q2):
        return False, "our verifier rejected a genuine openssl ECDSA signature"
    if server.ecdsa_p256_verify(msg + b"!", raw2, Q2):
        return False, "our verifier accepted a signature over a DIFFERENT message"
    return True, "ok"
# ==== end SIGNATURES area artifacts ============================================

def main():
    artifacts = [
        ("profile [04-08]", lambda: validate_profile(server.profile(BASE), version=server.VERSION,
                                                     role="business")),
        # SIGNATURES area: the published JWK is validated against the OFFICIAL
        # signing_key def; the signature artifacts are cross-anchored on openssl.
        ("signing_keys JWK (official signing_key def)", lambda: validate_against(
            server.signing_jwk(), "discovery/profile_schema.json", "signing_key",
            op="read", version=server.VERSION)),
        ("response signature (RFC 9421 artifacts)", _sig_response_artifacts),
        ("request verification (signature error codes)", _sig_request_verification),
        ("ECDSA openssl cross-anchor (both directions)", _sig_openssl_anchor),
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
        # identity-linking (04-08): capability declaration + RFC 8414 metadata
        ("identity_linking capability config", _identity_capability_config),
        ("oauth metadata (RFC 8414)", _oauth_metadata),
        # OAUTH area: the live authorization-code+PKCE server, gated resources,
        # and RFC 9728 metadata (rule-checked vs the pinned spec + RFCs; UCP
        # bodies and gated order entities oracle-validated)
        ("oauth code flow (RFC 6749/7636)", _oauth_code_flow),
        ("oauth gated ops (identity challenges)", _oauth_gated),
        ("oauth protected resource (RFC 9728)", _oauth_resource_metadata),
        ("cart response (UCP-Agent enforced)", _cart_artifact),
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
                # OAUTH area: the 01-era identity-linking business MUSTs
                batch.append(("oauth 01-era (Basic auth + RFC 7009)", _oauth_01era))
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
