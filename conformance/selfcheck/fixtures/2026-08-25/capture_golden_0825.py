#!/usr/bin/env python3
"""capture_golden_0825.py — capture REAL golden-0825 responses IN-PROCESS (no TCP port) as the
2026-08-25 dual-oracle corpus fixtures (D4-01 / B5a).

Drives conformance/testbed/golden-0825/server through FastAPI's TestClient using the server's
own integration-test harness (temporary sqlite DBs, seeded rose/tulip inventory, the same
headers its tests send), so every fixture here is a response the pinned golden actually
produced — not a hand-written shape. Re-run deliberately when the golden changes:

    cd conformance/testbed/golden-0825/server && uv sync --frozen --group dev
    .venv/bin/python ../../../selfcheck/fixtures/2026-08-25/capture_golden_0825.py

Writes (next to this script): discovery_profile.json, checkout_create_response.json,
checkout_response.valid.json (the COMPLETED checkout), order_response.valid.json, and
manifest.json (what each file is, how it is validated, the golden tree SHA + date).
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SERVER = ROOT / "conformance" / "testbed" / "golden-0825" / "server"
# the server's tests mix `import db` (server dir) with `from server.server import app`
# (its parent as a package) — ledger R10(e); put both on the path exactly as its CI does.
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(SERVER.parent))   # parent FIRST: `server` must be the package
os.chdir(SERVER)

import integration_test as it  # noqa: E402  (the server's own harness)


def main():
    t = it.IntegrationTest("test_single_item_checkout")
    t.setUp()
    try:
        c = t.client
        out = {}
        r = c.get("/.well-known/ucp")
        assert r.status_code == 200, r.text
        out["discovery_profile.json"] = r.json()

        # Plain-dict bodies (the harness's own _create_checkout_payload builds a bare
        # ShippingDestination without the `type` the pinned SDK requires — ledger G10/S8d);
        # these are the bodies the golden's smoke test sends and the golden accepts.
        fulfillment = {"methods": [{
            "id": "method_1", "type": "shipping", "line_item_ids": [],
            "destinations": [{"id": "dest_1", "type": "shipping_address", "address_country": "US"}],
            "selected_destination_id": "dest_1",
            "groups": [{"id": "group_1", "line_item_ids": [], "selected_option_id": "std-ship"}],
        }]}
        body = {"line_items": [{"item": {"id": "rose"}, "quantity": 1}], "fulfillment": fulfillment}
        r = c.post("/checkout-sessions", headers=t._get_headers(), json=body)
        assert r.status_code == 201, r.text
        out["checkout_create_response.json"] = r.json()
        cid = r.json()["id"]

        complete = {"payment": {"instruments": [{
            "id": "instr_1", "handler_id": "mock_payment_handler", "type": "card",
            "display": {"brand": "Visa", "last_digits": "1234"},
            "credential": {"type": "token", "token": "success_token"}}]}, "risk_signals": {}}
        r = c.post(f"/checkout-sessions/{cid}/complete", headers=t._get_headers(), json=complete)
        assert r.status_code == 200, r.text
        out["checkout_response.valid.json"] = r.json()
        oid = t.get_resource_id((r.json().get("order") or {}).get("id"))
        if oid:
            r = c.get(f"/orders/{oid}", headers=t._get_headers())
            assert r.status_code == 200, r.text
            out["order_response.valid.json"] = r.json()
    finally:
        t.tearDown()

    for name, obj in out.items():
        (HERE / name).write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    manifest = {
        "_about": "2026-08-25 dual-oracle corpus: responses captured IN-PROCESS from the pinned "
                  "golden-0825 (conformance/testbed/golden-0825/server via its own TestClient "
                  "harness) by capture_golden_0825.py. Each item is validated by BOTH oracles "
                  "(Rust ucp-schema + the independent referee) in validate_dual_oracle.py "
                  "--version 2026-08-25; `expect` is what the golden's response should be under "
                  "the pinned schemas, `mode` how it is dispatched (root = --schema without --def; "
                  "profile = discovery profile).",
        "captured": datetime.date.today().isoformat(),
        "golden_tree": sha,
        "items": [
            {"file": "checkout_create_response.json", "schema_rel": "schemas/shopping/checkout.json",
             "mode": "root", "op": "create", "direction": "response", "expect": "valid",
             "note": "201 body of POST /checkout-sessions (one rose, shipping method with destination/group)"},
            {"file": "checkout_response.valid.json", "schema_rel": "schemas/shopping/checkout.json",
             "mode": "root", "op": "complete", "direction": "response", "expect": "valid",
             "note": "200 body of POST /checkout-sessions/{id}/complete with a card token instrument; also the base for the #43/#45 boundary fixtures"},
            {"file": "order_response.valid.json", "schema_rel": "schemas/shopping/order.json",
             "mode": "root", "op": "read", "direction": "response", "expect": "valid",
             "note": "200 body of GET /orders/{id} after completion"},
        ],
        "reference_only": [{"file": "discovery_profile.json", "note": "GET /.well-known/ucp as captured; validated by the Rust oracle's profile mode elsewhere (smoke test) — the referee has no profile mode, so it is not a dual-oracle item"}],
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print("captured:", ", ".join(out), "@ golden tree", sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
