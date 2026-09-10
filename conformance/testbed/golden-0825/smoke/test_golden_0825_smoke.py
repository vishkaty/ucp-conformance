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
"""Smoke suite for golden-0825, OUR OWN v2026-08-25 reference server.

There is no upstream v2026-08-25 reference implementation to test against (verified:
samples has no migration branch, see STATUS.md), so this suite is the proof that our
adaptation of the samples flower-shop server actually serves the released protocol,
not just that it boots.

Asserts, in order:
  1. the server boots and answers health
  2. discovery validates against the RELEASED profile schema (profile.json,
     business_schema) via the official ucp-schema validator -- not a hand-rolled
     check, per the repo's schema-validation methodology (schema_oracle.py)
  3. a full happy-path checkout lifecycle (create -> update -> complete -> order
     GET) plus the cart lifecycle (create -> get -> update -> cancel), with EVERY
     wire body validated against the RELEASED schemas via the same oracle
  4. the "34-null" class (persisted responses serialized without exclude_none,
     see docs/build -- the samples fix this class started with) does NOT
     reproduce on the order GET path
  5. a kill-check: the oracle plumbing actually rejects a payload we know is
     broken, so a green suite here cannot be a vacuously-passing wiring bug

Requires: uv (to run the golden-0825 server), and conformance/.vendor/ucp-2026-08-25
fetched (conformance/ci/fetch_sources.sh) -- the smoke suite validates wire bodies
against that vendored release tree via conformance/selfcheck/schema_oracle.py.

Run:
  cd conformance/testbed/golden-0825/server && uv run --group dev pytest \
      ../smoke/test_golden_0825_smoke.py -v
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

import httpx
import pytest

GOLDEN_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = GOLDEN_DIR.parents[2]  # .../conformance/testbed/golden-0825 -> repo root
sys.path.insert(0, str(REPO_ROOT / "conformance" / "selfcheck"))
import schema_oracle as so  # noqa: E402

SPEC_VERSION = "2026-08-25"
PORT = int(os.environ.get("GOLDEN_0825_TEST_PORT", "8399"))
BASE_URL = f"http://localhost:{PORT}"
DB_DIR = pathlib.Path(f"/tmp/ucp_golden_0825_pytest_{uuid.uuid4().hex[:8]}")


def _require_oracle():
    """Skip (not silently pass) whenever the official validator isn't available,
    so a missing binary/vendor tree shows as SKIPPED, never as a false green."""
    base = so.SCHEMA_BASE.get(SPEC_VERSION)
    if base is None or not base.exists():
        pytest.skip(f"conformance/.vendor/ucp-2026-08-25 not fetched (base={base})")
    if not so.BIN.exists():
        pytest.skip(f"ucp-schema validator not built at {so.BIN}")


@pytest.fixture(scope="module")
def golden_server():
    """Boot golden-0825 via serve_golden_0825.sh (seed, boot, health, pid) and
    tear it down via stop_golden_0825.sh -- the same scripts a human or CI would
    run, so this test proves the SHIPPED harness, not a bespoke test-only path."""
    env = dict(os.environ)
    env["PORT"] = str(PORT)
    env["DB_DIR"] = str(DB_DIR)
    env["SIM_SECRET"] = "smoke-test-secret"
    serve = GOLDEN_DIR / "serve_golden_0825.sh"
    stop = GOLDEN_DIR / "stop_golden_0825.sh"
    result = subprocess.run([str(serve)], env=env, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        pytest.fail(
            f"serve_golden_0825.sh failed (exit {result.returncode}):\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    try:
        yield BASE_URL
    finally:
        subprocess.run([str(stop)], env=env, capture_output=True, text=True, timeout=60)


def ucp_headers(*, idem: str | None = None, req_id: str | None = None) -> dict:
    """Headers every mutating/read UCP request needs on this server: a resolvable
    UCP-Agent (dependencies.py requires the profile= parameter), and unique
    Idempotency-Key/Request-Id/Request-Signature per call."""
    suffix = uuid.uuid4().hex[:8]
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": idem or f"idem-{suffix}",
        "Request-Id": req_id or f"req-{suffix}",
        "Request-Signature": f"sig-{suffix}",
        "UCP-Agent": (
            'profile="http://localhost:9/.well-known/ucp"; version="2026-08-25"'
        ),
    }


def assert_no_nulls(obj, path="$"):
    """Recursively assert no JSON null survived into a served/persisted body --
    the exact class of defect the samples fix (exclude_none from the start)
    targets. A bare `None in str(obj)` check would both over- and under-fire;
    walk the structure instead."""
    if obj is None:
        raise AssertionError(f"null value found at {path} (the 34-null class)")
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert_no_nulls(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_no_nulls(v, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# 1 & 2: boots, discovery validates against the released profile schema
# ---------------------------------------------------------------------------


def test_server_boots_and_discovery_validates(golden_server):
    resp = httpx.get(f"{golden_server}/.well-known/ucp", timeout=5)
    assert resp.status_code == 200, resp.text
    profile = resp.json()

    assert profile["ucp"]["version"] == SPEC_VERSION
    # The dotted capability names must be the RELEASED ones, not guessed --
    # verified against the vendored release's own schema `name` fields
    # (conformance/.vendor/ucp-2026-08-25/source/schemas/shopping/*.json).
    caps = profile["ucp"]["capabilities"]
    for released_name in (
        "dev.ucp.shopping.checkout",
        "dev.ucp.shopping.cart",
        "dev.ucp.shopping.order",
    ):
        assert released_name in caps, f"missing released capability {released_name}"
    # Honest discovery: nothing is advertised that this server doesn't serve.
    # (location/request-constraints are new 08-25 surfaces -- not implemented,
    # not advertised. See STATUS.md.)
    assert "dev.ucp.common.location.search" not in caps
    assert "dev.ucp.common.location.lookup" not in caps

    _require_oracle()
    ok, detail = so.validate_profile(profile, version=SPEC_VERSION, role="business")
    assert ok, f"discovery profile failed official validation: {detail}"


# ---------------------------------------------------------------------------
# 3: full happy path, every wire body validated
# ---------------------------------------------------------------------------


def _fulfillment_block():
    return {
        "methods": [
            {
                "id": "method_1",
                "type": "shipping",
                "line_item_ids": [],
                "destinations": [
                    {"id": "dest_1", "type": "shipping_address", "address_country": "US"}
                ],
                "selected_destination_id": "dest_1",
                "groups": [
                    {"id": "group_1", "line_item_ids": [], "selected_option_id": "std-ship"}
                ],
            }
        ]
    }


def test_checkout_happy_path_create_update_complete_order(golden_server):
    _require_oracle()
    base = golden_server

    # --- create ---
    create_body = {
        "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
        "fulfillment": _fulfillment_block(),
    }
    r = httpx.post(f"{base}/checkout-sessions", headers=ucp_headers(), json=create_body, timeout=5)
    assert r.status_code == 201, r.text
    checkout = r.json()
    checkout_id = checkout["id"]
    ok, detail = so.validate_root(
        checkout, "schemas/shopping/checkout.json", op="create",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"create-checkout response failed official validation: {detail}"

    # --- update ---
    update_body = {
        "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
        "fulfillment": _fulfillment_block(),
    }
    r = httpx.put(
        f"{base}/checkout-sessions/{checkout_id}", headers=ucp_headers(), json=update_body, timeout=5,
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    ok, detail = so.validate_root(
        updated, "schemas/shopping/checkout.json", op="update",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"update-checkout response failed official validation: {detail}"

    # --- complete ---
    complete_body = {
        "payment": {
            "instruments": [
                {
                    "id": "instr_1",
                    "handler_id": "mock_payment_handler",
                    "type": "card",
                    "display": {"brand": "Visa", "last_digits": "1234"},
                    "credential": {"type": "token", "token": "success_token"},
                }
            ]
        },
        "risk_signals": {},
    }
    r = httpx.post(
        f"{base}/checkout-sessions/{checkout_id}/complete",
        headers=ucp_headers(), json=complete_body, timeout=5,
    )
    assert r.status_code == 200, r.text
    completed = r.json()
    assert completed["status"] == "completed"
    ok, detail = so.validate_root(
        completed, "schemas/shopping/checkout.json", op="complete",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"complete-checkout response failed official validation: {detail}"

    # --- order GET ---
    order_id = completed["order"]["id"]
    r = httpx.get(f"{base}/orders/{order_id}", headers=ucp_headers(), timeout=5)
    assert r.status_code == 200, r.text
    order = r.json()
    ok, detail = so.validate_root(
        order, "schemas/shopping/order.json", op="read",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"order GET response failed official validation: {detail}"

    # --- 4: the 34-null class does not reproduce on the persisted order ---
    assert_no_nulls(order)


def test_checkout_cancel_lifecycle(golden_server):
    """A separate checkout so completing one test's checkout doesn't block
    canceling another -- exercises the fifth lifecycle verb (cancel)."""
    _require_oracle()
    base = golden_server
    create_body = {"line_items": [{"item": {"id": "bouquet_tulips"}, "quantity": 1}]}
    r = httpx.post(f"{base}/checkout-sessions", headers=ucp_headers(), json=create_body, timeout=5)
    assert r.status_code == 201, r.text
    checkout_id = r.json()["id"]

    r = httpx.post(f"{base}/checkout-sessions/{checkout_id}/cancel", headers=ucp_headers(), timeout=5)
    assert r.status_code == 200, r.text
    canceled = r.json()
    assert canceled["status"] == "canceled"
    ok, detail = so.validate_root(
        canceled, "schemas/shopping/checkout.json", op="cancel",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"cancel-checkout response failed official validation: {detail}"


def test_cart_lifecycle_create_get_update_cancel(golden_server):
    _require_oracle()
    base = golden_server

    create_body = {"line_items": [{"item": {"id": "pot_ceramic"}, "quantity": 2}]}
    r = httpx.post(f"{base}/carts", headers=ucp_headers(), json=create_body, timeout=5)
    assert r.status_code == 201, r.text
    cart = r.json()
    cart_id = cart["id"]
    ok, detail = so.validate_root(
        cart, "schemas/shopping/cart.json", op="create",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"create-cart response failed official validation: {detail}"

    r = httpx.get(f"{base}/carts/{cart_id}", headers=ucp_headers(), timeout=5)
    assert r.status_code == 200, r.text
    ok, detail = so.validate_root(
        r.json(), "schemas/shopping/cart.json", op="read",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"get-cart response failed official validation: {detail}"

    update_body = {"line_items": [{"item": {"id": "pot_ceramic"}, "quantity": 3}]}
    r = httpx.put(f"{base}/carts/{cart_id}", headers=ucp_headers(), json=update_body, timeout=5)
    assert r.status_code == 200, r.text
    ok, detail = so.validate_root(
        r.json(), "schemas/shopping/cart.json", op="update",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"update-cart response failed official validation: {detail}"

    r = httpx.post(f"{base}/carts/{cart_id}/cancel", headers=ucp_headers(), timeout=5)
    assert r.status_code == 200, r.text
    ok, detail = so.validate_root(
        r.json(), "schemas/shopping/cart.json", op="cancel",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, f"cancel-cart response failed official validation: {detail}"


# ---------------------------------------------------------------------------
# 5: kill-check -- the oracle must actually be capable of failing
# ---------------------------------------------------------------------------


def test_validator_kill_check_rejects_broken_payload(golden_server):
    """Prove the validator plumbing can say NO. Every other test in this suite
    asserts `ok is True`; without this, a wiring bug that made validate_root()
    always return (True, "") would pass every test above vacuously. Take a real,
    already-proven-valid response and delete a required field; the oracle MUST
    reject it."""
    _require_oracle()
    base = golden_server
    create_body = {"line_items": [{"item": {"id": "bouquet_sunflowers"}, "quantity": 1}]}
    r = httpx.post(f"{base}/checkout-sessions", headers=ucp_headers(), json=create_body, timeout=5)
    assert r.status_code == 201, r.text
    valid_checkout = r.json()

    ok, _ = so.validate_root(
        valid_checkout, "schemas/shopping/checkout.json", op="create",
        version=SPEC_VERSION, direction="response",
    )
    assert ok, "precondition failed: the checkout this kill-check corrupts must start valid"

    broken = copy.deepcopy(valid_checkout)
    del broken["id"]  # checkout.json requires `id` in a response
    ok, detail = so.validate_root(
        broken, "schemas/shopping/checkout.json", op="create",
        version=SPEC_VERSION, direction="response",
    )
    assert not ok, "kill-check FAILED: validator accepted a payload missing a required field"
    assert "id" in detail


# ---------------------------------------------------------------------------
# C3 negotiation (D3-02): the served version set is {ucp.version} ∪
# supported_versions; anything else is 422 `version_unsupported` (lowercase).
# ---------------------------------------------------------------------------


def _create_body():
    return {
        "line_items": [{"item": {"id": "bouquet_roses"}, "quantity": 1}],
        "fulfillment": _fulfillment_block(),
    }


def _post_create_with_version(base, version):
    headers = ucp_headers()
    headers["UCP-Agent"] = f'profile="http://localhost:9/.well-known/ucp"; version="{version}"'
    return httpx.post(f"{base}/checkout-sessions", headers=headers, json=_create_body(), timeout=10)


def test_unadvertised_version_rejected(golden_server):
    """An older, never-advertised version (2026-01-23) and a newer one
    (2099-01-01) are both `version_unsupported`; the served version is 201."""
    for version in ("2026-01-23", "2099-01-01"):
        r = _post_create_with_version(golden_server, version)
        assert r.status_code == 422, (version, r.status_code, r.text)
        body = r.json()
        assert body["ucp"]["status"] == "error"
        assert body["messages"][0]["code"] == "version_unsupported", body
    r = _post_create_with_version(golden_server, SPEC_VERSION)
    assert r.status_code == 201, (r.status_code, r.text)


# ---------------------------------------------------------------------------
# C3 supported_versions + the 2026-04-08 leaf profile + version projection
# (D3-03, decision 18): ONE server, two served versions. The leaf is a plain
# 04-08 profile (no supported_versions of its own); a request negotiated at
# 2026-04-08 gets a 04-08-shaped body; and the whole 04-08 conformance
# population, pointed at the leaf, must grade the projection clean (the leaf
# differential -- what makes "projection" honest instead of a second server).
# ---------------------------------------------------------------------------

LEAF_VERSION = "2026-04-08"
LEAF_PATH = f"/.well-known/ucp/{LEAF_VERSION}"
# Discovery alias for runners that derive /.well-known/ucp from a base URL
# (merchant.py): `--server $G/2026-04-08` discovers the SAME leaf document.
LEAF_BASE = f"/{LEAF_VERSION}"
DIFFERENTIAL_CONFIG = REPO_ROOT / "conformance" / "ci" / "differential_flower.config.json"


def test_leaf_profile_is_leaf(golden_server):
    """The root profile publishes supported_versions naming the leaf; the leaf
    is a 04-08 profile validating against the 04-08 profile schema, carries
    NO supported_versions (top level or under ucp), and the alias serves the
    identical document."""
    _require_oracle()
    r = httpx.get(f"{golden_server}{LEAF_PATH}", timeout=10)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    leaf = r.json()

    root = httpx.get(f"{golden_server}/.well-known/ucp", timeout=10).json()
    supported = root["ucp"].get("supported_versions") or {}
    assert LEAF_VERSION in supported, root["ucp"].keys()
    assert supported[LEAF_VERSION].endswith(LEAF_PATH), supported
    # 04-08 documents are BARE (ucp.json@2026-04-08 $defs.base requires a
    # top-level `version`); no `ucp` wrapper, no supported_versions anywhere.
    assert "ucp" not in leaf and leaf["version"] == LEAF_VERSION, list(leaf)
    assert "supported_versions" not in leaf
    for name, entries in leaf["services"].items():
        assert all(e["version"] == LEAF_VERSION for e in entries), (name, entries)  # OVR-075
    for name, entries in leaf["capabilities"].items():
        assert all(e["version"] == LEAF_VERSION for e in entries), (name, entries)
    ok, detail = so.validate_profile(leaf, version=LEAF_VERSION, role="business")
    assert ok, detail

    alias = httpx.get(f"{golden_server}{LEAF_BASE}/.well-known/ucp", timeout=10)
    assert alias.status_code == 200
    assert alias.json() == leaf


def test_0408_request_gets_0408_shape(golden_server):
    """A create negotiated at 2026-04-08 (04-08 request shape: destinations
    without `type`) is 201 with a 04-08-shaped body: ucp.version 2026-04-08,
    no destinations[].type, valid against the 04-08 checkout schema."""
    _require_oracle()
    body = _create_body()
    for m in body["fulfillment"]["methods"]:
        for d in m["destinations"]:
            d.pop("type", None)
    headers = ucp_headers()
    headers["UCP-Agent"] = f'profile="http://localhost:9/.well-known/ucp"; version="{LEAF_VERSION}"'
    r = httpx.post(f"{golden_server}/checkout-sessions", headers=headers, json=body, timeout=10)
    assert r.status_code == 201, (r.status_code, r.text[:300])
    out = r.json()
    assert out["ucp"]["version"] == LEAF_VERSION
    dests = [d for m in (out.get("fulfillment") or {}).get("methods", []) for d in (m.get("destinations") or [])]
    assert dests and all("type" not in d for d in dests), dests
    ok, detail = so.validate_root(out, "schemas/shopping/checkout.json", op="create",
                                  version=LEAF_VERSION, direction="response")
    assert ok, detail

    # The leaf's service endpoint is version-scoped ({{ENDPOINT}}/2026-04-08):
    # a request there with NO version= negotiates at 2026-04-08 (decision 21's
    # fallback applied per endpoint), so a 04-08 platform that omits the
    # parameter -- the 04-08 population does -- is served the 04-08 shape too.
    leaf = httpx.get(f"{golden_server}{LEAF_PATH}", timeout=10).json()
    endpoint = leaf["services"]["dev.ucp.shopping"][0]["endpoint"]
    assert endpoint == f"{golden_server}{LEAF_BASE}", endpoint
    headers = ucp_headers()
    headers["UCP-Agent"] = 'profile="http://localhost:9/.well-known/ucp"'
    r = httpx.post(f"{endpoint}/checkout-sessions", headers=headers, json=body, timeout=10)
    assert r.status_code == 201, (r.status_code, r.text[:300])
    out = r.json()
    assert out["ucp"]["version"] == LEAF_VERSION
    assert all("type" not in d for m in out["fulfillment"]["methods"] for d in m["destinations"])


def test_leaf_differential(golden_server):
    """The 04-08 conformance population (merchant.py) pointed at the leaf must
    grade the projected golden with 0 deviations, and the number of checks it
    actually ran is pinned so a silently-shrinking population cannot hide a
    regression behind '0 deviations'."""
    _require_oracle()
    cmd = [sys.executable, str(REPO_ROOT / "conformance" / "checks" / "merchant.py"),
           "--server", f"{golden_server}{LEAF_BASE}",
           "--config", str(DIFFERENTIAL_CONFIG), "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert proc.stdout.strip(), f"merchant.py produced no report: rc={proc.returncode}\n{proc.stderr[-800:]}"
    report = json.loads(proc.stdout)
    assert report["spec_version"] == LEAF_VERSION, report["spec_version"]
    deviations = [c["id"] for c in report["checks"] if c["status"] == "deviation"]
    assert not deviations, deviations
    ran = [c["id"] for c in report["checks"] if c["status"] == "clean-pass"]
    assert len(ran) == LEAF_DIFFERENTIAL_RUN_COUNT, (len(ran), sorted(ran))


# Pinned from the first green run (2026-09-10, lane/w0-d3, D3-03: 46 clean-pass,
# 0 deviations, 136 not-applicable, 46 not-tested -- the population listing is
# in the W0-d3 landing note). Any change to the 04-08 population, to the leaf's
# advertised capabilities, or to the differential config must re-pin this
# deliberately; a silently shrinking population is a red test, not a pass.
LEAF_DIFFERENTIAL_RUN_COUNT = 46


# ---------------------------------------------------------------------------
# D3-04: the UCP error envelope on EVERY error (unknown routes included), R15's
# request-side 500s become 422 envelopes with a path, C3b's destinations[].type
# default when the platform omits it, and DSC-007/030's rejected-code messages.
# ---------------------------------------------------------------------------


def _assert_error_envelope(body, code):
    assert body["ucp"]["version"] == SPEC_VERSION and body["ucp"]["status"] == "error", body
    msgs = body["messages"]
    assert msgs and msgs[0]["type"] == "error" and msgs[0]["code"] == code, msgs
    ok, detail = so.validate_root(body, "schemas/common/types/error_response.json",
                                  op="read", version=SPEC_VERSION, direction="response")
    assert ok, detail


def test_unknown_route_envelope(golden_server):
    """A request for a route this server does not serve is a UCP error
    envelope (404, code not_found), not a framework `{"detail": ...}` body."""
    _require_oracle()
    r = httpx.get(f"{golden_server}/nope", headers=ucp_headers(), timeout=10)
    assert r.status_code == 404, (r.status_code, r.text)
    _assert_error_envelope(r.json(), "not_found")


def test_bad_consent_key_422(golden_server):
    """A consent purpose key that is not a reverse-DNS name (CNST-004) is a
    422 invalid_request envelope naming the offending path -- R15: the
    inherited server crashed with a bare 500 here."""
    _require_oracle()
    body = _create_body()
    body["buyer"] = {"consent": {"not a reverse dns key": {
        "granted": True, "source": "platform", "description": "x"}}}
    r = httpx.post(f"{golden_server}/checkout-sessions", headers=ucp_headers(), json=body, timeout=10)
    assert r.status_code == 422, (r.status_code, r.text[:300])
    out = r.json()
    _assert_error_envelope(out, "invalid_request")
    assert "consent" in (out["messages"][0].get("path") or ""), out["messages"][0]


def test_omitted_destination_type_defaults(golden_server):
    """C3b: `type` is `ucp_request: optional` on a destination; when the
    platform omits it the business defaults it per method (shipping ->
    shipping_address) and the response carries the discriminator."""
    body = _create_body()
    for m in body["fulfillment"]["methods"]:
        for d in m["destinations"]:
            d.pop("type", None)
    r = httpx.post(f"{golden_server}/checkout-sessions", headers=ucp_headers(), json=body, timeout=10)
    assert r.status_code == 201, (r.status_code, r.text[:300])
    dests = [d for m in r.json()["fulfillment"]["methods"] for d in m["destinations"]]
    assert dests and all(d.get("type") == "shipping_address" for d in dests), dests


def test_rejected_code_surfaces_message(golden_server):
    """DSC-007/030: a discount code the business rejects is still a 201, and
    the rejection is surfaced in messages[] (code discount_code_rejected) with
    a path to the rejected entry -- never silently dropped."""
    body = _create_body()
    body["discounts"] = {"codes": ["INVALID_CODE"]}
    r = httpx.post(f"{golden_server}/checkout-sessions", headers=ucp_headers(), json=body, timeout=10)
    assert r.status_code == 201, (r.status_code, r.text[:300])
    msgs = [m for m in (r.json().get("messages") or []) if m.get("code") == "discount_code_rejected"]
    assert msgs, r.json().get("messages")
    assert "codes" in (msgs[0].get("path") or ""), msgs[0]


# ---------------------------------------------------------------------------
# D3-05: REQUIRE_SIGNATURES=1 boot switch -- the serve script passes
# --require_signatures (and the localhost carve-out) through, echoes it in
# the UP line, and an unsigned request is 401 signature_missing (SIG-031).
# ---------------------------------------------------------------------------

SIGNED_PORT = int(os.environ.get("GOLDEN_0825_SIGNED_TEST_PORT", "8196"))


@pytest.fixture(scope="module")
def signed_golden_server():
    """A SECOND golden, booted with REQUIRE_SIGNATURES=1 on its own port
    (8196: the Wave 0 registry's signed-golden proof port), through the same
    serve/stop scripts."""
    db_dir = pathlib.Path(f"/tmp/ucp_golden_0825_pytest_signed_{uuid.uuid4().hex[:8]}")
    env = dict(os.environ)
    env["PORT"] = str(SIGNED_PORT)
    env["DB_DIR"] = str(db_dir)
    env["SIM_SECRET"] = "smoke-test-secret"
    env["REQUIRE_SIGNATURES"] = "1"
    serve = GOLDEN_DIR / "serve_golden_0825.sh"
    stop = GOLDEN_DIR / "stop_golden_0825.sh"
    result = subprocess.run([str(serve)], env=env, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        pytest.fail(f"serve_golden_0825.sh (REQUIRE_SIGNATURES=1) failed (exit {result.returncode}):\n"
                    f"stdout={result.stdout}\nstderr={result.stderr}")
    try:
        yield f"http://localhost:{SIGNED_PORT}", result.stdout.strip()
    finally:
        subprocess.run([str(stop)], env=env, capture_output=True, text=True, timeout=60)


def test_require_signatures_rejects_unsigned(signed_golden_server):
    """Booted with REQUIRE_SIGNATURES=1 the UP line says so, and an unsigned
    create is 401 signature_missing in the UCP error envelope."""
    base, up_line = signed_golden_server
    headers = ucp_headers()
    headers.pop("Request-Signature", None)
    r = httpx.post(f"{base}/checkout-sessions", headers=headers, json=_create_body(), timeout=10)
    assert r.status_code == 401, (r.status_code, r.text[:300])
    body = r.json()
    assert body["ucp"]["status"] == "error"
    assert body["messages"][0]["code"] == "signature_missing", body
    assert "signatures REQUIRED" in up_line, up_line
