#   Copyright 2026 UCP Authors
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""MCP (JSON-RPC 2.0, streamable HTTP) transport for the UCP shopping service.

The discovery profile advertises an ``mcp`` transport at ``<endpoint>/mcp``
(D3-09). This endpoint answers ``initialize``, ``tools/list`` (the shopping
tools this golden serves, mirroring the UCP shopping OpenRPC
``services/shopping/mcp.openrpc.json``: checkout, cart, get_order -- no catalog,
which this golden does not advertise) and ``tools/call``, which
routes/mcp_bridge.py dispatches to the SAME REST handlers. A client that asks
for ``text/event-stream`` gets the same JSON-RPC response as one SSE
``message`` event (checkout/mcp.md conformance item 6, streaming; proven for
one event).

Signature verification runs on this route over the JSON-RPC body bytes
(checkout/mcp.md "Request Signing": Content-Digest binds the JSON-RPC body,
no canonicalization), AFTER server.py's McpMetaMiddleware mapped
``arguments.meta`` onto the UCP-Agent / Idempotency-Key headers; a failure is
a JSON-RPC ``-32000`` under the corresponding HTTP status (OVR-060).
"""

import json
from typing import Any

import config
import dependencies
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse
from routes import mcp_bridge
import server_state

router = APIRouter()

# MCP protocol revision this endpoint speaks.
MCP_PROTOCOL_VERSION = "2025-06-18"

_OBJECT = {"type": "object"}
_STRING = {"type": "string"}
_SCHEMAS = "https://ucp.dev/2026-08-25/schemas"
_META = {
  "type": "object",
  "description": "Request metadata (maps to the HTTP UCP-Agent / Idempotency-Key headers).",
  "required": ["ucp-agent"],
  "properties": {
    "ucp-agent": {"type": "object", "required": ["profile"],
                  "properties": {"profile": {"type": "string", "format": "uri"}}},
    "idempotency-key": {"type": "string"},
  },
}
_META_IDEM = {**_META, "required": ["ucp-agent", "idempotency-key"]}


def _tool(name, description, props, required, output):
  return {
    "name": name,
    "description": description,
    "inputSchema": {"type": "object", "properties": props, "required": required},
    # overview/index.md "Response Format": servers SHOULD declare outputSchema
    # referencing the UCP JSON Schema for the capability.
    "outputSchema": {"$ref": f"{_SCHEMAS}/{output}"},
  }


# Shopping tools this golden serves over MCP, mirroring the UCP shopping OpenRPC
# (services/shopping/mcp.openrpc.json at 2026-08-25): the core checkout tools
# (checkout/mcp.md), the core cart tools (cart/mcp.md) and get_order
# (order/mcp.md). Catalog tools are not listed: this golden does not advertise
# the catalog capabilities (STATUS.md).
_TOOLS: list[dict[str, Any]] = [
  _tool("create_checkout", "Create a checkout.",
        {"meta": _META, "checkout": _OBJECT}, ["meta", "checkout"], "shopping/checkout.json"),
  _tool("get_checkout", "Get a checkout by id.",
        {"meta": _META, "id": _STRING}, ["meta", "id"], "shopping/checkout.json"),
  _tool("update_checkout", "Update a checkout.",
        {"meta": _META, "id": _STRING, "checkout": _OBJECT}, ["meta", "id", "checkout"],
        "shopping/checkout.json"),
  _tool("complete_checkout", "Complete a checkout and place the order.",
        {"meta": _META_IDEM, "id": _STRING, "checkout": _OBJECT}, ["meta", "id", "checkout"],
        "shopping/checkout.json"),
  _tool("cancel_checkout", "Cancel a checkout.",
        {"meta": _META_IDEM, "id": _STRING}, ["meta", "id"], "shopping/checkout.json"),
  _tool("create_cart", "Create a cart.",
        {"meta": _META, "cart": _OBJECT}, ["meta", "cart"], "shopping/cart.json"),
  _tool("get_cart", "Get a cart by id.",
        {"meta": _META, "id": _STRING}, ["meta", "id"], "shopping/cart.json"),
  _tool("update_cart", "Update a cart.",
        {"meta": _META, "id": _STRING, "cart": _OBJECT}, ["meta", "id", "cart"],
        "shopping/cart.json"),
  _tool("cancel_cart", "Cancel a cart.",
        {"meta": _META_IDEM, "id": _STRING}, ["meta", "id"], "shopping/cart.json"),
  _tool("get_order", "Get an order by id.",
        {"meta": _META, "id": _STRING}, ["meta", "id"], "shopping/order.json"),
]


def _respond(request: Request, body: dict, status: int = 200) -> Response:
  """Serve a JSON-RPC response as JSON, or as ONE SSE `message` event when the
  client asked for text/event-stream (streamable HTTP; checkout/mcp.md
  conformance item 6). The event's data is the same JSON-RPC document."""
  accept = request.headers.get("accept", "")
  if "text/event-stream" in accept:
    event = "event: message\ndata: " + json.dumps(body) + "\n\n"
    return Response(content=event, status_code=status, media_type="text/event-stream")
  return JSONResponse(body, status_code=status)


def _jsonrpc_from_http_error(request_id: Any, exc: HTTPException) -> tuple[dict, int]:
  """A signature-verification HTTPException (dependencies._signature_http_error)
  -> the JSON-RPC protocol error -32000 carrying the UCP envelope as `data`,
  under the same HTTP status (OVR-060)."""
  detail = exc.detail
  errors = detail.get("errors") if isinstance(detail, dict) else None
  messages = [
    {"type": "error", "code": str(e.get("code", "invalid_request")),
     "content": str(e.get("message") or e.get("content") or ""),
     "severity": "unrecoverable"}
    for e in (errors or []) if isinstance(e, dict)
  ] or [{"type": "error", "code": "invalid_request",
         "content": detail if isinstance(detail, str) else json.dumps(detail),
         "severity": "unrecoverable"}]
  envelope = {"ucp": {"version": config.get_server_version(), "status": "error"},
              "messages": messages}
  return {
    "jsonrpc": "2.0",
    "id": request_id,
    "error": {"code": mcp_bridge.PROTOCOL_ERROR, "message": messages[0]["content"],
              "data": envelope},
  }, exc.status_code


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
  """The MCP (JSON-RPC 2.0) endpoint: initialize, tools/list, tools/call.

  Signature verification applies the same policy as every other inbound
  route (verify-if-present by default, required under
  ``--require_signatures``), over the JSON-RPC body bytes. It runs here, in
  the handler, rather than as a route dependency so that (a) server.py's
  McpMetaMiddleware has already mapped ``arguments.meta`` onto the
  UCP-Agent / Idempotency-Key headers (the ordering the spec's signed
  example relies on: the signer's identity may travel only in meta) and
  (b) a rejection is a JSON-RPC ``-32000`` error under the REST status
  (OVR-060), not a bare REST envelope.
  """
  try:
    payload = await request.json()
  except Exception:
    return _respond(request, {"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "Parse error"}})

  method = payload.get("method") if isinstance(payload, dict) else None
  request_id = payload.get("id") if isinstance(payload, dict) else None

  try:
    await dependencies.verify_signature(request)
  except HTTPException as exc:
    body, status = _jsonrpc_from_http_error(request_id, exc)
    if server_state.defects_engine().behavior_armed("mcp.error_http_status_200"):
      status = 200
    return _respond(request, body, status)

  # JSON-RPC notifications (no id), e.g. notifications/initialized.
  if (
    request_id is None
    and isinstance(method, str)
    and method.startswith("notifications/")
  ):
    return Response(status_code=202)

  if method == "initialize":
    return _respond(request, {
      "jsonrpc": "2.0",
      "id": request_id,
      "result": {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {
          "name": "ucp-shopping-sample",
          "version": config.get_server_version(),
        },
      },
    })

  if method == "tools/list":
    tools = _TOOLS
    # behavior row `mcp_tools_list_missing_cancel` (MCP-003 item 2, "provide all
    # core checkout tools"): armed, cancel_checkout is not listed.
    if server_state.defects_engine().behavior_armed("mcp.tools_list_missing_cancel"):
      tools = [t for t in _TOOLS if t["name"] != "cancel_checkout"]
    return _respond(request, {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}})

  if method == "tools/call":
    body, status = await mcp_bridge.dispatch_tool_call(request, payload)
    return _respond(request, body, status)

  return _respond(request, {
    "jsonrpc": "2.0",
    "id": request_id,
    "error": {"code": -32601, "message": f"Method not found: {method}"},
  })
