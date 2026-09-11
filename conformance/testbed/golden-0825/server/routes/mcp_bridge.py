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

"""MCP `tools/call` bridge to the REST handlers (D3-09, PLAN-v3 SS2.15).

The MCP transport is a MAPPING LAYER over the same UCP operations
(overview/index.md "Model Context Protocol", checkout/mcp.md "Implementation"):
OpenRPC `method` -> `params.name`, OpenRPC params -> `params.arguments`,
HTTP headers -> `arguments.meta`. This module implements exactly that mapping
and nothing else -- every operation is dispatched IN-PROCESS to the REST route
that already implements it (request validation, version negotiation,
idempotency, the error envelope, the defects seam), so the two transports can
never drift apart in business behaviour.

Two pieces:

* `McpMetaMiddleware` (pure ASGI, installed in server.py): for a `tools/call`
  it maps `meta["ucp-agent"]` -> `UCP-Agent` and `meta["idempotency-key"]` ->
  `Idempotency-Key` onto the request scope BEFORE `dependencies.verify_signature`
  runs on the /mcp route, so a signature whose UCP-Agent travels only in meta
  still resolves its keys from the platform profile (the named ordering test,
  mcp_test.py::test_meta_headers_precede_signature_verify). The mapped names
  are recorded in `scope["ucp_meta_mapped"]` so the verifier's REQUIRED-
  COMPONENT set is computed over the headers the signer could actually cover
  (the wire headers), never over a header this server synthesized.
* `dispatch_tool_call(request, payload)`: the tools/call -> REST -> JSON-RPC
  round trip. UCP payloads come back in `result.structuredContent` (+ `content[]`
  serialized JSON, overview "Response Format"); business outcomes -- 4xx UCP
  envelopes such as out_of_stock -- are `result`s whose structuredContent is the
  envelope (checkout/mcp.md "Business Outcomes"); protocol errors are JSON-RPC
  errors: -32602 invalid params (meta missing, `id` inside a create/update
  payload -- MCP-002/MCP-015 --, a schema-invalid input), -32001 negotiation
  (overview NEG table's MCP column), -32000 protocol (signature errors,
  idempotency conflict -- signatures.md idempotency table --, 401/403/429/503),
  -32603 for a 500; each JSON-RPC error is served under the REST status it maps
  from (OVR-060: "servers MUST return the corresponding HTTP status code
  alongside the JSON-RPC error").

Behavior guards (decision 19; rows in defects_config.json `behavior_mutants[]`,
graded by the battery through the MCP conformance checks):
  mcp.meta_not_required            -- a missing meta is accepted (default agent)
  mcp.business_error_as_jsonrpc_error -- a business outcome served as -32000
  mcp.negotiation_error_code_32000 -- negotiation errors emitted as -32000
  mcp.error_http_status_200        -- JSON-RPC errors always under HTTP 200
  mcp.headers_after_verify         -- meta mapped AFTER verify_signature (middleware)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import Request
import server_state

logger = logging.getLogger(__name__)

# tool name -> (REST method, path template, payload argument, id argument)
TOOL_ROUTES: dict[str, tuple[str, str, str | None, str | None]] = {
  "create_checkout": ("POST", "/checkout-sessions", "checkout", None),
  "get_checkout": ("GET", "/checkout-sessions/{id}", None, "id"),
  "update_checkout": ("PUT", "/checkout-sessions/{id}", "checkout", "id"),
  "complete_checkout": ("POST", "/checkout-sessions/{id}/complete", "checkout", "id"),
  "cancel_checkout": ("POST", "/checkout-sessions/{id}/cancel", None, "id"),
  "create_cart": ("POST", "/carts", "cart", None),
  "get_cart": ("GET", "/carts/{id}", None, "id"),
  "update_cart": ("PUT", "/carts/{id}", "cart", "id"),
  "cancel_cart": ("POST", "/carts/{id}/cancel", None, "id"),
  "get_order": ("GET", "/orders/{id}", None, "id"),
}

# Tools whose OpenRPC `meta` additionally requires idempotency-key.
IDEMPOTENCY_REQUIRED = {"complete_checkout", "cancel_checkout", "cancel_cart"}

# JSON-RPC codes (overview/index.md "Error Handling", transport binding table).
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
PROTOCOL_ERROR = -32000
NEGOTIATION_ERROR = -32001

# UCP codes the overview's negotiation table maps to -32001.
NEGOTIATION_CODES = {
  "invalid_profile_url", "profile_unreachable", "profile_malformed",
  "version_unsupported", "version_invalid_format",
}
# UCP codes / HTTP statuses that are PROTOCOL errors (-32000): the signature
# table, the idempotency table's mismatched-payload row, and the generic
# 401/403/429/503 rows of the transport binding table.
PROTOCOL_CODES = {
  "signature_missing", "signature_invalid", "key_not_found", "digest_mismatch",
  "algorithm_unsupported", "signature_expired", "idempotency_conflict",
  "unauthorized", "forbidden",
}
PROTOCOL_STATUSES = {401, 403, 429, 503}


# ---------------------------------------------------------------------------
# meta -> header mapping (runs BEFORE verify_signature; see the module docstring)
# ---------------------------------------------------------------------------


def meta_headers(payload: Any) -> dict[str, str]:
  """The HTTP headers a tools/call's `arguments.meta` maps onto: `ucp-agent`
  (profile=, plus version= when meta carries one) and `idempotency-key`.
  Empty for anything that is not a well-formed tools/call with a meta object."""
  if not isinstance(payload, dict) or payload.get("method") != "tools/call":
    return {}
  params = payload.get("params")
  args = params.get("arguments") if isinstance(params, dict) else None
  meta = args.get("meta") if isinstance(args, dict) else None
  if not isinstance(meta, dict):
    return {}
  out: dict[str, str] = {}
  agent = meta.get("ucp-agent")
  if isinstance(agent, dict) and isinstance(agent.get("profile"), str) and agent["profile"]:
    value = f'profile="{agent["profile"]}"'
    if isinstance(agent.get("version"), str) and agent["version"]:
      value += f'; version="{agent["version"]}"'
    out["ucp-agent"] = value
  key = meta.get("idempotency-key")
  if isinstance(key, str) and key:
    out["idempotency-key"] = key
  return out


class McpMetaMiddleware:
  """Pure ASGI: for `POST /mcp`, read (and replay) the JSON-RPC body and add the
  meta-mapped headers to the scope when the wire does not already carry them.
  Any other request passes through untouched -- no body read."""

  def __init__(self, app):
    self.app = app

  async def __call__(self, scope, receive, send):
    if scope["type"] != "http" or scope.get("method") != "POST" \
        or scope.get("path", "") not in ("/mcp", "/mcp/"):
      await self.app(scope, receive, send)
      return
    chunks = []
    while True:
      message = await receive()
      if message["type"] != "http.request":
        break
      chunks.append(message.get("body", b""))
      if not message.get("more_body", False):
        break
    body = b"".join(chunks)
    replay = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive_replay():
      if replay:
        return replay.pop(0)
      return await receive()

    try:
      payload = json.loads(body) if body else None
    except (json.JSONDecodeError, UnicodeDecodeError):
      payload = None
    mapped = meta_headers(payload)
    present = {k.decode("latin-1").lower() for k, _ in scope.get("headers", [])}
    to_add = {k: v for k, v in mapped.items() if k not in present}
    engine = server_state.defects_engine()
    # behavior row `mcp_headers_after_verify` (D3-09): the ONE place the order
    # can be swapped. Armed, the meta headers are NOT applied before the route
    # (verify_signature then sees no UCP-Agent and cannot resolve the signer's
    # keys); the bridge still reads meta itself, so everything else works.
    if to_add and not engine.behavior_armed("mcp.headers_after_verify"):
      scope["headers"] = list(scope.get("headers", [])) + [
        (k.encode("latin-1"), v.encode("latin-1")) for k, v in to_add.items()
      ]
      scope["ucp_meta_mapped"] = set(to_add)
    await self.app(scope, receive_replay, send)


# ---------------------------------------------------------------------------
# tools/call -> REST -> JSON-RPC
# ---------------------------------------------------------------------------


class _Rpc(Exception):
  """A JSON-RPC error to emit: (code, message, http_status, data)."""

  def __init__(self, code: int, message: str, http_status: int = 200, data: Any = None):
    super().__init__(message)
    self.code, self.message, self.http_status, self.data = code, message, http_status, data


def _envelope(status: int, body: Any) -> dict | None:
  """The UCP error envelope carried by a REST error response, or None."""
  if status < 400 or not isinstance(body, dict):
    return None
  ucp = body.get("ucp")
  if isinstance(ucp, dict) and ucp.get("status") == "error" and isinstance(body.get("messages"), list):
    return body
  return None


def _codes(envelope: dict) -> list[str]:
  return [str(m.get("code")) for m in envelope.get("messages", []) if isinstance(m, dict)]


def classify(status: int, body: Any, engine) -> tuple[str, int, int]:
  """(kind, jsonrpc_code, http_status) for a REST outcome: kind is "result" for
  a success or a BUSINESS outcome, "error" for a JSON-RPC protocol error."""
  env = _envelope(status, body)
  if status < 400:
    return "result", 0, 200
  if env is None:
    # a non-envelope error body (never expected from this server: D3-04)
    return "error", INTERNAL_ERROR if status >= 500 else PROTOCOL_ERROR, status
  codes = _codes(env)
  if status >= 500:
    return "error", INTERNAL_ERROR, status
  if any(c in NEGOTIATION_CODES for c in codes):
    # behavior row `mcp_negotiation_error_code_-32000`: -32000 instead of -32001.
    code = PROTOCOL_ERROR if engine.behavior_armed("mcp.negotiation_error_code_32000") \
        else NEGOTIATION_ERROR
    return "error", code, status
  if status in PROTOCOL_STATUSES or any(c in PROTOCOL_CODES for c in codes):
    return "error", PROTOCOL_ERROR, status
  if status == 422 and "invalid_request" in codes:
    # a request the UCP schemas reject (checkout/mcp.md conformance item 5:
    # "Validate tool inputs against UCP schemas") -> JSON-RPC invalid params
    return "error", INVALID_PARAMS, status
  # behavior row `mcp_business_error_as_jsonrpc_error`: the business outcome
  # (out_of_stock, payment_failed, not_found, checkout_not_modifiable ...)
  # served as a JSON-RPC error instead of a `result` envelope.
  if engine.behavior_armed("mcp.business_error_as_jsonrpc_error"):
    return "error", PROTOCOL_ERROR, status
  return "result", 0, 200


def _build_rest_request(request: Request, tool: str, arguments: dict) -> tuple[str, str, bytes, dict[str, str]]:
  """(method, path, body_bytes, headers) for the REST dispatch of `tool`."""
  method, template, payload_arg, id_arg = TOOL_ROUTES[tool]
  meta = arguments.get("meta")
  wire = {k.lower(): v for k, v in request.headers.items()}
  mapped = meta_headers({"method": "tools/call", "params": {"arguments": arguments}})
  engine = server_state.defects_engine()
  if not mapped.get("ucp-agent"):
    # behavior row `mcp_meta_not_required` (MCP-010 / MCP-014 / MCP-011 ...):
    # armed, a request without meta.ucp-agent.profile is accepted under a
    # synthesized agent identity instead of being rejected.
    if not engine.behavior_armed("mcp.meta_not_required"):
      raise _Rpc(INVALID_PARAMS,
                 "Invalid params: arguments.meta['ucp-agent'].profile is required on every request",
                 400)
    mapped["ucp-agent"] = 'profile="about:blank"'
  path = template
  if id_arg is not None:
    rid = arguments.get(id_arg)
    if not isinstance(rid, str) or not rid:
      raise _Rpc(INVALID_PARAMS, f"Invalid params: '{id_arg}' (string) is required for {tool}", 400)
    path = template.replace("{id}", rid)
  body = b""
  if payload_arg is not None:
    payload = arguments.get(payload_arg)
    if not isinstance(payload, dict):
      raise _Rpc(INVALID_PARAMS, f"Invalid params: '{payload_arg}' (object) is required for {tool}", 400)
    if "id" in payload:
      # MCP-002 / MCP-015: the resource is identified by the top-level `id`
      # argument; the payload object MUST NOT carry one.
      raise _Rpc(INVALID_PARAMS,
                 f"Invalid params: the '{payload_arg}' object MUST NOT contain an 'id' field "
                 f"(use the top-level 'id' argument to name an existing resource)", 400)
    body = json.dumps(payload).encode("utf-8")
  headers = {
    # the HTTP-level UCP-Agent (the header the spec's signed MCP example
    # carries, and the one a signature covers) is authoritative when present;
    # meta fills it in otherwise -- the same rule McpMetaMiddleware applies.
    "ucp-agent": wire.get("ucp-agent") or mapped["ucp-agent"],
    "request-id": (meta.get("request-id") if isinstance(meta, dict)
                   and isinstance(meta.get("request-id"), str) else None)
                  or wire.get("request-id") or str(uuid.uuid4()),
  }
  idem = mapped.get("idempotency-key") or wire.get("idempotency-key")
  if not idem:
    if tool in IDEMPOTENCY_REQUIRED:
      raise _Rpc(INVALID_PARAMS,
                 f"Invalid params: arguments.meta['idempotency-key'] is required for {tool}", 400)
    idem = str(uuid.uuid4())
  headers["idempotency-key"] = idem
  if body:
    headers["content-type"] = "application/json"
    headers["content-length"] = str(len(body))
  for name in ("host", "accept-language", "user-agent"):
    if name in wire:
      headers[name] = wire[name]
  return method, path, body, headers


async def _dispatch_rest(request: Request, method: str, path: str, body: bytes,
                         headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
  """Run one REST request through the SAME application (middleware stack
  included) in-process and return (status, headers, body)."""
  scope = {
    "type": "http",
    "asgi": request.scope.get("asgi", {"version": "3.0"}),
    "http_version": request.scope.get("http_version", "1.1"),
    "method": method,
    "scheme": request.scope.get("scheme", "http"),
    "path": path,
    "raw_path": path.encode("utf-8"),
    "query_string": b"",
    "root_path": request.scope.get("root_path", ""),
    "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
    "client": request.scope.get("client"),
    "server": request.scope.get("server"),
    "state": {},
    # the outer /mcp request already ran verify_signature over the signed
    # JSON-RPC bytes (checkout/mcp.md "Request Signing": the signature is
    # applied at the HTTP layer); the bridged REST request carries no
    # signature of its own and must not be asked for one.
    "ucp_mcp_bridged": True,
  }
  sent: dict[str, Any] = {"status": 500, "headers": [], "body": b""}
  queue = [{"type": "http.request", "body": body, "more_body": False}]

  async def receive():
    if queue:
      return queue.pop(0)
    return {"type": "http.disconnect"}

  async def send(message):
    if message["type"] == "http.response.start":
      sent["status"] = message["status"]
      sent["headers"] = message.get("headers", [])
    elif message["type"] == "http.response.body":
      sent["body"] += message.get("body", b"")

  await request.app(scope, receive, send)
  hdrs = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in sent["headers"]}
  return sent["status"], hdrs, sent["body"]


async def dispatch_tool_call(request: Request, payload: dict) -> tuple[dict, int]:
  """The full tools/call round trip. Returns (json_rpc_response, http_status)."""
  request_id = payload.get("id")
  engine = server_state.defects_engine()
  try:
    params = payload.get("params")
    if not isinstance(params, dict):
      raise _Rpc(INVALID_PARAMS, "Invalid params: 'params' object is required for tools/call", 400)
    tool = params.get("name")
    if tool not in TOOL_ROUTES:
      raise _Rpc(INVALID_PARAMS, f"Invalid params: unknown tool {tool!r}", 400)
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
      raise _Rpc(INVALID_PARAMS, "Invalid params: 'arguments' object is required", 400)
    method, path, body, headers = _build_rest_request(request, tool, arguments)
    status, _hdrs, raw = await _dispatch_rest(request, method, path, body, headers)
    try:
      parsed = json.loads(raw) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
      parsed = None
    kind, code, http_status = classify(status, parsed, engine)
    if kind == "result":
      structured = parsed if isinstance(parsed, dict) else {}
      return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
          "structuredContent": structured,
          "content": [{"type": "text", "text": json.dumps(structured)}],
        },
      }, 200
    env = _envelope(status, parsed)
    message = (env["messages"][0].get("content") if env and env["messages"]
               else f"HTTP {status}") or f"HTTP {status}"
    raise _Rpc(code, str(message), http_status, env)
  except _Rpc as exc:
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.data is not None:
      error["data"] = exc.data
    http_status = exc.http_status
    # behavior row `mcp_error_http_status_200` (OVR-060): armed, every JSON-RPC
    # error is served under HTTP 200 instead of the corresponding status.
    if engine.behavior_armed("mcp.error_http_status_200"):
      http_status = 200
    return {"jsonrpc": "2.0", "id": request_id, "error": error}, http_status
