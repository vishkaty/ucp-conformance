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

"""Discovery routes for the UCP server."""

import json
import pathlib
import uuid
from fastapi import APIRouter
from fastapi import Request
from fastapi import Response
import webhook_signer

router = APIRouter()

PROFILE_TEMPLATE_PATH = pathlib.Path(__file__).parent / "discovery_profile.json"

# C3 supported_versions (D3-03, decision 18): the root profile's
# ucp.supported_versions maps 2026-04-08 to this LEAF -- a complete,
# self-contained 04-08 profile in the 04-08 document shape (bare: version/
# services/capabilities/payment_handlers at the top level, signing_keys[] for
# the webhook key -- ucp.json@2026-04-08 $defs.base requires top-level
# `version`), advertising checkout + order at version 2026-04-08 on every
# entry (OVR-075) and carrying NO supported_versions of its own (OVR-009).
# Its service endpoint is {{ENDPOINT}}/2026-04-08: a version-specific profile
# points at a version-specific endpoint, where server.py's
# VersionProjectionMiddleware negotiates 2026-04-08 for requests that carry
# no explicit version= (and projects both directions).
LEAF_VERSION = "2026-04-08"
LEAF_TEMPLATE_PATH = pathlib.Path(__file__).parent / f"discovery_profile_{LEAF_VERSION}.json"

# Profiles are stable, non-sensitive documents; the spec requires a cacheable
# response (overview.md, Discovery: profile caching): `public` with `max-age`
# of at least 60 seconds, and never `private`/`no-store`/`no-cache`.
PROFILE_CACHE_CONTROL = "public, max-age=3600"

# Generate a unique shop ID for this server instance
SHOP_ID = str(uuid.uuid4())


def _render(template_path: pathlib.Path, request: Request) -> dict:
  """Read a profile template and substitute {{ENDPOINT}} / {{SHOP_ID}}."""
  with template_path.open(encoding="utf-8") as f:
    template = f.read()
  profile_json = template.replace(
    "{{ENDPOINT}}", str(request.base_url).rstrip("/")
  ).replace("{{SHOP_ID}}", SHOP_ID)
  return json.loads(profile_json)


@router.get(
  "/.well-known/ucp",
  response_model=dict,
  summary="Get Merchant Profile",
)
async def get_merchant_profile(request: Request, response: Response):
  """Return the merchant profile and capabilities."""
  response.headers["Cache-Control"] = PROFILE_CACHE_CONTROL
  profile_data = _render(PROFILE_TEMPLATE_PATH, request)

  # Preserve the spec-required top-level `ucp` wrapper. The UCP discovery
  # profile schema (discovery/profile.json) defines `$defs.base.required =
  # ["ucp"]`, so the served body MUST be `{"ucp": {...}}`. Default
  # payment_handlers INSIDE the ucp object rather than at the document root.
  ucp = profile_data.setdefault("ucp", {})
  ucp.setdefault("payment_handlers", [])

  # Publish the webhook-signing public key so platforms can verify our
  # order-event deliveries (order.md, Webhook Signature Verification /
  # signatures.md, Key Discovery). profile.json's $defs.base places keys[]
  # at the TOP LEVEL of the served document, a SIBLING of `ucp` -- NOT
  # nested inside it, and NOT under the retired `signing_keys[]` name
  # (removed at 08-25, ucp#566). overview/index.md#L1262-1265: "When a
  # profile publishes signing keys, they MUST appear in the top-level
  # keys[] array -- the canonical UCP profile field that every UCP
  # verifier reads." This server's own verifier (ucp_signing._extract_keys)
  # resolves keys from exactly this location.
  jwk = webhook_signer.public_jwk()
  profile_data.setdefault("keys", []).append(jwk)

  return profile_data


@router.get(
  f"/.well-known/ucp/{LEAF_VERSION}",
  response_model=dict,
  summary=f"Get Merchant Profile ({LEAF_VERSION} leaf)",
)
@router.get(
  f"/{LEAF_VERSION}/.well-known/ucp",
  response_model=dict,
  summary=f"Get Merchant Profile ({LEAF_VERSION} leaf, base-URL alias)",
  include_in_schema=False,
)
async def get_merchant_profile_leaf(request: Request, response: Response):
  """The 2026-04-08 leaf profile named by the root's supported_versions.

  Served at the canonical supported_versions URI and, byte-identically, under
  a base-URL alias (`/2026-04-08/.well-known/ucp`) so a runner that derives
  discovery from a base URL (conformance/checks/merchant.py --server
  $G/2026-04-08) discovers the same leaf -- the leaf differential (D3-03).
  """
  response.headers["Cache-Control"] = PROFILE_CACHE_CONTROL
  leaf = _render(LEAF_TEMPLATE_PATH, request)
  # 04-08 key discovery location: profile-level signing_keys[] (the location
  # the 04-08 generation's verifiers read; the 08-25 root publishes keys[]).
  leaf.setdefault("signing_keys", []).append(webhook_signer.public_jwk())
  return leaf
