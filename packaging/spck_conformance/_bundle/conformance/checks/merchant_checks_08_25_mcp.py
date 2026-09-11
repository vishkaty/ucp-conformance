#!/usr/bin/env python3
"""
merchant_checks_08_25_mcp.py — the MCP transport's MERCHANT half at 2026-08-25
(D3-09 rows / D3-10 harness; PLAN-v3 §2.15).

Every check drives a real JSON-RPC `tools/call` (or `tools/list`) at the merchant's
advertised MCP endpoint through checks/mcp_client.py and judges the JSON-RPC ENVELOPE
the way the spec pins it: UCP payloads in `result.structuredContent` (+ `content[]`
text that parses to the same document), business outcomes as `result` envelopes,
protocol errors as JSON-RPC errors (-32602 invalid params, -32001 negotiation, -32000
protocol) under the corresponding HTTP status (OVR-060). The catalog/location MCP tools
(MCP-001/006/009) stay with their capabilities: a merchant that does not advertise
them is not graded on them.

Register ids graded (2026-08-25): MCP-003, MCP-004, MCP-005, MCP-016 (business half),
OVR-060, NEG-001 (MCP column), REPLAY-001 (MCP column). The client-side MCP rows --
MCP-002, MCP-007, MCP-008, MCP-010, MCP-011, MCP-012, MCP-013, MCP-014, MCP-015 (what
the PLATFORM must put in a request / must check in a response) -- belong to the agent
lane (D3-11..D3-15, "D9"); the business-side validation those rows imply (meta
required, `id` never inside the payload) is graded here under MCP-003/MCP-016
conformance item 5 ("Validate tool inputs against UCP schemas").

Transport-scoped (`transport="mcp"`): every check is not-applicable on a merchant that
advertises no MCP binding. Version-scoped to 2026-08-25 (the 2026-04-08 MCP checks live
in merchant_checks.py). Fixed 11 checks; the kill sets are locked by killset_lock.json.
"""
import json, pathlib, sys, uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, fetch, CLEAN, DEVIATION                       # noqa: E402
from merchant_checks import MCheck                                      # noqa: E402
from mcp_client import McpClient, meta as _mcp_meta                     # noqa: E402
from wire_shapes import shapes_for, AGENT_PROFILE                       # noqa: E402

VERSIONS = ("2026-08-25",)
V0825 = VERSIONS

CORE_CHECKOUT_TOOLS = ("create_checkout", "get_checkout", "update_checkout",
                       "complete_checkout", "cancel_checkout")

INVALID_PARAMS, PROTOCOL_ERROR, NEGOTIATION_ERROR = -32602, -32000, -32001


# ---- request building ------------------------------------------------------------
def _client(ctx):
    return McpClient(ctx.mcp_endpoint)


def _meta(**extra):
    return _mcp_meta(AGENT_PROFILE, **extra)


def _checkout_payload(ctx, **overrides):
    """The version's REST create body (wire_shapes) minus `id`: over MCP the payload
    object MUST NOT carry an id (checkout/mcp.md#L122-L123, MCP-002)."""
    p = shapes_for(ctx.version).checkout_create(ctx)
    p.pop("id", None)
    p.update(overrides)
    return p


def _create_args(ctx, **meta_extra):
    return {"meta": _meta(**meta_extra), "checkout": _checkout_payload(ctx)}


def _call(ctx, name, arguments, headers=None):
    """One tools/call; the Resp carries the HTTP status and the JSON-RPC document."""
    return _client(ctx).call(name, arguments, headers=headers).as_resp()


def _result(r):
    """The JSON-RPC result object, or None (HTTP != 200, not a 2.0 doc, error)."""
    j = r.json
    if r.status != 200 or not isinstance(j, dict) or j.get("jsonrpc") != "2.0" or "error" in j:
        return None
    return j.get("result") if isinstance(j.get("result"), dict) else None


def _error(r):
    """The JSON-RPC error object with an int code, or None (a result, or malformed)."""
    j = r.json
    if not isinstance(j, dict) or j.get("jsonrpc") != "2.0" or "result" in j:
        return None
    err = j.get("error")
    return err if isinstance(err, dict) and isinstance(err.get("code"), int) else None


def _structured(r):
    res = _result(r)
    sc = res.get("structuredContent") if res else None
    return sc if isinstance(sc, dict) else None


# ---- fetches ---------------------------------------------------------------------
def create_raw(ctx):
    return _call(ctx, "create_checkout", _create_args(ctx))


def out_of_stock_raw(ctx):
    """A create for the merchant's seeded out-of-stock product (config
    out_of_stock_id) -- a BUSINESS outcome, never a protocol failure."""
    payload = _checkout_payload(ctx)
    payload["line_items"] = [{"id": "li_1", "quantity": 1,
                              "item": {"id": ctx.config["out_of_stock_id"], "price": 1000},
                              "totals": []}]
    return _call(ctx, "create_checkout", {"meta": _meta(), "checkout": payload})


def no_meta_raw(ctx):
    return _call(ctx, "create_checkout", {"checkout": _checkout_payload(ctx)})


def id_in_payload_raw(ctx):
    """A create whose checkout object carries an `id` (the MCP-002 mistake)."""
    return _call(ctx, "create_checkout",
                 {"meta": _meta(), "checkout": _checkout_payload(ctx, id=str(uuid.uuid4()))})


def tools_list_raw(ctx):
    return _client(ctx).tools_list().as_resp()


def live_profile_resp(ctx):
    """The profile as served NOW (not the one captured at discovery): the battery's
    discovery mutants must be visible to this check."""
    r = fetch(ctx.base, "/.well-known/ucp")
    if r.status != 200 or not isinstance(r.json, dict):
        return r
    doc = r.json.get("ucp", r.json)
    return Resp(r.status, r.headers, json.dumps(doc).encode())


def unsupported_version_raw(ctx):
    """A create whose HTTP-level UCP-Agent (the header the MCP signed-request example
    carries) names a version no business advertises."""
    return _call(ctx, "create_checkout", _create_args(ctx),
                 headers={"UCP-Agent": f'profile="{AGENT_PROFILE}"; version="2099-01-01"'})


def cart_create_raw(ctx):
    return _call(ctx, "create_cart", {"meta": _meta(), "cart": {
        "line_items": [{"item": {"id": ctx.product_id}, "quantity": 2}],
        "currency": ctx.config.get("currency", "USD")}})


def get_order_raw(ctx):
    """create_checkout -> complete_checkout (both over MCP, the completion with the
    idempotency-key meta the OpenRPC requires) -> get_order of the placed order.
    The returned Resp is the get_order envelope."""
    c = _client(ctx)
    # a completable session: fulfillment requested and the config option selected
    # (the REST checks' _create_for_complete), minus the id the MCP payload may not carry
    payload = shapes_for(ctx.version).checkout_create(ctx, with_fulfillment=True)
    payload.pop("id", None)
    opt = ctx.config.get("fulfillment_option_id")
    if opt:
        payload["fulfillment"]["methods"][0]["groups"][0]["selected_option_id"] = opt
    created = c.call("create_checkout", {"meta": _meta(), "checkout": payload})
    cid = ((created.result or {}).get("structuredContent") or {}).get("id")
    if not cid:
        return created.as_resp()
    completed = c.call("complete_checkout", {
        "meta": _meta(**{"idempotency-key": str(uuid.uuid4())}),
        "id": cid, "checkout": ctx.config.get("complete_payment")})
    order_id = (((completed.result or {}).get("structuredContent") or {}).get("order") or {}).get("id")
    if not order_id:
        return Resp(0, {}, json.dumps({"probe": "complete_checkout over MCP placed no order",
                                       "observed": completed.doc}).encode())
    ctx._mcp_placed_order_id = order_id      # read by p_get_order (ctx-aware predicate)
    return c.call("get_order", {"meta": _meta(), "id": order_id}).as_resp()


def idempotency_raw(ctx):
    """Three creates under ONE meta idempotency-key: the second (same payload) must
    replay the first; the third (different payload) is the conflict under test.
    Returns the third response, annotated with whether the replay matched."""
    c = _client(ctx)
    key = str(uuid.uuid4())
    args = _create_args(ctx, **{"idempotency-key": key})
    first = c.call("create_checkout", args)
    second = c.call("create_checkout", args)
    first_id = ((first.result or {}).get("structuredContent") or {}).get("id")
    second_id = ((second.result or {}).get("structuredContent") or {}).get("id")
    if not first_id or first_id != second_id:
        return Resp(0, {}, json.dumps({"probe": "same key + same payload did not replay the cached "
                                                "checkout", "first": first.doc, "second": second.doc}).encode())
    mismatched = json.loads(json.dumps(args))
    mismatched["checkout"]["line_items"][0]["quantity"] = 2
    return c.call("create_checkout", mismatched).as_resp()


# ---- predicates ------------------------------------------------------------------
def p_structured_content(r):
    """MCP-003/004: a create via tools/call is a JSON-RPC result whose structuredContent
    IS the checkout (ucp + id + status); when content[] is present its text part
    parses to the same document."""
    res = _result(r)
    if res is None:
        return DEVIATION
    sc = res.get("structuredContent")
    if not isinstance(sc, dict) or not isinstance(sc.get("ucp"), dict) \
            or not sc.get("id") or not sc.get("status"):
        return DEVIATION
    content = res.get("content")
    if content is not None:
        if not isinstance(content, list) or not content:
            return DEVIATION
        texts = [c for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if not texts:
            return DEVIATION
        try:
            if json.loads(texts[0].get("text", "")) != sc:
                return DEVIATION
        except (TypeError, ValueError):
            return DEVIATION
    return CLEAN


def p_business_outcome(r):
    """MCP-003 item 4: a business outcome (out of stock) is a `result` whose
    structuredContent is the UCP error envelope with messages[] -- never a JSON-RPC
    error."""
    sc = _structured(r)
    if sc is None:
        return DEVIATION
    ucp, messages = sc.get("ucp"), sc.get("messages")
    if not isinstance(ucp, dict) or ucp.get("status") != "error":
        return DEVIATION
    if not isinstance(messages, list) or not messages \
            or not all(isinstance(m, dict) and m.get("code") for m in messages):
        return DEVIATION
    return CLEAN


def p_invalid_params(r):
    """A schema-invalid tool input (no meta / an `id` inside the payload) is rejected
    as JSON-RPC -32602 (invalid params), with no masquerading result."""
    err = _error(r)
    return CLEAN if err is not None and err["code"] == INVALID_PARAMS else DEVIATION


def p_tools_list_core(r):
    """MCP-003 item 2 / MCP-004: tools/list names every core checkout tool."""
    res = _result(r)
    tools = res.get("tools") if res else None
    if not isinstance(tools, list):
        return DEVIATION
    names = {t.get("name") for t in tools if isinstance(t, dict)}
    return CLEAN if all(t in names for t in CORE_CHECKOUT_TOOLS) else DEVIATION


def p_negotiation_32001(r):
    """NEG-001 (MCP column): version_unsupported is JSON-RPC -32001."""
    err = _error(r)
    return CLEAN if err is not None and err["code"] == NEGOTIATION_ERROR else DEVIATION


def p_error_http_status(r):
    """OVR-060: the JSON-RPC negotiation error is served under the REST status that
    corresponds to it (version_unsupported -> 422), never a blanket 200."""
    err = _error(r)
    if err is None or err["code"] != NEGOTIATION_ERROR:
        return DEVIATION
    return CLEAN if r.status == 422 else DEVIATION


def p_mcp_advertised(r):
    """The served profile advertises the MCP binding: a services[] entry with
    transport "mcp" and an endpoint."""
    j = r.json if isinstance(r.json, dict) else {}
    svc = (j.get("services") or {}).get("dev.ucp.shopping")
    if not isinstance(svc, list):
        return DEVIATION
    mcp = [s for s in svc if isinstance(s, dict) and s.get("transport") == "mcp"]
    return CLEAN if mcp and isinstance(mcp[0].get("endpoint"), str) and mcp[0]["endpoint"] else DEVIATION


def p_cart_result(r):
    """MCP-016 (items 1/2/4): create_cart via tools/call is a result whose
    structuredContent is the cart (ucp, id, currency, line_items, totals)."""
    sc = _structured(r)
    if sc is None:
        return DEVIATION
    if not (isinstance(sc.get("ucp"), dict) and sc.get("id") and sc.get("currency")
            and isinstance(sc.get("line_items"), list) and isinstance(sc.get("totals"), list)):
        return DEVIATION
    return CLEAN


def p_get_order(r, ctx):
    """MCP-005: get_order via tools/call returns the placed order (structuredContent
    with ucp, THE order id that was placed, line_items)."""
    sc = _structured(r)
    if sc is None or not isinstance(sc.get("ucp"), dict) or not sc.get("id"):
        return DEVIATION
    if sc.get("id") != getattr(ctx, "_mcp_placed_order_id", None):
        return DEVIATION
    return CLEAN if isinstance(sc.get("line_items"), list) else DEVIATION


def p_idempotency_conflict(r):
    """REPLAY-001 (MCP column): a duplicate idempotency key with a MISMATCHED payload
    is rejected with JSON-RPC -32000 (the replay itself was proven by the fetch)."""
    err = _error(r)
    return CLEAN if err is not None and err["code"] == PROTOCOL_ERROR else DEVIATION


# ---- the check set ----------------------------------------------------------------
_CHK = "dev.ucp.shopping.checkout"
CHECKS_MCP = [
    MCheck("mcp.checkout_structured_content", ["MCP-003", "MCP-004"], "MUST",
           create_raw, p_structured_content,
           ["drop:result.structuredContent", "drop:result", "set:result.structuredContent={}",
            'set:result.content=[{"type":"text","text":"{}"}]',
            'set:error={"code":-32000,"message":"x"}', "status:500", "corrupt-json"],
           capability=_CHK, needs=("product",), transport="mcp", versions=V0825),
    MCheck("mcp.business_outcome_as_result", ["MCP-003"], "MUST",
           out_of_stock_raw, p_business_outcome,
           ['set:error={"code":-32000,"message":"out of stock"}', "drop:result",
            'set:result.structuredContent.ucp.status="success"',
            "set:result.structuredContent.messages=[]", "status:400", "corrupt-json"],
           capability=_CHK, needs=("product",), cfg_needs=("out_of_stock_id",),
           transport="mcp", versions=V0825),
    MCheck("mcp.meta_required_32602", ["MCP-003"], "MUST",
           no_meta_raw, p_invalid_params,
           ["drop:error", "set:error.code=-32000", 'set:result={"structuredContent":{}}',
            "corrupt-json"],
           capability=_CHK, needs=("product",), transport="mcp", versions=V0825),
    MCheck("mcp.id_in_payload_rejected", ["MCP-003"], "MUST",
           id_in_payload_raw, p_invalid_params,
           ["drop:error", "set:error.code=-32000", 'set:result={"structuredContent":{}}',
            "corrupt-json"],
           capability=_CHK, needs=("product",), transport="mcp", versions=V0825),
    MCheck("mcp.tools_list_core_checkout", ["MCP-003", "MCP-004"], "MUST",
           tools_list_raw, p_tools_list_core,
           ["set:result.tools=[]", "drop:result",
            'set:result.tools=[{"name":"create_checkout"},{"name":"get_checkout"},'
            '{"name":"update_checkout"},{"name":"complete_checkout"}]',
            "corrupt-json"],
           capability=_CHK, transport="mcp", versions=V0825),
    MCheck("mcp.negotiation_error_32001", ["NEG-001"], "MUST",
           unsupported_version_raw, p_negotiation_32001,
           ["set:error.code=-32000", "drop:error", 'set:result={"structuredContent":{}}',
            "corrupt-json"],
           capability=_CHK, needs=("product",), transport="mcp", versions=V0825),
    MCheck("mcp.error_http_status", ["OVR-060"], "MUST",
           unsupported_version_raw, p_error_http_status,
           ["status:200", "status:500", "drop:error"],
           capability=_CHK, needs=("product",), transport="mcp", versions=V0825),
    MCheck("mcp.cart_via_tools_call", ["MCP-016"], "MUST",
           cart_create_raw, p_cart_result,
           ["drop:result.structuredContent", "drop:result", "drop:result.structuredContent.id",
            "drop:result.structuredContent.line_items",
            'set:error={"code":-32000,"message":"x"}', "status:500", "corrupt-json"],
           capability="dev.ucp.shopping.cart", needs=("product",), transport="mcp",
           versions=V0825),
    MCheck("mcp.get_order_tool_0825", ["MCP-005"], "MUST",
           get_order_raw, p_get_order,
           ["drop:result.structuredContent", "drop:result", 'set:result.structuredContent.id="other"',
            "drop:result.structuredContent.line_items",
            'set:error={"code":-32000,"message":"x"}', "status:500", "corrupt-json"],
           capability="dev.ucp.shopping.order", needs=("product",),
           cfg_needs=("complete_payment",), transport="mcp", versions=V0825),
    MCheck("mcp.idempotency_conflict_32000", ["REPLAY-001"], "MUST NOT",
           idempotency_raw, p_idempotency_conflict,
           ["drop:error", "set:error.code=-32001", 'set:result={"structuredContent":{}}',
            "corrupt-json"],
           capability=_CHK, needs=("product",), transport="mcp", versions=V0825),
    # Unattributed: no register row obliges a business to OFFER MCP; this check pins
    # that the golden's served profile advertises the binding it serves (kill:
    # defects_config mutant mcp_transport_not_advertised).
    MCheck("discovery.mcp_transport_advertised", [], "MUST",
           live_profile_resp, p_mcp_advertised,
           ["drop:services", "set:services={}",
            'set:services={"dev.ucp.shopping":[{"transport":"rest","endpoint":"https://m.example"}]}',
            "corrupt-json"],
           transport="mcp", versions=V0825),
]
