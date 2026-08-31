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


# R11 (PLAN-0825 SS C.4): the ONE choke point for defect injection. The engine
# itself lives in server_state (shared with the test-only fixture route).
@app.middleware("http")
async def defects_middleware(request: Request, call_next):
  """Apply the currently-armed mutant (if any) to a matching response's JSON
  body. OFF-BY-DEFAULT short-circuit: when defects mode is disabled this reads
  `engine.enabled` (a bool already computed at construction) and returns
  `call_next`'s response completely untouched -- no body read, no JSON parse,
  no re-serialization -- so the normal serve path is byte-for-byte identical
  to a server build with no defects code at all (proven in defects_test.py and
  by the battery runner's own disabled-mode capture, PLAN-0825 SS C.4 build
  item 1)."""
  response = await call_next(request)
  engine = server_state.defects_engine()
  if not engine.enabled:
    return response
  route = request.scope.get("route")
  route_template = getattr(route, "path", request.url.path)
  # Peek at whether a mutant is even armed for this (method, route) BEFORE
  # paying for body drain + JSON parse -- keeps every unarmed request cheap
  # even while defects mode is globally enabled (battery mid-run, between
  # mutants).
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
