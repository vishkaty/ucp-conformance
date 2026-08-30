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
