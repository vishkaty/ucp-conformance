#!/usr/bin/env python3
"""
mcp_client.py — the stdlib MCP (JSON-RPC 2.0 over streamable HTTP) client the merchant
harness drives UCP operations with (PLAN-v3 D3-10, §2.15).

One contract, enforced here so every check inherits it: over MCP the UCP payload of a
tools/call is `result.structuredContent` (overview/index.md "Response Format": servers
MUST return it there; `content[]` is compatibility text). `payload_of()` raises
EnvelopeError on a result without it — a client that quietly read `content[0].text`
instead would grade a non-conformant server as fine (validate_mcp_checks.py --selftest
plants exactly that client and proves the harness is not fooled).

    client = McpClient(ctx.mcp_endpoint)
    r = client.call("create_checkout", {"meta": meta(profile), "checkout": {...}})
    r.status      -> HTTP status (OVR-060: JSON-RPC errors carry the REST status)
    r.doc         -> the JSON-RPC document (result or error); decoded from an SSE
                     `message` event when the server answered text/event-stream
    payload_of(r.doc) -> result.structuredContent (EnvelopeError otherwise)

Pure stdlib (urllib); TLS through engine._SSL_CTX like every other harness request.
"""
import json
import uuid

from engine import fetch, Resp


class EnvelopeError(ValueError):
    """A JSON-RPC result that does not carry the UCP payload where the spec puts it."""


def meta(profile, idempotency_key=None, version=None, **extra):
    """`arguments.meta`: ucp-agent.profile (required on every request), optional
    idempotency-key (required for complete/cancel), optional version."""
    agent = {"profile": profile}
    if version:
        agent["version"] = version
    m = {"ucp-agent": agent, **extra}
    if idempotency_key:
        m["idempotency-key"] = idempotency_key
    return m


def decode_sse(body):
    """The JSON-RPC document carried by an SSE body (the data of the first event that
    parses as JSON), or the body itself parsed as JSON. Raises ValueError when neither."""
    text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
    try:
        return json.loads(text)
    except ValueError:
        pass
    for event in text.split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
        if data:
            try:
                return json.loads(data)
            except ValueError:
                continue
    raise ValueError("no JSON-RPC document in the response body")


def payload_of(doc):
    """The UCP payload of a tools/call result: result.structuredContent, and nothing
    else. EnvelopeError when `doc` is not a result, or the result lacks it."""
    if not isinstance(doc, dict) or "error" in doc or not isinstance(doc.get("result"), dict):
        raise EnvelopeError("not a JSON-RPC result document")
    sc = doc["result"].get("structuredContent")
    if not isinstance(sc, dict):
        raise EnvelopeError("result carries no structuredContent (the UCP payload MUST be there)")
    return sc


class McpResponse:
    """status = HTTP status; headers = response headers; doc = the JSON-RPC document
    (None when the body is not one); raw = the body bytes."""

    def __init__(self, status, headers, raw):
        self.status, self.headers, self.raw = status, dict(headers), raw
        try:
            self.doc = decode_sse(raw)
        except ValueError:
            self.doc = None

    @property
    def error(self):
        """The JSON-RPC error object (int code) or None."""
        d = self.doc
        if not isinstance(d, dict) or "result" in d:
            return None
        err = d.get("error")
        return err if isinstance(err, dict) and isinstance(err.get("code"), int) else None

    @property
    def result(self):
        d = self.doc
        if self.status != 200 or not isinstance(d, dict) or d.get("jsonrpc") != "2.0" or "error" in d:
            return None
        return d.get("result") if isinstance(d.get("result"), dict) else None

    def payload(self):
        return payload_of(self.doc)

    def as_resp(self):
        """An engine.Resp over the JSON-RPC document — what a predicate + the engine's
        response mutations (drop:/set:/status:) operate on."""
        return Resp(self.status, self.headers, json.dumps(self.doc).encode() if self.doc is not None else self.raw)


class McpClient:
    def __init__(self, endpoint, sse=False):
        self.endpoint = endpoint
        self.sse = sse
        self._next_id = 0

    def _rpc(self, method, params=None, headers=None):
        self._next_id += 1
        doc = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            doc["params"] = params
        h = {"Content-Type": "application/json", "request-id": str(uuid.uuid4()),
             **({"Accept": "text/event-stream"} if self.sse else {}), **(headers or {})}
        r = fetch(self.endpoint, "", "POST", doc, h)
        return McpResponse(r.status, r.headers, r.body)

    def initialize(self):
        return self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "spck-conformance", "version": "0"}})

    def tools_list(self):
        return self._rpc("tools/list")

    def call(self, name, arguments, headers=None):
        """tools/call. `headers` are HTTP-level (e.g. a UCP-Agent header carrying
        version=, the header the spec's signed MCP example sends)."""
        return self._rpc("tools/call", {"name": name, "arguments": arguments}, headers=headers)

    @staticmethod
    def payload_of(doc):
        return payload_of(doc)
