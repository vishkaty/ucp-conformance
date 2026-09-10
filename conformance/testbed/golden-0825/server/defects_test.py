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
    catches a malformed row before it ever reaches a live boot. Covers both
    "mutants" (oracle-graded) and "self_referenced_mutants" (graded by a check
    file's own predicate, PLAN-0825 SS B -- same arm/disarm shape, just not
    iterated by validate_golden_0825_battery.py's standing report gate)."""
    here = Path(__file__).resolve().parent
    config = json.loads((here / "defects_config.json").read_text())
    assert config["mutants"], "defects_config.json must ship at least one mutant"
    all_mutants = config["mutants"] + config.get("self_referenced_mutants", [])
    names = [m["name"] for m in all_mutants] + [f["name"] for f in config.get("fixture_only", [])]
    assert len(names) == len(set(names)), "mutant/fixture names must be unique"
    for m in all_mutants:
        assert m["route"]["method"] in ("GET", "POST", "PUT", "DELETE")
        assert m["route"]["path"].startswith("/")
        assert m["patch"], f"{m['name']} has an empty patch"
        for instr in m["patch"]:
            assert instr["op"] in ("drop", "set")
            assert isinstance(instr["path"], list) and instr["path"]
    for f in config.get("fixture_only", []):
        assert "body" in f
    # behavior rows (D3-01): data only -- a key, the checks that grade it, and
    # the rule it violates; never a patch or a route (those would make it a
    # patch mutant wearing the wrong label).
    behaviors = config.get("behavior_mutants", [])
    names += [b["name"] for b in behaviors]
    assert len(names) == len(set(names)), "behavior names must not collide with mutant/fixture names"
    for b in behaviors:
        assert isinstance(b["behavior"], str) and b["behavior"], f"{b['name']} has no behavior key"
        assert isinstance(b["checks"], list) and b["checks"], f"{b['name']} names no checks"
        assert b["violates"], f"{b['name']} does not say what it violates"
        assert "patch" not in b and "route" not in b, f"{b['name']} is a behavior row, not a patch"


def test_self_referenced_mutants_load_into_the_same_engine_as_mutants(tmp_path):
    """defects_config.json's self_referenced_mutants array is merged into
    DefectsEngine.mutants alongside the oracle-graded "mutants" array -- one
    arm/disarm mechanism, not a second code path (P-7)."""
    cfg = _config_file(tmp_path)
    cfg.write_text(json.dumps({
        "mutants": [{"name": "m1", "route": {"method": "GET", "path": "/a"},
                     "patch": [{"op": "drop", "path": ["x"]}]}],
        "self_referenced_mutants": [{"name": "m2", "route": {"method": "GET", "path": "/b"},
                                      "patch": [{"op": "drop", "path": ["y"]}]}],
    }))
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(tmp_path / "state.json"))
    assert set(engine.mutants.keys()) == {"m1", "m2"}


# ---------------------------------------------------------------------------
# behavior mutants (D3-01, decision 19): rows stay data; a server-side guard
# consults DefectsEngine.behavior_armed(key) at exactly one place per key.
# ---------------------------------------------------------------------------


def _behavior_config(tmp_path):
    path = tmp_path / "defects_config.json"
    path.write_text(json.dumps({
        "mutants": [],
        "behavior_mutants": [{
            "name": "negotiation_accept_any",
            "behavior": "negotiation.accept_any",
            "checks": ["negotiation.version_unsupported_error"],
            "violates": "NEG-001 -- an unadvertised version must be rejected",
        }],
    }))
    return path


def test_behavior_armed_reads_state(tmp_path):
    """behavior_armed(key) is False when nothing is armed, True only while the
    behavior row naming that key is armed (same hot-reloaded state file as the
    patch mutants), and a behavior row NEVER touches a response body:
    maybe_mutate stays byte-identical (object identity) while it is armed."""
    cfg = _behavior_config(tmp_path)
    state = tmp_path / "state.json"
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(state))

    assert engine.behavior_armed("negotiation.accept_any") is False

    defects.write_state(str(state), "negotiation_accept_any")
    assert engine.behavior_armed("negotiation.accept_any") is True
    assert engine.behavior_armed("some.other.key") is False
    body, fired = engine.maybe_mutate("POST", "/checkout-sessions", SAMPLE_DOC)
    assert body is SAMPLE_DOC  # identity: a behavior row is not a patch
    assert fired is None

    defects.write_state(str(state), None)
    assert engine.behavior_armed("negotiation.accept_any") is False
    # every consultation is recorded so the battery can tell "guard never ran"
    # (LOADER-BROKEN) apart from "guard ran and the checks did not flip".
    assert "negotiation.accept_any" in engine.consulted_keys()


def test_armed_behavior_row_is_identity_on_every_catalogued_route(tmp_path):
    """The object-identity proof extended to behavior rows (D3-01): while a
    behavior row is armed, maybe_mutate returns the EXACT input object for
    every (method, route) the real catalog knows -- a behavior row can only
    ever act through its guard, never through the patch path."""
    here = Path(__file__).resolve().parent
    real = json.loads((here / "defects_config.json").read_text())
    routes = {(m["route"]["method"], m["route"]["path"]) for m in real["mutants"]}
    assert routes
    cfg = _behavior_config(tmp_path)
    state = tmp_path / "state.json"
    defects.write_state(str(state), "negotiation_accept_any")
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(state))
    assert engine.behavior_armed("negotiation.accept_any") is True
    for method, path in sorted(routes):
        body, fired = engine.maybe_mutate(method, path, SAMPLE_DOC)
        assert body is SAMPLE_DOC, (method, path)
        assert fired is None, (method, path)


def test_behavior_armed_is_false_and_untracked_by_state_when_disabled(tmp_path):
    """Defects OFF (config_path=None, the normal boot): behavior_armed is False
    without ever reading a state file -- even one that names a behavior row --
    so a guard's conformant branch is the only branch, byte-identical to a
    build with no defects code. The consultation is still recorded (cheap,
    in-memory) but server.py never emits the header when disabled."""
    state = tmp_path / "state.json"
    defects.write_state(str(state), "negotiation_accept_any")
    engine = defects.DefectsEngine(config_path=None, state_path=str(state))
    assert engine.behavior_armed("negotiation.accept_any") is False
    assert engine.consulted_keys() == {"negotiation.accept_any"}


def test_consulted_keys_reset_when_arm_state_changes(tmp_path):
    """The battery reads consulted keys AFTER arming a row and driving its
    checks; a consultation that happened under a PREVIOUS arm state must not
    make an unwired row look wired. The record restarts on every arm change."""
    cfg = _behavior_config(tmp_path)
    state = tmp_path / "state.json"
    engine = defects.DefectsEngine(config_path=str(cfg), state_path=str(state))
    engine.behavior_armed("negotiation.accept_any")
    assert "negotiation.accept_any" in engine.consulted_keys()
    defects.write_state(str(state), "negotiation_accept_any")
    engine.behavior_armed("some.other.key")  # first consultation under the new state
    assert engine.consulted_keys() == {"some.other.key"}
