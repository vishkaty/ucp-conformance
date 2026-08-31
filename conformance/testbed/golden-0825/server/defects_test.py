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

"""defects_test.py — hermetic unit tests for defects.py (R11, PLAN-0825 SS C.4).

No server boot, no network, no oracle -- these run in well under a second and
are the PRECISE form of the "disabled mode is byte-identical" proof (R11 build
item 1): unlike an end-to-end capture across two server boots (which is
confounded by golden-0825's per-boot ephemeral webhook-signing key -- see
conformance/selfcheck/validate_golden_0825_battery.py's phase0 docstring for
why that end-to-end comparison can't be exact), this test calls maybe_mutate()
directly and asserts object identity / exact equality on a fixed input, so
there is no boot-to-boot volatility to confound the comparison.

Run:
  cd conformance/testbed/golden-0825/server && uv run --group dev pytest defects_test.py -v
"""
import copy
import json
import tempfile
from pathlib import Path

import defects


SAMPLE_DOC = {
    "ucp": {"version": "2026-08-25", "keys": [{"kid": "k1", "kty": "EC", "crv": "P-256"}]},
    "id": "chk_1",
    "line_items": [{"item": {"id": "bouquet_roses"}}],
}


def _config_file(tmp_path, mutants=None, fixture_only=None):
    path = tmp_path / "defects_config.json"
    path.write_text(json.dumps({"mutants": mutants or [], "fixture_only": fixture_only or []}))
    return path


# ---------------------------------------------------------------------------
# apply_patch: the pure mutation primitive
# ---------------------------------------------------------------------------


def test_apply_patch_drop():
    out = defects.apply_patch(SAMPLE_DOC, [{"op": "drop", "path": ["id"]}])
    assert "id" not in out
    assert "id" in SAMPLE_DOC  # apply_patch must not mutate its input


def test_apply_patch_drop_nested_array_index():
    out = defects.apply_patch(SAMPLE_DOC, [{"op": "drop", "path": ["ucp", "keys", 0, "crv"]}])
    assert "crv" not in out["ucp"]["keys"][0]
    assert "crv" in SAMPLE_DOC["ucp"]["keys"][0]  # input untouched


def test_apply_patch_set_existing_field():
    out = defects.apply_patch(SAMPLE_DOC, [{"op": "set", "path": ["id"], "value": "chk_MUTATED"}])
    assert out["id"] == "chk_MUTATED"


def test_apply_patch_set_injects_new_field():
    """`set` on a dict key that does not yet exist ADDS it -- this is how the
    JWK/C62/consent mutants inject fields the clean response never carries."""
    out = defects.apply_patch(SAMPLE_DOC, [{"op": "set", "path": ["totally_new_field"], "value": 42}])
    assert out["totally_new_field"] == 42
    assert "totally_new_field" not in SAMPLE_DOC


def test_apply_patch_unresolvable_path_is_a_noop_not_a_crash():
    """A path into a key that doesn't exist resolves to None at some hop and
    the instruction is silently skipped -- this is what makes a misconfigured
    mutant observable as LOADER-BROKEN (nothing changed) rather than a 500."""
    out = defects.apply_patch(SAMPLE_DOC, [{"op": "drop", "path": ["nope", "nested", "gone"]}])
    assert out == SAMPLE_DOC


def test_apply_patch_dotted_reverse_dns_key_is_one_segment_not_split():
    """The whole reason paths are JSON arrays, not dotted strings (see
    defects.py's module docstring): a UCP capability/purpose name like
    dev.ucp.shopping.cart IS the key, dots and all."""
    doc = {"ucp": {"capabilities": {"dev.ucp.shopping.cart": [{"version": "2026-08-25"}]}}}
    out = defects.apply_patch(doc, [
        {"op": "set", "path": ["ucp", "capabilities", "dev.ucp.shopping.cart"], "value": "not-an-array"}
    ])
    assert out["ucp"]["capabilities"]["dev.ucp.shopping.cart"] == "not-an-array"


# ---------------------------------------------------------------------------
# DefectsEngine.maybe_mutate: the byte-identity proof (R11 build item 1)
# ---------------------------------------------------------------------------


def test_disabled_engine_returns_input_untouched(tmp_path):
    """config_path=None (the literal default -- what a normal boot passes) ->
    enabled=False -> maybe_mutate returns the EXACT SAME OBJECT, not a copy."""
    engine = defects.DefectsEngine(config_path=None, state_path=None)
    assert engine.enabled is False
    body, fired = engine.maybe_mutate("GET", "/.well-known/ucp", SAMPLE_DOC)
    assert body is SAMPLE_DOC  # identity, not just equality: proves zero processing
    assert fired is None


def test_enabled_but_unarmed_returns_input_untouched(tmp_path):
    """config_path SET but no state file written (armed=None) -> same
    guarantee as fully disabled: the object comes back untouched."""
    cfg = _config_file(tmp_path, mutants=[{
        "name": "m1", "route": {"method": "GET", "path": "/.well-known/ucp"},
        "patch": [{"op": "drop", "path": ["id"]}],
    }])
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(tmp_path / "state.json"))
    assert engine.enabled is True
    body, fired = engine.maybe_mutate("GET", "/.well-known/ucp", SAMPLE_DOC)
    assert body is SAMPLE_DOC
    assert fired is None


def test_enabled_armed_but_route_mismatch_returns_input_untouched(tmp_path):
    """Armed with a real mutant, but the request's (method, route) doesn't
    match that mutant's declared route -> untouched, same as unarmed. This is
    what keeps every OTHER endpoint byte-identical while one specific mutant
    is armed mid-battery."""
    cfg = _config_file(tmp_path, mutants=[{
        "name": "m1", "route": {"method": "GET", "path": "/.well-known/ucp"},
        "patch": [{"op": "drop", "path": ["id"]}],
    }])
    state = tmp_path / "state.json"
    defects.write_state(str(state), "m1")
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(state))
    body, fired = engine.maybe_mutate("POST", "/checkout-sessions", SAMPLE_DOC)
    assert body is SAMPLE_DOC
    assert fired is None


def test_enabled_armed_matching_route_mutates(tmp_path):
    cfg = _config_file(tmp_path, mutants=[{
        "name": "m1", "route": {"method": "GET", "path": "/.well-known/ucp"},
        "patch": [{"op": "drop", "path": ["id"]}],
    }])
    state = tmp_path / "state.json"
    defects.write_state(str(state), "m1")
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(state))
    body, fired = engine.maybe_mutate("GET", "/.well-known/ucp", SAMPLE_DOC)
    assert fired == "m1"
    assert "id" not in body
    assert "id" in SAMPLE_DOC  # still didn't touch the input


def test_disarm_after_arm_restores_untouched(tmp_path):
    """The hot-reload arm/disarm cycle the battery runner actually uses:
    write armed, mutate; write armed=None, untouched again -- same engine
    instance, same config load, no reboot."""
    cfg = _config_file(tmp_path, mutants=[{
        "name": "m1", "route": {"method": "GET", "path": "/.well-known/ucp"},
        "patch": [{"op": "drop", "path": ["id"]}],
    }])
    state = tmp_path / "state.json"
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(state))

    defects.write_state(str(state), "m1")
    body, fired = engine.maybe_mutate("GET", "/.well-known/ucp", SAMPLE_DOC)
    assert fired == "m1" and "id" not in body

    defects.write_state(str(state), None)
    body, fired = engine.maybe_mutate("GET", "/.well-known/ucp", SAMPLE_DOC)
    assert fired is None
    assert body is SAMPLE_DOC


def test_missing_state_file_is_treated_as_unarmed(tmp_path):
    """A battery run that hasn't written a state file yet (or one that was
    cleaned up mid-run) must fail toward the SAFE (unarmed) reading, not throw
    and not accidentally arm something."""
    cfg = _config_file(tmp_path, mutants=[{
        "name": "m1", "route": {"method": "GET", "path": "/.well-known/ucp"},
        "patch": [{"op": "drop", "path": ["id"]}],
    }])
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(tmp_path / "does_not_exist.json"))
    body, fired = engine.maybe_mutate("GET", "/.well-known/ucp", SAMPLE_DOC)
    assert fired is None and body is SAMPLE_DOC


def test_corrupt_state_file_is_treated_as_unarmed(tmp_path):
    """Same fail-safe direction for a torn/partial write of the state file
    (a battery runner crashing mid-write is the realistic trigger)."""
    cfg = _config_file(tmp_path, mutants=[{
        "name": "m1", "route": {"method": "GET", "path": "/.well-known/ucp"},
        "patch": [{"op": "drop", "path": ["id"]}],
    }])
    state = tmp_path / "state.json"
    state.write_text("{not valid json")
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(state))
    body, fired = engine.maybe_mutate("GET", "/.well-known/ucp", SAMPLE_DOC)
    assert fired is None and body is SAMPLE_DOC


# ---------------------------------------------------------------------------
# the real catalog: every mutant's patch is at least mechanically well-formed
# ---------------------------------------------------------------------------


def test_real_catalog_loads_and_every_mutant_has_a_route_and_patch():
    """A schema-lite sanity check on the actual shipped defects_config.json --
    catches a malformed row before it ever reaches a live boot."""
    here = Path(__file__).resolve().parent
    config = json.loads((here / "defects_config.json").read_text())
    assert config["mutants"], "defects_config.json must ship at least one mutant"
    names = [m["name"] for m in config["mutants"]] + [f["name"] for f in config.get("fixture_only", [])]
    assert len(names) == len(set(names)), "mutant/fixture names must be unique"
    for m in config["mutants"]:
        assert m["route"]["method"] in ("GET", "POST", "PUT", "DELETE")
        assert m["route"]["path"].startswith("/")
        assert m["patch"], f"{m['name']} has an empty patch"
        for instr in m["patch"]:
            assert instr["op"] in ("drop", "set")
            assert isinstance(instr["path"], list) and instr["path"]
    for f in config.get("fixture_only", []):
        assert "body" in f
