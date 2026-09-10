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

"""UCP Merchant Server (Python/FastAPI)."""

import json
import logging
import sys
from collections.abc import Sequence
from absl import app as absl_app
import config
import defects
from enums import ErrorSeverity, MessageType
from exceptions import UcpError, UcpErrorResponse, UcpMessageError
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import generated_routes.ucp_routes
from routes.defect_fixtures import router as defect_fixtures_router
from routes.discovery import router as discovery_router
from routes.mcp import router as mcp_router
from routes.order import router as order_router
import routes.ucp_implementation
import server_state
from services import version_projection
from ucp_version import extract_agent_version
import uvicorn

# --- App Setup ---

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
  title="UCP Shopping Service",
  version=config.get_server_version(),
  description="Reference implementation of the UCP Shopping Service",
  lifespan=config.lifespan,
)


# C3 version projection (D3-03, decision 18): serve 2026-04-08 from this
# 08-25 server. Pure ASGI (not BaseHTTPMiddleware) because the REQUEST body
# must be rewritten BEFORE FastAPI validates it against the 08-25 models. It
# is added first, so it sits INNERMOST: the defects middleware below still
# sees the behavior keys the projection consulted (x-defects-consulted).
class VersionProjectionMiddleware:
  """When the platform negotiated 2026-04-08 (UCP-Agent version=), project the
  request into the 08-25 model shape and the JSON response into the 04-08
  wire shape (services/version_projection.py). Any other version, or a
  non-http scope, passes through untouched -- no body read, no parse."""

  def __init__(self, app):
    self.app = app

  async def __call__(self, scope, receive, send):
    if scope["type"] != "http":
      await self.app(scope, receive, send)
      return
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    version = extract_agent_version(headers.get("ucp-agent"))
    leaf = version_projection.LEAF_VERSION
    prefix = f"/{leaf}/"
    path = scope.get("path", "")
    if path.startswith(prefix) and path != f"/{leaf}/.well-known/ucp":
      # The leaf profile's service endpoint is {{ENDPOINT}}/2026-04-08: a
      # version-specific profile points at a version-specific endpoint, so a
      # request there IS a 2026-04-08 request. Route it to the same handlers
      # (strip the prefix) and, when UCP-Agent carries no version=, negotiate
      # at the endpoint's version -- at this endpoint "ours" is 2026-04-08
      # (decision 21's fallback, applied per endpoint). An explicit version=
      # still wins and is validated as usual (an unadvertised one is 422).
      scope["path"] = path[len(prefix) - 1:]
      if "raw_path" in scope and scope["raw_path"]:
        raw = scope["raw_path"]
        if raw.startswith(prefix.encode()):
          scope["raw_path"] = raw[len(prefix) - 1:]
      if version is None and "ucp-agent" in headers:
        version = leaf
        scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"ucp-agent"]
        scope["headers"].append((b"ucp-agent", f'{headers["ucp-agent"]}; version="{leaf}"'.encode("latin-1")))
    if version != leaf:
      await self.app(scope, receive, send)
      return

    if scope.get("method") in ("POST", "PUT", "PATCH"):
      raw = b""
      more = True
      while more:
        message = await receive()
        raw += message.get("body", b"")
        more = message.get("more_body", False)
      try:
        parsed = json.loads(raw) if raw else None
      except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None
      if isinstance(parsed, dict):
        raw = json.dumps(version_projection.project_request(parsed, version)).encode("utf-8")
        scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
        scope["headers"].append((b"content-length", str(len(raw)).encode()))

      async def receive_projected():
        return {"type": "http.request", "body": raw, "more_body": False}

      receive = receive_projected

    start_message = None
    chunks = []

    async def send_projected(message):
      nonlocal start_message
      if message["type"] == "http.response.start":
        start_message = message
        return
      if message["type"] == "http.response.body":
        chunks.append(message.get("body", b""))
        if message.get("more_body", False):
          return
        body = b"".join(chunks)
        try:
          parsed = json.loads(body) if body else None
        except (json.JSONDecodeError, UnicodeDecodeError):
          parsed = None
        if isinstance(parsed, dict):
          projected = version_projection.project_response(
              parsed, version, server_state.defects_engine())
          body = json.dumps(projected).encode("utf-8")
        headers = [(k, v) for k, v in start_message["headers"] if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode()))
        await send({"type": "http.response.start", "status": start_message["status"],
                    "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})
        return
      await send(message)

    await self.app(scope, receive, send_projected)


app.add_middleware(VersionProjectionMiddleware)


# R11 (PLAN-0825 SS C.4): the ONE choke point for defect injection. The engine
# itself lives in server_state (shared with the test-only fixture route).
@app.middleware("http")
async def defects_middleware(request: Request, call_next):
  """Apply the currently-armed mutant (if any) to a matching response's JSON
  body. OFF-BY-DEFAULT short-circuit: when defects mode is disabled this reads
  `engine.enabled` (a bool already computed at construction) and returns
  `call_next`'s response completely untouched -- no body read, no JSON parse,
  no re-serialization, no extra header -- so the normal serve path is
  byte-for-byte identical to a server build with no defects code at all
  (proven in defects_test.py and by the battery runner's own disabled-mode
  capture, PLAN-0825 SS C.4 build item 1).

  While defects mode is ON, every response additionally carries
  `x-defects-consulted`: the behavior keys some guard consulted since the arm
  state last changed (D3-01, decision 19). The battery reads it after driving
  a behavior row's checks to tell an UNWIRED key (LOADER-BROKEN: no guard ever
  asked) apart from a guard that asked and whose checks did not flip."""
  response = await call_next(request)
  engine = server_state.defects_engine()
  if not engine.enabled:
    return response
  response = await _apply_armed_patch(request, response, engine)
  consulted = engine.consulted_keys()
  if consulted:
    response.headers["x-defects-consulted"] = ",".join(sorted(consulted))
  return response


async def _apply_armed_patch(request: Request, response, engine):
  route = request.scope.get("route")
  route_template = getattr(route, "path", request.url.path)
  # Peek at whether a mutant is even armed for this (method, route) BEFORE
  # paying for body drain + JSON parse -- keeps every unarmed request cheap
  # even while defects mode is globally enabled (battery mid-run, between
  # mutants). A behavior row carries no "route" and is never a patch: it
  # falls out here untouched, by construction.
  mutant = engine.armed_mutant()
  if mutant is None or "route" not in mutant:
    return response
  mroute = mutant["route"]
  if mroute.get("method") != request.method or mroute.get("path") != route_template:
    return response
  body = b""
  async for chunk in response.body_iterator:
    body += chunk if isinstance(chunk, bytes) else chunk.encode()
  try:
    parsed = json.loads(body)
  except (json.JSONDecodeError, UnicodeDecodeError):
    # Not JSON (or empty/error body) -- nothing this middleware knows how to
    # patch; serve the original bytes back unchanged.
    return Response(
        content=body, status_code=response.status_code,
        headers=dict(response.headers), media_type=response.media_type,
    )
  mutated = defects.apply_patch(parsed, mutant["patch"])
  new_body = json.dumps(mutated).encode("utf-8")
  headers = dict(response.headers)
  headers["content-length"] = str(len(new_body))
  headers["x-defects-armed"] = mutant["name"]
  return Response(
      content=new_body, status_code=response.status_code,
      headers=headers, media_type=response.media_type,
  )


def _format_validation_loc(loc: tuple[int | str, ...]) -> str:
  parts = list(loc)
  if parts and parts[0] in ("body", "query", "path", "header"):
    parts = parts[1:]
  if not parts:
    return str(loc[0]) if loc else "request"
  path = ""
  for p in parts:
    if isinstance(p, int):
      path += f"[{p}]"
    else:
      path = f"{path}.{p}" if path else str(p)
  return path


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
  request: Request, exc: RequestValidationError
):
  """Handle validation errors and convert to the UCP error envelope."""
  del request  # Unused.
  error_lines = []
  for err in exc.errors():
    path = _format_validation_loc(err.get("loc", ()))
    msg = err.get("msg", "Validation error")
    error_lines.append(f"✖ {msg}\n  → at {path}")

  error_content = (
    "\n".join(error_lines) if error_lines else "Request validation failed."
  )
  logger.warning("Request payload failed validation:\n%s", error_content)

  error_response = UcpErrorResponse(
    ucp={
      "version": config.get_server_version(),
      "status": "error",
    },
    messages=[
      UcpMessageError(
        type=MessageType.ERROR,
        code="INVALID_REQUEST",
        content=error_content,
        severity=ErrorSeverity.UNRECOVERABLE,
      )
    ],
  )
  return JSONResponse(
    status_code=422,
    content=error_response.model_dump(mode="json"),
  )


@app.exception_handler(UcpError)
async def ucp_exception_handler(request: Request, exc: UcpError):
  """Handle UCP-specific exceptions and converts them to JSON responses."""
  del request  # Unused.
  error_response = UcpErrorResponse(
    ucp={
      "version": config.get_server_version(),
      "status": "error",
    },
    messages=[
      UcpMessageError(
        type=MessageType.ERROR,
        code=exc.code,
        content=exc.message,
        severity=exc.severity,
      )
    ],
  )
  return JSONResponse(
    status_code=exc.status_code,
    content=error_response.model_dump(mode="json"),
  )


# Apply business logic implementation to generated routes
routes.ucp_implementation.apply_implementation(
  generated_routes.ucp_routes.router
)
app.include_router(generated_routes.ucp_routes.router)
app.include_router(order_router)
app.include_router(discovery_router)
app.include_router(mcp_router)
app.include_router(defect_fixtures_router)


def main(argv: Sequence[str]) -> None:
  """Run the UCP Merchant Server."""
  del argv  # Unused.

  if (
    config.FLAGS.products_db_path is None
    or config.FLAGS.transactions_db_path is None
    or config.FLAGS.port is None
  ):
    logger.error(
      "Both --products_db_path, --transactions_db_path, and --port must be"
      " provided."
    )
    print("\nUsage:")  # noqa: T201
    print(config.FLAGS.main_module_help())  # noqa: T201
    sys.exit(1)

  uvicorn.run(app, host="0.0.0.0", port=config.FLAGS.port)


if __name__ == "__main__":
  absl_app.run(main)
