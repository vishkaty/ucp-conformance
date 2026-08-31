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

"""Test-only fixture-echo route for R11's defect battery (PLAN-0825 SS C.4).

Every mutant this battery ships targets a field this golden actually serves on
its real business routes -- EXCEPT one: the maxProperties-family SDK-drop mutant
(python-sdk#90/#91) targets `common/types/location_serves.json`, and this golden
honestly does NOT implement the Location Search/Lookup capability that schema
belongs to (STATUS.md, "Honestly NOT advertised"). Grafting a location_serves-
shaped fixture onto an unrelated business response would not even be checked by
the oracle (it would just be an unknown extra property the enclosing schema's
`additionalProperties` policy ignores) -- so it would prove nothing.

Rather than fabricate a fake Location capability just to give one mutant a
host, this route does what `/testing/simulate-shipping/{id}` already does for a
different purpose: serve a small, explicitly test-only, secret-gated fixture
body over real HTTP, so the schema-constraint family is still WIRE-level (a
real response an oracle client validates), not merely an in-memory fixture. It:
  - is never advertised in the discovery profile (not a capability),
  - 404s whenever defects mode is off (matches "off by default" everywhere
    else in this battery -- there is no code path where this route is live
    outside an explicit defects-mode boot),
  - requires the same Simulation-Secret header as the other testing/* routes,
  - only serves fixture_only entries from the loaded defects_config.json, never
    arbitrary caller input.
"""
from typing import Annotated, Any

import dependencies
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Path
import server_state

router = APIRouter()


@router.get(
  "/testing/defect-fixtures/{key}",
  response_model=dict[str, Any],
  operation_id="get_defect_fixture",
  dependencies=[Depends(dependencies.verify_simulation_secret)],
)
async def get_defect_fixture(
  key: Annotated[str, Path(...)],
) -> dict[str, Any]:
  """Serve a fixture_only body from defects_config.json by name. 404 when
  defects mode is off or the key isn't a known fixture -- a battery runner
  probing this route against a normal (non-defects) boot gets the same 404 a
  business capability that was never implemented would give, not a 500 or a
  silently-empty 200."""
  engine = server_state.defects_engine()
  if not engine.enabled:
    raise HTTPException(status_code=404, detail="defects mode is not enabled")
  entry = engine.fixtures.get(key)
  if entry is None:
    raise HTTPException(status_code=404, detail=f"no defect fixture named {key!r}")
  return entry["body"]
