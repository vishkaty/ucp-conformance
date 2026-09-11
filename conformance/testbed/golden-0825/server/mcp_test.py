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

"""mcp_test.py -- the MCP `tools/call` bridge (D3-09, PLAN-v3 SS2.15).

In-process (TestClient) tests of routes/mcp.py + routes/mcp_bridge.py: a
JSON-RPC `tools/call` is dispatched to the SAME REST handlers (validation,
negotiation, idempotency, error envelope), the UCP payload comes back in
`result.structuredContent` (+ `content[]`), business outcomes are `result`,
protocol errors are JSON-RPC errors under the corresponding HTTP status
(OVR-060), and `meta` maps onto the REST headers BEFORE signature
verification (the named ordering test), with Content-Digest bound to the
JSON-RPC body bytes and the idempotency key round-tripping through `meta`.

Run:
  cd conformance/testbed/golden-0825/server && uv run --group dev pytest mcp_test.py -v
"""

import http.server
import json
import threading
import uuid

from absl.testing import absltest
from cryptography.hazmat.primitives.asymmetric import ec
import config
import integration_test
import ucp_signing

# A freshly created checkout is in one of the two pre-completion states.
OPEN_STATES = {"incomplete", "ready_for_complete"}


class _ProfileHandler(http.server.BaseHTTPRequestHandler):
  """Serves the platform profile (keys[]) the signed tests point UCP-Agent at."""

  routes: dict[str, tuple[int, bytes]] = {}

  def do_GET(self) -> None:  # noqa: N802 (http.server API)
    """Serve a canned route."""
    status, body = self.routes.get(self.path, (404, b"{}"))
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, *args) -> None:
    """Silence request logging."""
    del args


class _McpBase(integration_test.IntegrationTest):
  """IntegrationTest scaffolding (temp DBs, seeded rose x5 / tulip x2, in-process
  client) minus its inherited lifecycle tests, plus a loopback profile server."""

  for _inherited in [n for n in dir(integration_test.IntegrationTest) if n.startswith("test_")]:
    locals()[_inherited] = None
  del _inherited

  require_signatures = False

  def setUp(self) -> None:
    """Start the profile server; set the signature flags for this class."""
    super().setUp()
    ucp_signing.clear_key_cache()
    config.FLAGS.require_signatures = self.require_signatures
    config.FLAGS.allow_insecure_profile_urls = True
    self.agent_key = ec.generate_private_key(ec.SECP256R1())
    self.agent_kid = "mcp-test-agent-key"
    jwk = ucp_signing.jwk_from_public_key(self.agent_key.public_key(), self.agent_kid)
    version = config.get_server_version()
    _ProfileHandler.routes = {
      "/profile.json": (200, json.dumps({"ucp": {"version": version}, "keys": [jwk]}).encode()),
    }
    self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProfileHandler)
    self.profile_url = f"http://127.0.0.1:{self.server.server_address[1]}/profile.json"
    self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    self.thread.start()

  def tearDown(self) -> None:
    """Stop the profile server; reset the signature flags."""
    self.server.shutdown()
    self.server.server_close()
    config.FLAGS.require_signatures = False
    config.FLAGS.allow_insecure_profile_urls = False
    super().tearDown()

  # ---- helpers -------------------------------------------------------------

  def _meta(self, **extra) -> dict:
    return {"ucp-agent": {"profile": self.profile_url}, **extra}

  def _create_args(self, quantity: int = 1, item: str = "rose", **meta_extra) -> dict:
    return {
      "meta": self._meta(**meta_extra),
      "checkout": {"line_items": [{"item": {"id": item}, "quantity": quantity}]},
    }

  @staticmethod
  def _rpc_body(name: str, arguments: dict, rpc_id=1) -> bytes:
    return json.dumps({
      "jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
      "params": {"name": name, "arguments": arguments},
    }).encode()

  def _call(self, name: str, arguments: dict, rpc_id=1, headers: dict | None = None):
    """Unsigned tools/call over the in-process client."""
    body = self._rpc_body(name, arguments, rpc_id)
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    with self.client:
      return self.client.post("/mcp", content=body, headers=hdrs)

  def _signed_call(self, body: bytes, *, tamper_body: bytes | None = None,
                   extra_headers: dict | None = None):
    """Sign `body` at the HTTP layer with NO UCP-Agent header on the wire (the
    profile travels only in meta) and POST `tamper_body or body`."""
    headers = {"Idempotency-Key": str(uuid.uuid4()), "Request-Id": str(uuid.uuid4()),
               **(extra_headers or {})}
    additions = ucp_signing.sign_request(
      self.agent_key, self.agent_kid, "POST", "http://testserver/mcp", headers, body)
    headers.update(additions)
    with self.client:
      return self.client.post("/mcp", content=tamper_body or body, headers=headers)

  def _assert_error(self, response, code: int, ucp_code: str | None = None,
                    http_status: int | None = None):
    j = response.json()
    self.assertIn("error", j, j)
    self.assertNotIn("result", j)
    self.assertEqual(j["error"]["code"], code, j)
    if ucp_code is not None:
      data = j["error"].get("data") or {}
      codes = [m.get("code") for m in (data.get("messages") or [])]
      self.assertIn(ucp_code, codes, j)
    if http_status is not None:
      self.assertEqual(response.status_code, http_status, j)
    return j


class ToolsCallBridgeTest(_McpBase):
  """tools/call bridges to the REST handlers (permissive signature mode)."""

  def test_tools_call_create_checkout(self) -> None:
    """A create over MCP is a `result` whose structuredContent IS the checkout,
    content[0].text is the same JSON, and the envelope has the tool_call shape."""
    r = self._call("create_checkout", self._create_args())
    self.assertEqual(r.status_code, 200, r.text)
    j = r.json()
    self.assertEqual(j.get("jsonrpc"), "2.0")
    self.assertEqual(j.get("id"), 1)
    self.assertNotIn("error", j, j)
    sc = j["result"]["structuredContent"]
    self.assertTrue(sc.get("id"), sc)
    # the in-process seed needs no fulfillment for "rose", so the fresh
    # session is already ready_for_complete; the live golden's flower seed
    # answers incomplete (the D3-09 acceptance curl). Both are open states.
    self.assertIn(sc.get("status"), OPEN_STATES, json.dumps(sc))
    self.assertEqual(sc["ucp"]["version"], config.get_server_version())
    content = j["result"]["content"]
    self.assertEqual(content[0]["type"], "text")
    self.assertEqual(json.loads(content[0]["text"]), sc)
    # the REST GET sees the same session: the bridge went through the real handler
    with self.client:
      got = self.client.get(f"/checkout-sessions/{sc['id']}", headers=self._get_headers())
    self.assertEqual(got.status_code, 200, got.text)
    self.assertEqual(got.json()["id"], sc["id"])

  def test_missing_meta_is_invalid_params(self) -> None:
    """MCP-010: meta (ucp-agent.profile) is REQUIRED -> -32602, HTTP 400."""
    r = self._call("create_checkout", {"checkout": {"line_items": [{"item": {"id": "rose"}, "quantity": 1}]}})
    self._assert_error(r, -32602, http_status=400)
    r = self._call("create_checkout", {"meta": {}, "checkout": {"line_items": []}})
    self._assert_error(r, -32602, http_status=400)

  def test_id_in_payload_is_invalid_params(self) -> None:
    """MCP-002: the checkout object of a create MUST NOT carry `id` -> -32602."""
    args = self._create_args()
    args["checkout"]["id"] = "chk_client_chosen"
    self._assert_error(self._call("create_checkout", args), -32602, http_status=400)

  def test_version_unsupported_is_negotiation_error(self) -> None:
    """NEG MCP column: an unadvertised version -> -32001 carrying the UCP
    envelope, under the REST status (422, OVR-060)."""
    r = self._call("create_checkout", self._create_args())
    self.assertEqual(r.status_code, 200, r.text)
    args = self._create_args()
    args["meta"]["ucp-agent"]["version"] = "2099-01-01"
    self._assert_error(self._call("create_checkout", args), -32001, "version_unsupported", 422)

  def test_business_outcome_is_result(self) -> None:
    """Out of stock is a business outcome: JSON-RPC `result` whose
    structuredContent is the UCP error envelope (ucp.status == error)."""
    r = self._call("create_checkout", self._create_args(quantity=99, item="tulip"))
    self.assertEqual(r.status_code, 200, r.text)
    j = r.json()
    self.assertNotIn("error", j, j)
    sc = j["result"]["structuredContent"]
    self.assertEqual(sc["ucp"]["status"], "error", sc)
    self.assertEqual(sc["messages"][0]["code"], "out_of_stock", sc)

  def test_unknown_tool_is_invalid_params(self) -> None:
    self._assert_error(self._call("no_such_tool", self._create_args()), -32602)

  def test_idempotency_key_round_trip(self) -> None:
    """meta["idempotency-key"] -> Idempotency-Key: same key + same args replays
    the cached checkout (same id); same key + different args -> -32000
    idempotency_conflict under HTTP 409 (signatures.md idempotency table)."""
    key = str(uuid.uuid4())
    first = self._call("create_checkout", self._create_args(**{"idempotency-key": key}))
    second = self._call("create_checkout", self._create_args(**{"idempotency-key": key}))
    self.assertEqual(first.status_code, 200, first.text)
    self.assertEqual(second.status_code, 200, second.text)
    self.assertEqual(first.json()["result"]["structuredContent"]["id"],
                     second.json()["result"]["structuredContent"]["id"])
    conflict = self._call("create_checkout",
                          self._create_args(quantity=2, **{"idempotency-key": key}))
    self._assert_error(conflict, -32000, "idempotency_conflict", 409)

  def test_tools_list_carries_core_checkout_tools(self) -> None:
    """MCP-003 item 2: every core checkout tool is listed (cancel included)."""
    with self.client:
      r = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    for tool in ("create_checkout", "get_checkout", "update_checkout",
                 "complete_checkout", "cancel_checkout"):
      self.assertIn(tool, names)

  def test_sse_echo(self) -> None:
    """Streamable HTTP: an Accept: text/event-stream client gets the SAME
    JSON-RPC response as one SSE `message` event."""
    r = self._call("create_checkout", self._create_args(), headers={"Accept": "text/event-stream"})
    self.assertEqual(r.status_code, 200, r.text)
    self.assertTrue(r.headers["content-type"].startswith("text/event-stream"), r.headers)
    events = [e for e in r.text.split("\n\n") if e.strip()]
    self.assertEqual(len(events), 1, r.text)
    data = "".join(line[5:].lstrip() for line in events[0].splitlines() if line.startswith("data:"))
    j = json.loads(data)
    self.assertIn(j["result"]["structuredContent"]["status"], OPEN_STATES, data)

  def test_transport_advertised(self) -> None:
    """The profile advertises the mcp transport at <endpoint>/mcp."""
    with self.client:
      r = self.client.get("/.well-known/ucp")
    svc = r.json()["ucp"]["services"]["dev.ucp.shopping"]
    mcp = [s for s in svc if s.get("transport") == "mcp"]
    self.assertEqual(len(mcp), 1, svc)
    self.assertTrue(mcp[0]["endpoint"].endswith("/mcp"), mcp)
    self.assertEqual(mcp[0]["version"], config.get_server_version())


class SignedBridgeTest(_McpBase):
  """REQUIRE_SIGNATURES: the ordering and the signed bytes."""

  require_signatures = True

  def test_meta_headers_precede_signature_verify(self) -> None:
    """meta["ucp-agent"].profile is mapped onto UCP-Agent BEFORE verify_signature
    runs: a signed call with the profile ONLY in meta resolves its keys from
    that profile and succeeds; an unsigned call is -32000 signature_missing
    under HTTP 401."""
    body = self._rpc_body("create_checkout", self._create_args())
    r = self._signed_call(body)
    self.assertEqual(r.status_code, 200, r.text)
    self.assertIn(r.json()["result"]["structuredContent"]["status"], OPEN_STATES, r.text)
    unsigned = self._call("create_checkout", self._create_args())
    self._assert_error(unsigned, -32000, "signature_missing", 401)

  def test_signed_bytes_over_mcp(self) -> None:
    """Content-Digest is over the JSON-RPC body bytes: replaying the signed
    headers with a body tampered by one byte is -32000 digest_mismatch (400)."""
    body = self._rpc_body("create_checkout", self._create_args())
    tampered = body.replace(b'"quantity": 1', b'"quantity": 2')
    self.assertNotEqual(body, tampered)
    r = self._signed_call(body, tamper_body=tampered)
    self._assert_error(r, -32000, "digest_mismatch", 400)


if __name__ == "__main__":
  absltest.main()
