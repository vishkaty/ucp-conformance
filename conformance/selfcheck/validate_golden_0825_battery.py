#!/usr/bin/env python3
"""
validate_golden_0825_battery.py — R11: the golden-0825 mutant battery
(PLAN-0825 SS C.4 / SS8-L3.4).

This is the "selfcheck idiom" gate for the golden's defect-injection mode: it boots
golden-0825 itself (own port range, 8199 -- never 8182, which selftest.sh's main
golden owns), arms every mutant in defects_config.json one at a time against a single
running instance (hot-reload, not a boot-per-mutant), and proves three things per
mutant:

  1. FIRED    -- the armed response actually differs from clean at exactly the path
                 the mutant's patch names (not "differs somewhere" -- exactly there).
                 A mutant that does NOT fire is a BROKEN DEFECT LOADER: configured but
                 never served. That is always a hard failure of this gate, unconditionally
                 (kill-test of the battery itself, R11 build item 5 -- see --selftest).
  2. CAUGHT   -- once fired, the mutant's declared oracle call rejects the mutated body.
                 A fired-but-uncaught mutant is an UNCOVERED MUTANT: reported by name,
                 honestly, never silently dropped from the report (P-1). It is ALSO a
                 hard failure of this gate unless explicitly acknowledged in
                 defects_config.json's top-level "acknowledged_gaps" (same discipline as
                 conformance/ci/known_oracle_divergences.json: a real finding gets named
                 in the same commit that discovers it, not hidden and not silently
                 blocking forever).
  3. RESTORED -- after disarming, the SAME request path is clean again (oracle: ok=True).
                 Proves disarm genuinely restores the normal serve path, not just that
                 arming did something.

BEHAVIOR rows (defects_config.json `behavior_mutants[]`, D3-01 / decision 19) carry no
patch: `{name, behavior: "<key>", checks[], violates}`. Exactly one guard per key in
server code asks `DefectsEngine.behavior_armed(key)` and takes the violating branch
while the row is armed. This runner grades such a row by the CONFORMANCE CHECKS it
names (`checks[]` = check ids from conformance/checks/area_*.py, plus the runner's own
`battery.*` selftest checks): KILLED when every named check is CLEAN before arming,
DEVIATION armed, and CLEAN again disarmed; SURVIVED when armed and not all deviate;
LOADER-BROKEN when the server's `x-defects-consulted` header shows NO guard consulted
the row's key while it was armed (an unwired key -- the row is data nobody reads).

Disabled-mode byte-identity (R11 build item 1) is proved separately and more precisely
by conformance/testbed/golden-0825/server/defects_test.py (a hermetic unit test on
defects.py itself: given DEFECTS OFF, maybe_mutate() returns the exact same object,
no copy, no parse). This runner additionally captures one end-to-end confirmation: a
normal (defects-config-unset) boot's responses for every core route, byte-for-byte,
BEFORE the defects-mode boot -- see phase 0 below.

Exit codes: 0 = every mutant fired+caught, disabled-mode proof holds, no gap;
1 = a mutant didn't fire, wasn't caught (and isn't acknowledged), or the disabled-mode
proof failed; 2 = the ucp-schema oracle binary/vendor tree isn't available (honest
skip, mirrors every other oracle-backed gate in this suite).

Usage:
    python3 conformance/selfcheck/validate_golden_0825_battery.py
    python3 conformance/selfcheck/validate_golden_0825_battery.py --selftest
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
SELF = ROOT / "conformance" / "selfcheck"
GOLDEN_DIR = ROOT / "conformance" / "testbed" / "golden-0825"
SERVER_DIR = GOLDEN_DIR / "server"
DEFAULT_CONFIG = SERVER_DIR / "defects_config.json"

sys.path.insert(0, str(SELF))
import schema_oracle as so  # noqa: E402

sys.path.insert(0, str(SERVER_DIR))
import defects  # noqa: E402  (pure stdlib module; see its own docstring on why this
                              # runner imports it directly rather than reimplementing
                              # apply_patch/_get_parent a third time)

CHECKS_DIR = ROOT / "conformance" / "checks"
sys.path.insert(0, str(CHECKS_DIR))
import engine as chk_engine  # noqa: E402  (Check / run_check / fetch: the SAME runner
                             # merchant.py uses, so a behavior row is graded by the
                             # real check, not by a battery-local reimplementation)
from verdict_gate import CLEAN, DEVIATION  # noqa: E402

PORT = int(os.environ.get("GOLDEN_0825_BATTERY_PORT", "8199"))
BASE = f"http://localhost:{PORT}"
SIM_SECRET = "battery-secret"
VERSION = "2026-08-25"


class OracleUnavailable(RuntimeError):
    pass


def _require_oracle():
    base = so.SCHEMA_BASE.get(VERSION)
    if base is None or not base.exists():
        raise OracleUnavailable(f"conformance/.vendor/ucp-{VERSION} not fetched")
    if not so.BIN.exists():
        raise OracleUnavailable(f"ucp-schema validator not built at {so.BIN}")


# ---------------------------------------------------------------------------
# server lifecycle
# ---------------------------------------------------------------------------


class Golden:
    def __init__(self, db_dir, defects_config=None, state_file=None):
        self.db_dir = pathlib.Path(db_dir)
        self.defects_config = defects_config
        self.state_file = state_file

    def start(self):
        env = dict(os.environ)
        env["PORT"] = str(PORT)
        env["DB_DIR"] = str(self.db_dir)
        env["SIM_SECRET"] = SIM_SECRET
        if self.defects_config:
            env["DEFECTS_CONFIG"] = str(self.defects_config)
        else:
            env.pop("DEFECTS_CONFIG", None)
        if self.state_file:
            env["DEFECTS_STATE_FILE"] = str(self.state_file)
        else:
            env.pop("DEFECTS_STATE_FILE", None)
        serve = GOLDEN_DIR / "serve_golden_0825.sh"
        result = subprocess.run([str(serve)], env=env, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"serve_golden_0825.sh failed (exit {result.returncode}):\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )

    def stop(self):
        env = dict(os.environ)
        env["PORT"] = str(PORT)
        env["DB_DIR"] = str(self.db_dir)
        stop = GOLDEN_DIR / "stop_golden_0825.sh"
        subprocess.run([str(stop)], env=env, capture_output=True, text=True, timeout=60)


def arm(state_file, name):
    if state_file:
        defects.write_state(str(state_file), name)


# ---------------------------------------------------------------------------
# HTTP helpers (mirrors smoke/test_golden_0825_smoke.py's ucp_headers())
# ---------------------------------------------------------------------------


def ucp_headers():
    suffix = uuid.uuid4().hex[:8]
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": f"idem-{suffix}",
        "Request-Id": f"req-{suffix}",
        "Request-Signature": f"sig-{suffix}",
        "UCP-Agent": 'profile="http://localhost:9/.well-known/ucp"; version="2026-08-25"',
        "Simulation-Secret": SIM_SECRET,
    }


def http(method, path, body=None, expect_json=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=ucp_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if expect_json and raw else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def _fulfillment_block():
    return {
        "methods": [
            {
                "id": "method_1", "type": "shipping", "line_item_ids": [],
                "destinations": [{"id": "dest_1", "type": "shipping_address", "address_country": "US"}],
                "selected_destination_id": "dest_1",
                "groups": [{"id": "group_1", "line_item_ids": [], "selected_option_id": "std-ship"}],
            }
        ]
    }


def _new_checkout():
    st, body = http("POST", "/checkout-sessions", {
        "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
        "fulfillment": _fulfillment_block(),
    })
    assert st == 201, f"fixture checkout create failed: {st} {body}"
    return body


def _new_cart():
    st, body = http("POST", "/carts", {"line_items": [{"item": {"id": "pot_ceramic"}, "quantity": 2}]})
    assert st == 201, f"fixture cart create failed: {st} {body}"
    return body


def _completed_order_id():
    checkout = _new_checkout()
    st, completed = http("POST", f"/checkout-sessions/{checkout['id']}/complete", {
        "payment": {"instruments": [{
            "id": "instr_1", "handler_id": "mock_payment_handler", "type": "card",
            "display": {"brand": "Visa", "last_digits": "1234"},
            "credential": {"type": "token", "token": "success_token"},
        }]},
        "risk_signals": {},
    })
    assert st == 200, f"fixture order-completion failed: {st} {completed}"
    order_id = completed["order"]["id"]
    # P3 wave 3: also simulate a shipment so fulfillment.events[] is non-empty --
    # the ROUTE-keyed fixture for /orders/{id}, not a per-mutant special case
    # (the walls doctrine): a bare create->complete order has expectations[] but
    # ZERO events (services/checkout_service.py's ship_order is the only path in
    # this golden that ever appends one), so any mutant targeting a field under
    # fulfillment.events[0] (order-event-drop-occurred-at) would be silently a
    # no-op against an empty array -- defects.py's apply_patch() is documented
    # permissive on an unresolvable path (see defects.py's own docstring), so a
    # thin fixture doesn't error, it just never fires. Populating events[] here
    # makes the canonical order fixture representative of a real shipped order
    # for EVERY order-surface mutant, not just this one.
    st_ship, _ = http("POST", f"/testing/simulate-shipping/{order_id}")
    assert st_ship == 200, f"fixture order-shipping-simulation failed: {st_ship}"
    return order_id


def perform(route):
    """Issue the exact request a mutant's route names, using a freshly-created
    resource where the op needs one to exist."""
    method, path = route["method"], route["path"]
    if path == "/.well-known/ucp" or path.startswith("/.well-known/ucp/"):
        return http("GET", path, expect_json=True)   # root profile or a version leaf
    if path == "/checkout-sessions":
        return http("POST", path, {
            "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
            "fulfillment": _fulfillment_block(),
        })
    if path == "/checkout-sessions/{id}":
        checkout = _new_checkout()
        if method == "GET":
            return http("GET", f"/checkout-sessions/{checkout['id']}")
        return http("PUT", f"/checkout-sessions/{checkout['id']}", {
            "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 2}],
            "fulfillment": _fulfillment_block(),
        })
    if path == "/checkout-sessions/{id}/complete":
        checkout = _new_checkout()
        return http("POST", f"/checkout-sessions/{checkout['id']}/complete", {
            "payment": {"instruments": [{
                "id": "instr_1", "handler_id": "mock_payment_handler", "type": "card",
                "display": {"brand": "Visa", "last_digits": "1234"},
                "credential": {"type": "token", "token": "success_token"},
            }]},
            "risk_signals": {},
        })
    if path == "/checkout-sessions/{id}/cancel":
        checkout = _new_checkout()
        return http("POST", f"/checkout-sessions/{checkout['id']}/cancel")
    if path == "/carts":
        return http("POST", path, {"line_items": [{"item": {"id": "pot_ceramic"}, "quantity": 2}]})
    if path == "/carts/{id}":
        cart = _new_cart()
        if method == "GET":
            return http("GET", f"/carts/{cart['id']}")
        return http("PUT", f"/carts/{cart['id']}", {"line_items": [{"item": {"id": "pot_ceramic"}, "quantity": 3}]})
    if path == "/carts/{id}/cancel":
        cart = _new_cart()
        return http("POST", f"/carts/{cart['id']}/cancel")
    if path == "/orders/{id}":
        order_id = _completed_order_id()
        return http("GET", f"/orders/{order_id}")
    if path.startswith("/testing/defect-fixtures/"):
        return http("GET", path)
    raise ValueError(f"validate_golden_0825_battery.py: no fixture-builder for route {route}")


# ---------------------------------------------------------------------------
# oracle dispatch
# ---------------------------------------------------------------------------


def run_oracle(oracle_cfg, body):
    kind = oracle_cfg["kind"]
    if kind == "profile":
        return so.validate_profile(body, version=VERSION, role=oracle_cfg.get("role", "business"))
    if kind == "root":
        return so.validate_root(body, oracle_cfg["schema"], op=oracle_cfg.get("op", "read"),
                                 version=VERSION, direction="response")
    if kind == "against":
        return so.validate_against(body, oracle_cfg["schema"], oracle_cfg["def"],
                                    op=oracle_cfg.get("op", "read"), version=VERSION, direction="response")
    raise ValueError(f"unknown oracle kind {kind!r}")


# ---------------------------------------------------------------------------
# fired-check: did the patch actually land, exactly where it says it would
# ---------------------------------------------------------------------------


def patch_applied(body, patch, before=None):
    """True iff every instruction in `patch` is observably true of `body`: a
    'set' path holds exactly instr['value']; a 'drop' path's key is absent from
    its parent (or the parent itself is already absent -- also a valid 'gone').
    Reuses defects._get_parent so this check and the server's own apply_patch
    agree on what a path means (no second path-walker to drift out of sync).

    `before` (the pre-arm clean body, when the caller has one) fixes a real
    blind spot for `drop` on a LIST INDEX that isn't the last element: dropping
    index N shifts every later element down by one, so the mutated list still
    has *something* at index N -- a naive `0 <= N < len(parent)` post-hoc check
    on the armed body alone would misread that shift as "the drop never fired"
    (found live 2026-09-01 while converting TOT-005, whose mutant AT THAT TIME
    dropped totals[0] in a 3+-element array: LOADER-BROKEN even though the
    server-side apply_patch -- proven correct by every OTHER mutant here --
    genuinely removed it. That mutant was subsequently rewritten as a
    whole-array `set`, so NO entry in defects_config.json currently takes this
    branch; it is kept because it fails closed and the next index-drop mutant
    will need it). With `before` available, the mechanical proof a drop
    fired is comparing the SAME parent array's length before vs. after: exactly
    one shorter, and only that. This does not need to compare element values
    (fragile under duplicates); apply_patch is deterministic and this is the
    only instruction touching the path, so a length delta of exactly -1 is
    conclusive. Without `before` (a caller that has no clean-body reference),
    the check falls back to the prior index-presence heuristic, which is exact
    for a LAST-index drop and only ever under-fires (reports LOADER-BROKEN
    instead of KILLED) for a non-last index -- fails closed, never a silent
    false KILLED."""
    for instr in patch:
        path = instr["path"]
        parent = defects._get_parent(body, path)
        last = path[-1]
        if instr["op"] == "set":
            if not isinstance(parent, (dict, list)):
                return False
            actual = parent.get(last) if isinstance(parent, dict) else (
                parent[last] if isinstance(last, int) and 0 <= last < len(parent) else None)
            if actual != instr["value"]:
                return False
        elif instr["op"] == "drop":
            if parent is None:
                continue  # already absent upstream -- vacuously dropped
            if isinstance(parent, dict) and last in parent:
                return False
            if isinstance(parent, list) and isinstance(last, int):
                if before is not None:
                    before_parent = defects._get_parent(before, path)
                    if isinstance(before_parent, list) and 0 <= last < len(before_parent):
                        if len(parent) != len(before_parent) - 1:
                            return False
                        continue  # length-delta proof above supersedes the index check below
                if 0 <= last < len(parent):
                    return False
    return True


# ---------------------------------------------------------------------------
# the battery
# ---------------------------------------------------------------------------


def run_mutant(m, state_file, acknowledged, request_route=None):
    """Runs one `mutants[]` entry (a real business route, arm/disarm via the
    hot-reload state file). `fixture_only[]` entries go through run_fixture()
    instead -- they have no arm state at all (see defects_config.json).

    `request_route` overrides which route this runner actually CALLS, while
    `m["route"]` stays what the ARMED mutant declares (and what the server's
    middleware matches against). They are the same value for every real
    mutant by construction -- the split only matters for --selftest's planted
    non-firing mutant, which deliberately gives the server a route that never
    matches while the runner keeps calling the real, working endpoint (see
    selftest())."""
    name = m["name"]
    route = request_route or m["route"]

    arm(state_file, None)
    clean_status, clean_body = perform(route)
    if clean_status >= 400:
        return {"name": name, "verdict": "ERROR", "detail": f"clean baseline itself failed: {clean_status} {clean_body}"}

    arm(state_file, name)
    armed_status, armed_body = perform(route)
    arm(state_file, None)

    if not isinstance(armed_body, dict):
        return {"name": name, "verdict": "LOADER-BROKEN",
                "detail": f"armed response was not a JSON object ({armed_status}): {armed_body!r}"}

    fired = patch_applied(armed_body, m["patch"], before=clean_body)
    if not fired:
        return {"name": name, "verdict": "LOADER-BROKEN",
                "detail": "armed response does not reflect the configured patch -- "
                          "the mutant is configured but was NOT served"}

    ok, detail = run_oracle(m["oracle"], armed_body)
    if ok:
        verdict = "SURVIVED" if name not in acknowledged else "SURVIVED-ACKNOWLEDGED"
        return {"name": name, "verdict": verdict, "detail": "oracle accepted the mutated body"}

    # restore-clean: after disarm, the SAME op must validate clean again.
    restore_status, restore_body = perform(route)
    if isinstance(restore_body, dict):
        r_ok, r_detail = run_oracle(m["oracle"], restore_body)
        if not r_ok:
            return {"name": name, "verdict": "RESTORE-FAILED",
                    "detail": f"disarmed but still red: {r_detail}"}

    return {"name": name, "verdict": "KILLED", "detail": detail}


def consulted_keys_now():
    """Behavior keys the server reports as consulted since its arm state last
    changed: the `x-defects-consulted` header (server.py's middleware, defects
    mode on). Read off a discovery GET, which itself consults nothing."""
    req = urllib.request.Request(BASE + "/.well-known/ucp", headers=ucp_headers())
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.headers.get("x-defects-consulted", "")
    return {k for k in raw.split(",") if k}


def _stub_fetch(base):
    return chk_engine.fetch(base, "/testing/defects/behavior-stub", "GET", None,
                            {"Simulation-Secret": SIM_SECRET})


def _stub_predicate(r):
    j = r.json if isinstance(r.json, dict) else {}
    return CLEAN if j.get("stub") == "clean" else DEVIATION


LEAF_VERSION = "2026-04-08"


def _headers_0408():
    h = ucp_headers()
    h["UCP-Agent"] = f'profile="http://localhost:9/.well-known/ucp"; version="{LEAF_VERSION}"'
    return h


def _create_body_0408(consent=None):
    """A 2026-04-08-shaped create: destinations WITHOUT `type` (04-08
    shipping_destination is postal_address + id), optional 04-08 boolean
    consent under buyer.consent."""
    body = {
        "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
        "fulfillment": {"methods": [{
            "id": "method_1", "type": "shipping", "line_item_ids": [],
            "destinations": [{"id": "dest_1", "address_country": "US"}],
            "selected_destination_id": "dest_1",
            "groups": [{"id": "group_1", "line_item_ids": [], "selected_option_id": "std-ship"}],
        }]},
    }
    if consent is not None:
        body["buyer"] = {"consent": consent}
    return body


def _fetch_0408_shape(base):
    return chk_engine.fetch(base, "/checkout-sessions", "POST", _create_body_0408(), _headers_0408())


def _fetch_0408_consent(base):
    return chk_engine.fetch(base, "/checkout-sessions", "POST",
                            _create_body_0408(consent={"marketing": True, "analytics": False}),
                            _headers_0408())


def _pred_0408_shape(r):
    """201; ucp.version is the leaf version; no destinations[].type anywhere;
    and the 04-08 fulfillment oracle (fulfillment.json dev.ucp.shopping.checkout
    at 2026-04-08) accepts the body.

    The `type` absence is judged by the predicate itself, on purpose: verified
    2026-09-10 (lane/w0-d3, D3-03 kill-proof) that the pinned 04-08 oracle
    ACCEPTS a leaked destinations[].type -- 04-08 shipping_destination.json is
    postal_address + id with no additionalProperties bar -- so the oracle call
    here corroborates the rest of the shape but cannot by itself kill
    `projection_leaks_type`. The leaf differential (smoke::test_leaf_differential)
    stays green under that row for the same reason (and because the 04-08 leaf
    advertises no fulfillment capability, so the population's FUL checks are
    not-applicable there)."""
    j = r.json if isinstance(r.json, dict) else {}
    if r.status != 201 or (j.get("ucp") or {}).get("version") != LEAF_VERSION:
        return DEVIATION
    dests = [d for m in (j.get("fulfillment") or {}).get("methods", []) for d in (m.get("destinations") or [])]
    if not dests or any("type" in d for d in dests):
        return DEVIATION
    ok, _ = so.validate_against(j, "schemas/shopping/fulfillment.json", "dev.ucp.shopping.checkout",
                                op="create", version=LEAF_VERSION, direction="response")
    return CLEAN if ok else DEVIATION


def _pred_0408_consent(r):
    """201; every buyer.consent value is a 04-08 boolean (never an 08-25
    consent_purpose object); the 04-08 buyer-consent oracle accepts the body."""
    j = r.json if isinstance(r.json, dict) else {}
    consent = ((j.get("buyer") or {}).get("consent"))
    if r.status != 201 or not isinstance(consent, dict) or not consent:
        return DEVIATION
    if not all(isinstance(v, bool) for v in consent.values()):
        return DEVIATION
    ok, _ = so.validate_against(j, "schemas/shopping/buyer_consent.json", "dev.ucp.shopping.checkout",
                                op="create", version=LEAF_VERSION, direction="response")
    return CLEAN if ok else DEVIATION


# The runner's own checks, resolvable from a behavior row's checks[] like any
# conformance check id. `battery.behavior_stub` is the observable behind the
# golden's test-only /testing/defects/behavior-stub route (its guard is the one
# consultation of key "selftest.stub") -- the --selftest positive control.
# `battery.projection_0408_*` are the observables behind the version-projection
# behavior rows (D3-03): a create negotiated at 2026-04-08 must come back in the
# 04-08 wire shape, judged by the 04-08 oracle plus a direct shape predicate.
BATTERY_CHECKS = [
    chk_engine.Check("battery.behavior_stub", ["BATTERY-STUB"], "MUST",
                     _stub_fetch, _stub_predicate, ['set:stub="violated"']),
    chk_engine.Check("battery.projection_0408_shape", ["FUL-003@2026-04-08"], "MUST",
                     _fetch_0408_shape, _pred_0408_shape,
                     ['set:ucp.version="2026-08-25"',
                      'set:fulfillment.methods.0.destinations.0.type="shipping_address"']),
    chk_engine.Check("battery.projection_0408_consent", ["CNST@2026-04-08"], "MUST",
                     _fetch_0408_consent, _pred_0408_consent,
                     ['set:buyer.consent.marketing={"granted":true,"source":"platform","description":"x"}']),
]


class _GoldenCheckRow:
    """A golden_check_08_25.py Row adapted to this runner's verdict protocol:
    id `gc:<row id>`, verdict = CLEAN when the row's own predicate accepts the
    golden's current response, DEVIATION otherwise. The GC module's HTTP
    helpers are pointed at THIS runner's golden (port/secret) at import."""

    def __init__(self, row):
        self.id, self.row = f"gc:{row.id}", row

    def verdict(self):
        status, body = self.row.make_request()
        ok, _ = self.row.predicate(status, body)
        return CLEAN if ok else DEVIATION


def check_registry():
    """check id -> verdict source, from every conformance/checks/area_*.py CHECKS
    list (engine.Check), BATTERY_CHECKS, and golden_check_08_25.py's rows as
    `gc:<row id>` (D3-04: the golden-reading rows grade behavior rows too).
    Built once per run."""
    import glob
    import importlib
    reg = {c.id: c for c in BATTERY_CHECKS}
    for path in sorted(glob.glob(str(CHECKS_DIR / "area_*.py"))):
        mod = importlib.import_module(pathlib.Path(path).stem)
        for c in getattr(mod, "CHECKS", []):
            reg.setdefault(c.id, c)
    gc = importlib.import_module("golden_check_08_25")
    gc.PORT, gc.BASE, gc.SIM_SECRET = PORT, BASE, SIM_SECRET   # its http() reads these per call
    for row in gc.CHECKS:
        reg.setdefault(f"gc:{row.id}", _GoldenCheckRow(row))
    return reg


def _check_verdict(chk):
    """The check's verdict on the golden's CURRENT response: an engine.Check's
    clean predicate via the same engine.run_check merchant.py uses, or a
    golden_check_08_25.py row's own predicate."""
    if isinstance(chk, _GoldenCheckRow):
        return chk.verdict()
    _, detail = chk_engine.run_check(chk, BASE)
    return detail["clean"]


def run_behavior(b, state_file, acknowledged, registry):
    """Runs one `behavior_mutants[]` row: every named check must be CLEAN
    disarmed, DEVIATION armed, CLEAN again disarmed (FIRED/CAUGHT/RESTORED,
    judged by the checks the row names); and the server must report the row's
    key as consulted while armed, else the row is an UNWIRED key -> LOADER-BROKEN."""
    name, key = b["name"], b["behavior"]
    unknown = [cid for cid in b["checks"] if cid not in registry]
    if unknown:
        return {"name": name, "verdict": "CHECK-UNKNOWN",
                "detail": f"behavior row names checks this runner cannot resolve: {unknown}"}
    checks = [registry[cid] for cid in b["checks"]]

    arm(state_file, None)
    clean = {c.id: _check_verdict(c) for c in checks}
    not_clean = {k: v for k, v in clean.items() if v != CLEAN}
    if not_clean:
        return {"name": name, "verdict": "ERROR",
                "detail": f"clean baseline is not CLEAN for {not_clean}"}

    arm(state_file, name)
    armed = {c.id: _check_verdict(c) for c in checks}
    consulted = consulted_keys_now()
    arm(state_file, None)

    if key not in consulted:
        return {"name": name, "verdict": "LOADER-BROKEN",
                "detail": f"no guard consulted behavior key {key!r} while armed "
                          f"(consulted: {sorted(consulted)}) -- the row is data nobody reads"}
    not_flipped = {k: v for k, v in armed.items() if v != DEVIATION}
    if not_flipped:
        verdict = "SURVIVED" if name not in acknowledged else "SURVIVED-ACKNOWLEDGED"
        return {"name": name, "verdict": verdict,
                "detail": f"armed, but these checks did not deviate: {not_flipped}"}

    restored = {c.id: _check_verdict(c) for c in checks}
    not_restored = {k: v for k, v in restored.items() if v != CLEAN}
    if not_restored:
        return {"name": name, "verdict": "RESTORE-FAILED",
                "detail": f"disarmed but still red: {not_restored}"}
    return {"name": name, "verdict": "KILLED",
            "detail": f"guard consulted {key!r}; {len(checks)} check(s) flipped CLEAN->DEVIATION->CLEAN"}


def run_fixture(f):
    name = f["name"]
    route = f["route"]
    status, body = perform(route)
    if status != 200 or not isinstance(body, dict):
        return {"name": name, "verdict": "LOADER-BROKEN",
                "detail": f"fixture route did not serve the fixture body: {status} {body!r}"}
    if body != f["body"]:
        return {"name": name, "verdict": "LOADER-BROKEN",
                "detail": f"fixture route served a different body than configured: {body!r}"}
    ok, detail = run_oracle(f["oracle"], body)
    if ok:
        return {"name": name, "verdict": "SURVIVED", "detail": "oracle accepted the fixture body"}
    return {"name": name, "verdict": "KILLED", "detail": detail}


def phase0_disabled_mode_proof(db_dir):
    """Boot with NO --defects_config at all (the literal default path -- what
    every real deployment and every OTHER gate/smoke-test in this repo does)
    and confirm: (a) discovery is served normally, (b) the test-only fixture
    route 404s (it cannot possibly leak into a normal boot, since defects mode
    being off means routes/defect_fixtures.py's own `engine.enabled` check
    fails before it ever looks at the requested key).

    This phase deliberately does NOT compare bytes against the later
    enabled-mode boot: golden-0825 mints a fresh ephemeral webhook-signing
    keypair on every boot (config.py's --webhook_signing_key default), so
    discovery's `keys[0]` (and its kid, a hash of the key) genuinely differs
    across ANY two separate boots regardless of this feature -- asserting
    byte-identity across boots would be testing key ephemerality, not defects
    mode. The byte-identity claim that actually matters (does LOADING this
    code change what gets served when nothing is armed) is proved within a
    SINGLE running instance by phase 2's pre/post capture below, and at the
    unit level, exactly, by defects_test.py."""
    g = Golden(db_dir, defects_config=None)
    g.start()
    try:
        status, _ = http("GET", "/.well-known/ucp", expect_json=False)
        fixture_status, _ = http("GET", "/testing/defect-fixtures/location_serves", expect_json=False)
        return status == 200 and fixture_status == 404, status, fixture_status
    finally:
        g.stop()


def main():
    ap = argparse.ArgumentParser(description="R11 golden-0825 mutant battery.")
    ap.add_argument("--selftest", action="store_true",
                     help="kill-test the battery itself: plant a non-firing mutant "
                          "(a defect configured but never served) and assert this "
                          "runner detects it as LOADER-BROKEN, not a silent pass.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args()

    try:
        _require_oracle()
    except OracleUnavailable as e:
        print(f"validate_golden_0825_battery: SKIP -- {e}")
        return 2

    if args.selftest:
        return selftest()

    config = json.loads(pathlib.Path(args.config).read_text())
    acknowledged = set(config.get("acknowledged_gaps", {}).keys())

    with tempfile.TemporaryDirectory(prefix="ucp_golden_0825_battery_") as tmp:
        tmp = pathlib.Path(tmp)
        db_dir0 = tmp / "disabled"
        db_dir1 = tmp / "enabled"
        state_file = tmp / "defects_state.json"

        print("phase 0: disabled-mode boot (no --defects_config at all) ...")
        disabled_ok, disc_status, fixture_status = phase0_disabled_mode_proof(db_dir0)
        print(f"  discovery served: {disc_status == 200}, "
              f"fixture route 404s when disabled: {fixture_status == 404}")

        print("phase 1+2: enabled boot -- unarmed byte-capture, then the battery, "
              "then unarmed byte-capture again ...")
        g = Golden(db_dir1, defects_config=args.config, state_file=state_file)
        g.start()
        try:
            arm(state_file, None)
            _, pre_body = http("GET", "/.well-known/ucp", expect_json=False)

            results = []
            for m in config["mutants"]:
                results.append(run_mutant(m, state_file, acknowledged))
            for f in config.get("fixture_only", []):
                results.append(run_fixture(f))
            registry = check_registry()
            behavior_names = {b["name"] for b in config.get("behavior_mutants", [])}
            for b in config.get("behavior_mutants", []):
                results.append(run_behavior(b, state_file, acknowledged, registry))

            arm(state_file, None)
            _, post_body = http("GET", "/.well-known/ucp", expect_json=False)
        finally:
            g.stop()

        byte_identical = pre_body == post_body
        print(f"  unarmed discovery bytes identical before/after the full battery: {byte_identical}")
        phase01_ok = disabled_ok and byte_identical

    # ---- report ----
    print(f"\n{'mutant':42} verdict")
    ok = phase01_ok
    for r in results:
        print(f"  {r['name']:40} {r['verdict']:20} {r['detail'][:80]}")
        if r["verdict"] not in ("KILLED", "SURVIVED-ACKNOWLEDGED"):
            ok = False
    killed = sum(1 for r in results if r["verdict"] == "KILLED")
    acked = sum(1 for r in results if r["verdict"] == "SURVIVED-ACKNOWLEDGED")
    b_total = sum(1 for r in results if r["name"] in behavior_names)
    b_killed = sum(1 for r in results if r["name"] in behavior_names and r["verdict"] == "KILLED")
    print(f"\n{killed}/{len(results)} mutants killed"
          + (f" ({acked} acknowledged-open)" if acked else "")
          + f" · behavior {b_killed}/{b_total}"
          + f"; disabled-mode byte-identity: {'OK' if phase01_ok else 'FAILED'}")
    print("R11 battery:", "PASS" if ok else "FAIL")

    _write_last_run_report(ok, results, phase01_ok, killed, acked, b_killed, b_total)
    return 0 if ok else 1


def _write_last_run_report(ok, results, phase01_ok, killed, acked, b_killed=0, b_total=0):
    """Standalone gate, report-only line in run_suite.py (this battery boots a
    server twice and takes ~15s -- too heavy to run on every default run_suite
    invocation, matching the schema-census precedent: report-only by default,
    a deliberate flip to a hard default gate is a later, separate step). This
    is the artifact run_suite.py's report line reads; see its docstring there
    for the staleness rule (P-2, self-expiring: an old report is flagged, not
    quietly trusted forever)."""
    out_dir = GOLDEN_DIR / "battery"
    out_dir.mkdir(exist_ok=True)
    report = {
        "ran_at": time.time(),
        "ran_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": ok,
        "killed": killed,
        "acknowledged_open": acked,
        "total": len(results),
        "disabled_mode_byte_identity_ok": phase01_ok,
        "behavior_killed": b_killed,
        "behavior_total": b_total,
        "survivors": [r["name"] for r in results if r["verdict"] == "SURVIVED"],
        "loader_broken": [r["name"] for r in results if r["verdict"] == "LOADER-BROKEN"],
    }
    (out_dir / "LAST_RUN.json").write_text(json.dumps(report, indent=1))


# ---------------------------------------------------------------------------
# --selftest: kill-test the battery itself (R11 build item 5)
# ---------------------------------------------------------------------------


def selftest():
    """Plant a broken defect-loader: a config whose one mutant has a route that
    never matches anything the server serves (so the middleware never fires it),
    same as a real bug would look (a typo'd path, a dropped route registration).
    This runner MUST report it as LOADER-BROKEN, not KILLED and not a silent
    survivor -- proving the fired-check (patch_applied) actually does its job."""
    real = json.loads(DEFAULT_CONFIG.read_text())
    by_name = {m["name"]: m for m in real["mutants"]}

    # The planted defect-loader bug: same patch as the real discovery mutant, but
    # its declared route has a typo (a stand-in for "a route registration got
    # dropped" / "someone renamed a path and forgot to update the mutant"). The
    # server will happily boot; the ARMED middleware will just never match this
    # route to anything real, so the response comes back clean -- exactly what a
    # silently-broken defect loader looks like from the outside.
    broken_mutant = copy.deepcopy(by_name["sdkdrop-jwk-missing-crv"])
    broken_mutant["name"] = "selftest-planted-non-firing"
    broken_mutant["route"] = {"method": "GET", "path": "/.well-known/ucp-DOES-NOT-EXIST"}
    real_discovery_route = {"method": "GET", "path": "/.well-known/ucp"}

    # Positive control in the SAME run: a mutant already proven KILLED in the
    # real battery (sdkdrop-c62-nonzero-scale), run through the identical
    # harness path. Without this, a selftest that always reports LOADER-BROKEN
    # for everything would "pass" for the wrong reason -- it has to also prove
    # it still recognizes a real, correctly-firing, correctly-caught mutant.
    real_mutant = copy.deepcopy(by_name["sdkdrop-c62-nonzero-scale"])

    # Behavior rows (D3-01): a planted UNWIRED row -- its key is consulted by no
    # guard anywhere in the server (the stand-in for "someone added a row and
    # never wrote its guard") -- next to a wired control whose key the test-only
    # stub route consults. Both name the SAME check, so the only difference the
    # runner can see is the server's consulted-keys record: the unwired row must
    # be LOADER-BROKEN even though nothing about its checks is wrong, and the
    # control must be KILLED through the identical harness path.
    unwired_behavior = {
        "name": "selftest-planted-behavior-unwired",
        "behavior": "selftest.never_consulted",
        "checks": ["battery.behavior_stub"],
        "violates": "selftest: a behavior key no guard consults",
    }
    control_behavior = {
        "name": "selftest-behavior-control",
        "behavior": "selftest.stub",
        "checks": ["battery.behavior_stub"],
        "violates": "selftest: the stub route's guard flips its body while armed",
    }

    control_config = {"mutants": [broken_mutant, real_mutant], "fixture_only": [],
                      "behavior_mutants": [unwired_behavior, control_behavior]}

    with tempfile.TemporaryDirectory(prefix="ucp_golden_0825_battery_selftest_") as tmp:
        tmp = pathlib.Path(tmp)
        cfg_path = tmp / "broken_defects_config.json"
        cfg_path.write_text(json.dumps(control_config))
        state_file = tmp / "defects_state.json"
        db_dir = tmp / "db"

        g = Golden(db_dir, defects_config=cfg_path, state_file=state_file)
        g.start()
        try:
            broken_result = run_mutant(broken_mutant, state_file, set(), request_route=real_discovery_route)
            real_result = run_mutant(real_mutant, state_file, set())
            registry = check_registry()
            unwired_result = run_behavior(unwired_behavior, state_file, set(), registry)
            control_result = run_behavior(control_behavior, state_file, set(), registry)
        finally:
            g.stop()

    print(f"planted non-firing mutant  -> {broken_result['verdict']}: {broken_result['detail']}")
    print(f"positive control (real)    -> {real_result['verdict']}: {real_result['detail']}")
    print(f"planted unwired behavior   -> {unwired_result['verdict']}: {unwired_result['detail']}")
    print(f"behavior control (wired)   -> {control_result['verdict']}: {control_result['detail']}")

    ok = (broken_result["verdict"] == "LOADER-BROKEN" and real_result["verdict"] == "KILLED"
          and unwired_result["verdict"] == "LOADER-BROKEN" and control_result["verdict"] == "KILLED")
    print(f"selftest: planted route-typo {broken_result['verdict']}, control {real_result['verdict']}, "
          f"planted behavior {unwired_result['verdict']}, behavior control {control_result['verdict']}"
          f" · {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
