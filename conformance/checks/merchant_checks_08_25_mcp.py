#!/usr/bin/env python3
"""
merchant_checks_08_25_mcp.py — the MCP transport's MERCHANT half at 2026-08-25
(D3-09 rows / D3-10 harness; PLAN-v3 §2.15).

Every check here drives a real JSON-RPC `tools/call` (or `tools/list`) at the
merchant's advertised MCP endpoint and judges the JSON-RPC ENVELOPE the way the
spec pins it: UCP payloads in `result.structuredContent` (+ `content[]` text that
parses to the same document), business outcomes as `result` envelopes, protocol
errors as JSON-RPC errors (-32602 invalid params, -32001 negotiation, -32000
protocol) under the corresponding HTTP status (OVR-060). The catalog/location MCP
tools (MCP-001/006/009) stay with their capabilities: a merchant that does not
advertise them is not graded on them.

Register ids graded (transports.json / overview.json / negotiation-errors.json /
replay-protection-payload-matching.json at 2026-08-25): MCP-003, MCP-004,
MCP-005, MCP-016 (business half), OVR-060, NEG-001 (MCP column), REPLAY-001 (MCP
column). The client-side MCP rows -- MCP-002, MCP-007, MCP-008, MCP-010, MCP-011,
MCP-012, MCP-013, MCP-014, MCP-015 (what the PLATFORM must put in a request /
must check in a response) -- belong to the agent lane (D3-11..D3-15, "D9"); the
business-side validation those rows imply (meta required, `id` never inside the
payload) is graded here under MCP-003/MCP-016 conformance item 5.

Transport-scoped (`transport="mcp"`): every check is not-applicable on a
merchant that advertises no MCP binding. Version-scoped to 2026-08-25 (the
2026-04-08 MCP checks live in merchant_checks.py).
"""
import json, pathlib, sys, uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from engine import Resp, fetch, CLEAN, DEVIATION                       # noqa: E402
from merchant_checks import MCheck                                      # noqa: E402
from wire_shapes import shapes_for, AGENT_PROFILE                       # noqa: E402

VERSIONS = ("2026-08-25",)
V0825 = VERSIONS

CORE_CHECKOUT_TOOLS = ("create_checkout", "get_checkout", "update_checkout",
                       "complete_checkout", "cancel_checkout")
CORE_CART_TOOLS = ("create_cart", "get_cart", "update_cart", "cancel_cart")

INVALID_PARAMS, PROTOCOL_ERROR, NEGOTIATION_ERROR = -32602, -32000, -32001


# ---- JSON-RPC plumbing ---------------------------------------------------------
def _meta(**extra):
    return {"ucp-agent": {"profile": AGENT_PROFILE}, **extra}


def _rpc(ctx, method, params=None, rpc_id=1, headers=None):
    """POST one JSON-RPC request to the merchant's MCP endpoint; the Resp carries
    the HTTP status and the FULL JSON-RPC document as .json."""
    doc = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        doc["params"] = params
    h = {"Content-Type": "application/json", "request-id": str(uuid.uuid4()), **(headers or {})}
    return fetch(ctx.mcp_endpoint, "", "POST", doc, h)


def _call(ctx, name, arguments, headers=None):
    return _rpc(ctx, "tools/call", {"name": name, "arguments": arguments}, headers=headers)


def _checkout_payload(ctx, **overrides):
    """The version's REST create body (wire_shapes) minus `id`: over MCP the
    payload object MUST NOT carry an id (checkout/mcp.md#L122-L123, MCP-002)."""
    p = shapes_for(ctx.version).checkout_create(ctx)
    p.pop("id", None)
    p.update(overrides)
    return p


def _create_args(ctx, **meta_extra):
    return {"meta": _meta(**meta_extra), "checkout": _checkout_payload(ctx)}


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


# ---- fetches -----------------------------------------------------------------
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


def tools_list_raw(ctx):
    return _rpc(ctx, "tools/list", rpc_id=2)


def live_profile_resp(ctx):
    """The profile as served NOW (not the one captured at discovery): the
    battery's discovery mutants must be visible to this check."""
    r = fetch(ctx.base, "/.well-known/ucp")
    if r.status != 200 or not isinstance(r.json, dict):
        return r
    doc = r.json.get("ucp", r.json)
    return Resp(r.status, r.headers, json.dumps(doc).encode())


def unsupported_version_raw(ctx):
    """A create whose HTTP-level UCP-Agent (the header the MCP signed-request
    example carries) names a version no business advertises."""
    return _call(ctx, "create_checkout", _create_args(ctx),
                 headers={"UCP-Agent": f'profile="{AGENT_PROFILE}"; version="2099-01-01"'})


# ---- predicates ----------------------------------------------------------------
def p_structured_content(r):
    """MCP-003/004: a create via tools/call is a JSON-RPC result whose
    structuredContent IS the checkout (ucp + id + status); when content[] is
    present its text part parses to the same document."""
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
    structuredContent is the UCP error envelope with messages[] -- never a
    JSON-RPC error."""
    res = _result(r)
    if res is None:
        return DEVIATION
    sc = res.get("structuredContent")
    if not isinstance(sc, dict):
        return DEVIATION
    ucp, messages = sc.get("ucp"), sc.get("messages")
    if not isinstance(ucp, dict) or ucp.get("status") != "error":
        return DEVIATION
    if not isinstance(messages, list) or not messages \
            or not all(isinstance(m, dict) and m.get("code") for m in messages):
        return DEVIATION
    return CLEAN


def p_invalid_params(r):
    """A tools/call without meta is rejected as JSON-RPC -32602 (invalid params),
    with no masquerading result."""
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
    """OVR-060: the JSON-RPC negotiation error is served under the REST status
    that corresponds to it (version_unsupported -> 422), never a blanket 200."""
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


# ---- the check set ---------------------------------------------------------------
CHECKS_MCP = [
    MCheck("mcp.checkout_structured_content", ["MCP-003", "MCP-004"], "MUST",
           create_raw, p_structured_content,
           ["drop:result.structuredContent", "drop:result", "set:result.structuredContent={}",
            'set:result.content=[{"type":"text","text":"{}"}]',
            'set:error={"code":-32000,"message":"x"}', "status:500", "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="mcp",
           versions=V0825),
    MCheck("mcp.business_outcome_as_result", ["MCP-003"], "MUST",
           out_of_stock_raw, p_business_outcome,
           ['set:error={"code":-32000,"message":"out of stock"}', "drop:result",
            'set:result.structuredContent.ucp.status="success"',
            "set:result.structuredContent.messages=[]", "status:400", "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",),
           cfg_needs=("out_of_stock_id",), transport="mcp", versions=V0825),
    MCheck("mcp.meta_required_32602", ["MCP-003"], "MUST",
           no_meta_raw, p_invalid_params,
           ["drop:error", "set:error.code=-32000", 'set:result={"structuredContent":{}}',
            "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="mcp",
           versions=V0825),
    MCheck("mcp.tools_list_core_checkout", ["MCP-003", "MCP-004"], "MUST",
           tools_list_raw, p_tools_list_core,
           ["set:result.tools=[]", "drop:result",
            'set:result.tools=[{"name":"create_checkout"},{"name":"get_checkout"},'
            '{"name":"update_checkout"},{"name":"complete_checkout"}]',
            "corrupt-json"],
           capability="dev.ucp.shopping.checkout", transport="mcp", versions=V0825),
    MCheck("mcp.negotiation_error_32001", ["NEG-001"], "MUST",
           unsupported_version_raw, p_negotiation_32001,
           ["set:error.code=-32000", "drop:error", 'set:result={"structuredContent":{}}',
            "corrupt-json"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="mcp",
           versions=V0825),
    MCheck("mcp.error_http_status", ["OVR-060"], "MUST",
           unsupported_version_raw, p_error_http_status,
           ["status:200", "status:500", "drop:error"],
           capability="dev.ucp.shopping.checkout", needs=("product",), transport="mcp",
           versions=V0825),
    # Unattributed: no register row obliges a business to OFFER MCP; this check
    # pins that the golden's served profile advertises the binding it serves
    # (kill: defects_config mutant mcp_transport_not_advertised).
    MCheck("discovery.mcp_transport_advertised", [], "MUST",
           live_profile_resp, p_mcp_advertised,
           ["drop:services", "set:services={}",
            'set:services={"dev.ucp.shopping":[{"transport":"rest","endpoint":"https://m.example"}]}',
            "corrupt-json"],
           transport="mcp", versions=V0825),
]
