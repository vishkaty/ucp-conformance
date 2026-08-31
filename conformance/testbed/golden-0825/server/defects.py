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

"""defects.py — config-driven defect-injection ("mutant") mode for golden-0825.

R11 (PLAN-0825 §C.4 / §8-L3.4): the golden ships a defect-injection mode so future
08-25 checks can be kill-tested against wire-level defects, without inventing
per-scenario server code (the walls doctrine: variants are DATA). This module is
the ONE choke point: it is wired into server.py as a single ASGI middleware, and
every mutant is a row in defects_config.json, never a code branch.

Design:
  - OFF BY DEFAULT. `--defects_config` is unset (None) unless a caller explicitly
    passes it (serve_golden_0825.sh only does so when DEFECTS_CONFIG is exported).
    When off, `maybe_mutate()` returns its input completely untouched -- the exact
    same object, zero bytes read or parsed -- so the normal serve path is
    byte-identical to a build that never linked this module. Proven in
    defects_test.py and in the battery runner's own disabled-mode capture.
  - HOT-RELOADABLE ARM. Which mutant (if any) is active is read from a tiny state
    file (`--defects_state_file`) on every matching request -- not baked in at
    boot -- so a battery can arm/disarm mutants one at a time against a single
    server instance instead of rebooting per mutant (PLAN-0825 §C.4's "or
    hot-reloads config" option; much faster than a boot-per-mutant battery and
    still fully config-driven).
  - MUTATION GRAMMAR mirrors (does not import) conformance/checks/engine.py's
    drop/set vocabulary, because golden-0825's boot-determinism guarantee
    (STATUS.md §Boot determinism) means the server process must not gain a
    runtime dependency on the separate conformance harness venv. Paths are JSON
    arrays of keys/indices (not dotted strings), which sidesteps the one
    complication a dotted grammar would hit here: this corpus's field names are
    themselves reverse-DNS dotted strings (`dev.ucp.shopping.cart`), so a literal
    "." path separator would be ambiguous. See apply_patch().
"""
from __future__ import annotations

import copy
import json
import pathlib
import time
from typing import Any

# ---------------------------------------------------------------------------
# patch grammar: JSON-array paths, two ops (drop / set). `set` on a dict key
# that does not yet exist ADDS it -- so `set` alone covers both "corrupt an
# existing field" and "inject a field that isn't naturally served" (e.g. a
# discovery profile that doesn't yet publish signing keys).
# ---------------------------------------------------------------------------


def _get_parent(doc: Any, path: list) -> Any:
  cur = doc
  for key in path[:-1]:
    if isinstance(cur, list):
      if not isinstance(key, int) or not (0 <= key < len(cur)):
        return None
      cur = cur[key]
    elif isinstance(cur, dict):
      cur = cur.get(key)
    else:
      return None
    if cur is None:
      return None
  return cur


def apply_patch(doc: Any, patch: list[dict]) -> Any:
  """Apply a list of {"op": "drop"|"set", "path": [...], "value": ...} ops to a
  DEEP COPY of doc; returns the mutated copy. A path segment that fails to
  resolve is a no-op for that single instruction (not an error) -- the battery
  runner's Layer-1 "did it actually fire" check is what catches a misconfigured
  mutant; apply_patch staying permissive here is what makes that check possible
  (a hard exception here would just crash the request instead of silently
  surviving, which is a different and less informative failure mode)."""
  out = copy.deepcopy(doc)
  for instr in patch:
    path = instr["path"]
    if not path:
      continue
    parent = _get_parent(out, path)
    last = path[-1]
    op = instr["op"]
    if op == "drop":
      if isinstance(parent, dict):
        parent.pop(last, None)
      elif isinstance(parent, list) and isinstance(last, int) and 0 <= last < len(parent):
        parent.pop(last)
    elif op == "set":
      if isinstance(parent, dict):
        parent[last] = copy.deepcopy(instr["value"])
      elif isinstance(parent, list) and isinstance(last, int) and 0 <= last < len(parent):
        parent[last] = copy.deepcopy(instr["value"])
    else:
      raise ValueError(f"defects.py: unknown patch op {op!r}")
  return out


# ---------------------------------------------------------------------------
# engine: route matching + hot-reloaded arm state
# ---------------------------------------------------------------------------


class DefectsEngine:
  """Loaded once at boot from --defects_config (a static file: the mutant
  catalog). The ARMED mutant, by contrast, is re-read from --defects_state_file
  on every request that could possibly match one (cheap: one small JSON file
  stat+read), so a battery run can arm/disarm without restarting the server."""

  def __init__(self, config_path: str | None, state_path: str | None):
    self.enabled = bool(config_path)
    self.config_path = pathlib.Path(config_path) if config_path else None
    self.state_path = pathlib.Path(state_path) if state_path else None
    self.mutants: dict[str, dict] = {}
    self.fixtures: dict[str, dict] = {}
    if self.enabled:
      raw = json.loads(self.config_path.read_text(encoding="utf-8"))
      self.mutants = {m["name"]: m for m in raw.get("mutants", [])}
      self.fixtures = {f["name"]: f for f in raw.get("fixture_only", [])}

  def _armed_name(self) -> str | None:
    if not self.enabled or not self.state_path or not self.state_path.exists():
      return None
    try:
      state = json.loads(self.state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
      return None
    return state.get("armed")

  def armed_mutant(self) -> dict | None:
    """The full config row for the currently-armed mutant, or None. Exposed so
    the fixture-echo route (routes/defect_fixtures.py) can look up a
    fixture_only entry by the same arm state without duplicating state I/O."""
    name = self._armed_name()
    if name is None:
      return None
    return self.mutants.get(name) or self.fixtures.get(name)

  def maybe_mutate(self, method: str, route_template: str, body: Any) -> tuple[Any, str | None]:
    """If disabled, or no mutant is armed, or the armed mutant doesn't target
    this (method, route_template), return (body, None) UNCHANGED -- literally
    the same object, no copy, no parse -- so the disabled/unarmed path never
    diverges from a build with no defects code at all. Otherwise returns
    (mutated_body, mutant_name)."""
    if not self.enabled:
      return body, None
    mutant = self.armed_mutant()
    if mutant is None or "route" not in mutant:
      return body, None
    route = mutant["route"]
    if route.get("method") != method or route.get("path") != route_template:
      return body, None
    if body is None:
      return body, None
    return apply_patch(body, mutant["patch"]), mutant["name"]


def write_state(state_path: str, armed: str | None) -> None:
  """Battery-runner-side helper: arm/disarm by rewriting the tiny state file.
  A plain write (not atomic-rename) is fine here -- this is single-writer
  test-harness scaffolding, not a production consistency primitive."""
  pathlib.Path(state_path).write_text(
      json.dumps({"armed": armed, "written_at": time.time()}), encoding="utf-8"
  )
