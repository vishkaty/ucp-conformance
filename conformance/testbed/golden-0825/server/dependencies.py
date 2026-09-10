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

"""FastAPI dependencies for the UCP server.

This module contains dependency injection logic for FastAPI endpoints,
including:
- Header validation (UCP-Agent, Idempotency-Key).
- RFC 9421 request-signature verification (UCP-Agent key discovery).
- Service instantiation (CheckoutService, FulfillmentService).
- Database session management (Products and Transactions DBs).
"""

import logging
import re
from collections.abc import AsyncGenerator
from typing import Annotated

import config
import server_state
import db
from exceptions import UcpError
from exceptions import UcpVersionError
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel
from services.cart_service import CartService
from services.checkout_service import CheckoutService
from services.fulfillment_service import FulfillmentService
from sqlalchemy.ext.asyncio import AsyncSession
import ucp_signing
from ucp_version import extract_agent_version, parse_ucp_version

logger = logging.getLogger(__name__)


class CommonHeaders(BaseModel):
  """Common headers used in UCP requests."""

  x_api_key: str | None = None
  ucp_agent: str
  request_signature: str | None = None
  request_id: str


def _signature_http_error(exc: ucp_signing.SignatureError) -> HTTPException:
  """Wrap a SignatureError in the UCP error-envelope HTTP response."""
  return HTTPException(
    status_code=exc.status_code,
    detail={
      "status": "error",
      "errors": [
        {"code": exc.code, "message": exc.message, "severity": "critical"}
      ],
    },
  )


async def verify_signature(request: Request) -> None:
  """Verify an inbound request's RFC 9421 signature per the UCP spec.

  The signer's public keys are discovered from the ``UCP-Agent`` header's
  profile URL (its ``keys[]``). Behaviour depends on
  ``--require_signatures``:

  * When set, a missing or invalid signature is rejected with the spec's
    error code (401 ``signature_missing`` / ``signature_invalid`` /
    ``key_not_found``, 400 ``digest_mismatch`` / ``algorithm_unsupported``,
    etc.).
  * When unset (the default), signatures are still verified when present and
    the outcome is logged, but unsigned or invalid requests are allowed. This
    keeps the sample interoperable with clients that do not yet sign.

  No profile fetch occurs unless a ``Signature-Input`` header is present, so
  unsigned traffic incurs no extra work.

  Args:
    request: The incoming request.

  Raises:
    HTTPException: With a UCP error envelope when enforcement is on and
      verification fails.

  """
  headers = {k.lower(): v for k, v in request.headers.items()}
  enforcing = config.FLAGS.require_signatures

  if "signature-input" not in headers or "signature" not in headers:
    if enforcing:
      raise _signature_http_error(
        ucp_signing.SignatureError(
          "signature_missing", 401, "Request signature is required"
        )
      )
    logger.debug("No request signature present; skipping verification")
    return

  match = re.search(r'profile="([^"]+)"', headers.get("ucp-agent", ""))
  if not match:
    exc = ucp_signing.SignatureError(
      "signature_invalid",
      401,
      "UCP-Agent profile URL is required to resolve the signing key",
    )
    if enforcing:
      raise _signature_http_error(exc)
    logger.warning("Cannot verify signature: %s", exc.message)
    return

  body = await request.body()
  try:
    keys = await ucp_signing.fetch_signing_keys(
      match.group(1),
      allow_insecure=config.FLAGS.allow_insecure_profile_urls,
    )
    keyid = ucp_signing.verify_request(
      request.method,
      headers.get("host", ""),
      request.url.path,
      request.url.query,
      headers,
      body,
      keys,
    )
  except ucp_signing.SignatureError as exc:
    if enforcing:
      raise _signature_http_error(exc) from exc
    logger.warning(
      "Request signature verification failed (%s: %s); allowing because "
      "--require_signatures is not set",
      exc.code,
      exc.message,
    )
    return
  logger.info(
    "RFC 9421 signature verified (keyid=%s, profile=%s)",
    keyid,
    match.group(1),
  )


async def common_headers(
  request: Request,
  x_api_key: str | None = Header(None),
  ucp_agent: str = Header(...),
  request_signature: str | None = Header(None),
  request_id: str = Header(...),
) -> CommonHeaders:
  """Extract and validate common headers, verifying any request signature."""
  await validate_ucp_headers(ucp_agent)
  await verify_signature(request)
  return CommonHeaders(
    x_api_key=x_api_key,
    ucp_agent=ucp_agent,
    request_signature=request_signature,
    request_id=request_id,
  )


async def validate_ucp_headers(ucp_agent: str):
  """Validate UCP headers and version negotiation."""
  server_version = config.get_server_version()
  try:
    server_date = parse_ucp_version(server_version)
  except UcpVersionError as exc:
    raise UcpError("Server version configuration is invalid") from exc

  # Absent `version=` (decision 21, AMB-010): the rule is "read the platform
  # profile's `version`; absent there too, assume ours". The profile fetch is
  # D3-24's (W2); until it lands the golden assumes its own version and the
  # ambiguity is recorded in conformance/AMBIGUITIES.md (AMB-010).
  agent_version = server_version
  agent_date = server_date

  requested = extract_agent_version(ucp_agent)
  if requested is not None:
    agent_version = requested
    agent_date = parse_ucp_version(agent_version)

  del agent_date  # format validated above; acceptance is by membership, not order

  # C3 negotiation (overview/index.md NEG table, OVR-071 / NEG-001; D3-02): the
  # business serves exactly {its version} ∪ supported_versions. Anything else --
  # newer OR older, advertised nowhere -- is `version_unsupported` (422,
  # lowercase per decision 20). The inherited comparison only rejected NEWER
  # versions, so a never-advertised older version was silently accepted.
  accepted = {server_version, *config.get_supported_versions()}
  if agent_version not in accepted:
    # D3-01 behavior guard (decision 19): the ONE place the golden can be made
    # to accept an unadvertised version -- row `negotiation_accept_any` in
    # defects_config.json. Off (the default) this is a plain `raise`.
    if not server_state.defects_engine().behavior_armed("negotiation.accept_any"):
      raise UcpVersionError(
        message=(
          f"Version {agent_version} is not supported. This merchant"
          f" implements version {server_version}"
          + (f" (also: {', '.join(sorted(accepted - {server_version}))})"
             if len(accepted) > 1 else "")
          + "."
        ),
        code="version_unsupported",
        status_code=422,
      )


async def idempotency_header(
  idempotency_key: str = Header(...),
) -> str:
  """Extract the Idempotency-Key header."""
  return idempotency_key


async def verify_simulation_secret(
  simulation_secret: str | None = Header(None, alias="Simulation-Secret"),
) -> None:
  """Verify the secret for simulation endpoints."""
  expected_secret = config.FLAGS.simulation_secret
  if not expected_secret:
    raise HTTPException(
      status_code=500, detail="Simulation secret not configured"
    )

  if not simulation_secret or simulation_secret != expected_secret:
    raise HTTPException(status_code=403, detail="Invalid Simulation Secret")


def get_fulfillment_service() -> FulfillmentService:
  """Dependency provider for FulfillmentService."""
  return FulfillmentService()


async def get_products_db() -> AsyncGenerator[AsyncSession, None]:
  """Dependency provider for Products DB session."""
  async with db.manager.products_session_factory() as session:
    yield session


async def get_transactions_db() -> AsyncGenerator[AsyncSession, None]:
  """Dependency provider for Transactions DB session."""
  async with db.manager.transactions_session_factory() as session:
    yield session


def get_checkout_service(
  request: Request,
  fulfillment_service: Annotated[
    FulfillmentService, Depends(get_fulfillment_service)
  ],
  products_session: Annotated[AsyncSession, Depends(get_products_db)],
  transactions_session: Annotated[AsyncSession, Depends(get_transactions_db)],
) -> CheckoutService:
  """Dependency provider for CheckoutService."""
  return CheckoutService(
    fulfillment_service,
    products_session,
    transactions_session,
    str(request.base_url),
  )


def get_cart_service(
  request: Request,
  products_session: Annotated[AsyncSession, Depends(get_products_db)],
  transactions_session: Annotated[AsyncSession, Depends(get_transactions_db)],
) -> CartService:
  """Dependency provider for CartService."""
  return CartService(
    products_session,
    transactions_session,
    str(request.base_url),
  )
