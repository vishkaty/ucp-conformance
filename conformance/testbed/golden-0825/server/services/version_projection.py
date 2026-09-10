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

"""version_projection.py -- serve 2026-04-08 from the 2026-08-25 golden (D3-03).

Decision 18: ONE server, version projection -- not a second server. A request
negotiated at 2026-04-08 (UCP-Agent version=, resolved by dependencies.
validate_ucp_headers against {ucp.version} ∪ supported_versions) is served by
the same 08-25 business logic, with the wire shape projected at the edge:

  request  (04-08 -> 08-25 model shape, before validation):
    - fulfillment.methods[].destinations[] gain the `type` discriminator the
      08-25 shipping_destination requires (04-08 has no such field);
    - buyer.consent booleans (04-08: marketing/analytics/preferences/
      sale_of_data) become 08-25 consent_purpose objects keyed by the
      well-known reverse-DNS purpose ids (source "platform": the value IS a
      platform-captured buyer decision, transported in a 04-08 shape).
  response (08-25 -> 04-08 wire shape):
    - ucp.version (and every dev.ucp.* entry version the response echoes)
      becomes 2026-04-08;
    - destinations[].type is dropped (04-08 shipping_destination is
      postal_address + id, oneOf-discriminated by shape, not by a field);
    - buyer.consent purpose objects collapse to their `granted` boolean under
      the 04-08 key.

Every delta is a hand-written, named, TESTED deviation between the two pinned
schema generations (leaf differential: the 04-08 population must grade the
projection clean). The two response deltas carry a behavior guard each
(decision 19) so the battery can prove the projection is load-bearing:
  projection.leak_destination_type  -- row `projection_leaks_type`
  projection.consent_objects        -- row `projection_consent_objects_on_0408`
"""
from __future__ import annotations

import copy
from typing import Any

LEAF_VERSION = "2026-04-08"

# 04-08 consent key -> 08-25 well-known purpose id (buyer_consent.json, both pins).
CONSENT_0408_TO_0825 = {
    "marketing": "dev.ucp.consent.marketing",
    "analytics": "dev.ucp.consent.analytics",
    "preferences": "dev.ucp.consent.preferences",
    "sale_of_data": "dev.ucp.consent.sale_or_sharing",
}
CONSENT_0825_TO_0408 = {v: k for k, v in CONSENT_0408_TO_0825.items()}

# fulfillment method type -> the 08-25 destination `type` discriminator a
# platform on that generation would have sent (shipping_destination.json const).
DESTINATION_TYPE_BY_METHOD = {
    "shipping": "shipping_address",
}


def _methods(body: dict) -> list:
  ful = body.get("fulfillment")
  if not isinstance(ful, dict):
    return []
  methods = ful.get("methods")
  return methods if isinstance(methods, list) else []


def _consent(body: dict) -> dict | None:
  buyer = body.get("buyer")
  if not isinstance(buyer, dict):
    return None
  consent = buyer.get("consent")
  return consent if isinstance(consent, dict) else None


def project_request(body: Any, version: str | None) -> Any:
  """04-08 request shape -> the shape the 08-25 models validate. Identity for
  any other version (and for non-object bodies)."""
  if version != LEAF_VERSION or not isinstance(body, dict):
    return body
  out = copy.deepcopy(body)
  for method in _methods(out):
    if not isinstance(method, dict):
      continue
    default_type = DESTINATION_TYPE_BY_METHOD.get(method.get("type"))
    for dest in method.get("destinations") or []:
      if isinstance(dest, dict) and default_type and "type" not in dest:
        dest["type"] = default_type
  consent = _consent(out)
  if consent is not None:
    projected = {}
    for key, value in consent.items():
      purpose = CONSENT_0408_TO_0825.get(key)
      if purpose and isinstance(value, bool):
        projected[purpose] = {
            "granted": value,
            "source": "platform",
            "description": f"{key} consent (2026-04-08 request, projected)",
        }
      else:
        projected[key] = value  # already 08-25-shaped, or unknown: pass through
    out["buyer"]["consent"] = projected
  return out


def project_response(body: Any, version: str | None, engine) -> Any:
  """08-25 wire body -> 04-08 wire shape. Identity for any other version.
  `engine` is the DefectsEngine (server_state.defects_engine()): the two
  behavior guards below are the ONLY code that consults their keys."""
  if version != LEAF_VERSION or not isinstance(body, dict):
    return body
  out = copy.deepcopy(body)
  ucp = out.get("ucp")
  if isinstance(ucp, dict):
    ucp["version"] = LEAF_VERSION
    caps = ucp.get("capabilities")
    if isinstance(caps, dict):
      for entries in caps.values():
        for entry in entries if isinstance(entries, list) else []:
          if isinstance(entry, dict) and "version" in entry:
            entry["version"] = LEAF_VERSION
  if not engine.behavior_armed("projection.leak_destination_type"):
    for method in _methods(out):
      if not isinstance(method, dict):
        continue
      for dest in method.get("destinations") or []:
        if isinstance(dest, dict):
          dest.pop("type", None)
  if not engine.behavior_armed("projection.consent_objects"):
    consent = _consent(out)
    if consent is not None:
      projected = {}
      for purpose, value in consent.items():
        key = CONSENT_0825_TO_0408.get(purpose)
        if key and isinstance(value, dict) and "granted" in value:
          projected[key] = bool(value["granted"])
        # purposes 04-08 cannot express are dropped from the 04-08 view
      out["buyer"]["consent"] = projected
  return out
